#!/usr/bin/env python3
"""DSL Combined Runner v2.0 — Multi-strategy support.

Iterates ALL enabled strategies, processes ALL active DSL state files per strategy.
Each position uses its own strategy's wallet for close operations.

v2.0 changes (WOLF v6):
  - Multi-strategy: iterates load_all_strategies(), uses per-strategy state dirs
  - Each result includes strategyKey for routing
  - Two HYPE positions in different strategies processed independently
  - Uses wolf_config for paths and config loading

v1.0 (WOLF v5):
  - Single cron manages ALL active WOLF positions
  - Batch pricing, per-position DSL v4.1 logic

Usage:
  PYTHONUNBUFFERED=1 python3 dsl-combined.py

Output: JSON with per-position results + summary.
"""
import json, subprocess, os, sys, glob, time
from datetime import datetime, timezone

# Add scripts dir to path for wolf_config import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import load_all_strategies, dsl_state_glob, atomic_write

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_all_mids():
    """Fetch all mid prices in one call (crypto only). Returns dict of asset->price."""
    try:
        r = subprocess.run(
            ["curl", "-s", "https://api.hyperliquid.xyz/info",
             "-H", "Content-Type: application/json",
             "-d", '{"type":"allMids"}'],
            capture_output=True, text=True, timeout=15
        )
        return json.loads(r.stdout)
    except Exception:
        return {}


def fetch_xyz_positions(wallet):
    """Fetch XYZ clearinghouse state for a wallet. Returns dict of coin->price."""
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi.strategy_get_clearinghouse_state",
             f"strategy_wallet={wallet}", "dex=xyz"],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(r.stdout)
        positions = {}
        for pos in data.get("data", {}).get("xyz", {}).get("assetPositions", []):
            coin = pos["position"]["coin"]
            pval = float(pos["position"]["positionValue"])
            sz = abs(float(pos["position"]["szi"]))
            if sz > 0:
                positions[coin] = pval / sz
        return positions
    except Exception:
        return {}


def close_position(wallet, coin, reason):
    """Close a position via mcporter with retry."""
    for attempt in range(2):
        try:
            cr = subprocess.run(
                ["mcporter", "call", "senpi", "close_position", "--args",
                 json.dumps({
                     "strategyWalletAddress": wallet,
                     "coin": coin,
                     "reason": reason
                 })],
                capture_output=True, text=True, timeout=30
            )
            result_text = cr.stdout.strip()
            no_position = "CLOSE_NO_POSITION" in result_text
            if cr.returncode == 0 and ("error" not in result_text.lower() or no_position):
                return True, result_text if not no_position else "position_already_closed"
            else:
                last_error = f"api_error_attempt_{attempt+1}: {result_text}"
        except Exception as e:
            last_error = f"error_attempt_{attempt+1}: {str(e)}"
        if attempt < 1:
            time.sleep(3)
    return False, last_error


def process_position(state_file, state, price, strategy_cfg):
    """Run DSL v4.1 logic on a single position. Returns result dict."""
    direction = state.get("direction", "LONG").upper()
    is_long = direction == "LONG"
    is_xyz = state.get("dex") == "xyz" or state.get("asset", "").startswith("xyz:")
    breach_decay_mode = state.get("breachDecay", "hard")
    max_fetch_failures = state.get("maxFetchFailures", 10)

    entry = state["entryPrice"]
    size = state["size"]
    leverage = state["leverage"]
    hw = state["highWaterPrice"]
    phase = state["phase"]
    breach_count = state["currentBreachCount"]
    tier_idx = state["currentTierIndex"]
    tier_floor = state["tierFloorPrice"]
    tiers = state["tiers"]
    force_close = state.get("pendingClose", False)

    # --- Stagnation config ---
    stag_cfg = state.get("stagnation", {})
    stag_enabled = stag_cfg.get("enabled", True)
    stag_min_roe = stag_cfg.get("minROE", 8.0)
    stag_stale_hours = stag_cfg.get("thresholdHours", 1.0)
    stag_range_pct = stag_cfg.get("priceRangePct", 1.0)

    # --- DSL config from registry (with backward-compatible defaults) ---
    dsl_cfg = strategy_cfg.get("dsl", {})

    # --- Auto-fix absoluteFloor ---
    retrace_roe = abs(state["phase1"]["retraceThreshold"])
    retrace_decimal = retrace_roe / 100 if retrace_roe > 1 else retrace_roe
    retrace_price = retrace_decimal / leverage
    if is_long:
        correct_floor = round(entry * (1 - retrace_price), 6)
    else:
        correct_floor = round(entry * (1 + retrace_price), 6)
    existing_floor = state["phase1"].get("absoluteFloor", correct_floor)
    if is_long:
        final_floor = min(correct_floor, existing_floor) if existing_floor > 0 else correct_floor
    else:
        final_floor = max(correct_floor, existing_floor) if existing_floor > 0 else correct_floor
    state["phase1"]["absoluteFloor"] = final_floor
    state["floorPrice"] = final_floor

    # --- uPnL ---
    if is_long:
        upnl = (price - entry) * size
    else:
        upnl = (entry - price) * size
    margin = entry * size / leverage
    upnl_pct = upnl / margin * 100

    # --- Update high water ---
    hw_updated = False
    if is_long and price > hw:
        hw = price
        state["highWaterPrice"] = hw
        hw_updated = True
    elif not is_long and price < hw:
        hw = price
        state["highWaterPrice"] = hw
        hw_updated = True

    if hw_updated or "hwTimestamp" not in state:
        state["hwTimestamp"] = now

    # --- Tier upgrades ---
    previous_tier_idx = tier_idx
    tier_changed = False
    for i, tier in enumerate(tiers):
        if tier_idx is not None and i <= tier_idx:
            continue
        if upnl_pct >= tier["triggerPct"]:
            tier_idx = i
            tier_changed = True
            if is_long:
                tier_floor = round(entry + (hw - entry) * tier["lockPct"] / 100, 4)
            else:
                tier_floor = round(entry - (entry - hw) * tier["lockPct"] / 100, 4)
            state["currentTierIndex"] = tier_idx
            state["tierFloorPrice"] = tier_floor
            if phase == 1:
                phase2_trigger = state.get("phase2TriggerTier", 0)
                if tier_idx >= phase2_trigger:
                    phase = 2
                    state["phase"] = 2
                    breach_count = 0
                    state["currentBreachCount"] = 0

    # --- Effective floor ---
    if phase == 1:
        p1_retrace = abs(state["phase1"]["retraceThreshold"])
        p1_retrace_price = (p1_retrace / 100 if p1_retrace > 1 else p1_retrace) / leverage
        breaches_needed = state["phase1"]["consecutiveBreachesRequired"]
        abs_floor = state["phase1"]["absoluteFloor"]
        if is_long:
            trailing_floor = round(hw * (1 - p1_retrace_price), 4)
            effective_floor = max(abs_floor, trailing_floor)
        else:
            trailing_floor = round(hw * (1 + p1_retrace_price), 4)
            effective_floor = min(abs_floor, trailing_floor)
    else:
        p2_retrace_pct = state.get("phase2", {}).get("retraceFromHW", 5)
        p2_retrace = p2_retrace_pct / 100
        if tier_idx is not None and tier_idx >= 0:
            t_retrace = tiers[tier_idx].get("retrace", p2_retrace)
        else:
            t_retrace = p2_retrace
        t_retrace_price = t_retrace / leverage
        breaches_needed = (tiers[tier_idx].get("breachesRequired", tiers[tier_idx].get("retraceClose", 2))
                           if tier_idx is not None and tier_idx >= 0
                           else state.get("phase2", {}).get("consecutiveBreachesRequired", 2))
        if is_long:
            trailing_floor = round(hw * (1 - t_retrace_price), 4)
            effective_floor = max(tier_floor or 0, trailing_floor)
        else:
            trailing_floor = round(hw * (1 + t_retrace_price), 4)
            effective_floor = min(tier_floor or float('inf'), trailing_floor)

    state["floorPrice"] = round(effective_floor, 4)

    # --- Stagnation check ---
    stagnation_triggered = False
    stag_hours_stale = 0.0
    if stag_enabled and upnl_pct >= stag_min_roe and state.get("hwTimestamp"):
        try:
            hw_time = datetime.fromisoformat(state["hwTimestamp"].replace("Z", "+00:00"))
            stag_hours_stale = (datetime.now(timezone.utc) - hw_time).total_seconds() / 3600
            if stag_hours_stale >= stag_stale_hours:
                hw_price = state["highWaterPrice"]
                if hw_price > 0:
                    price_move_pct = abs(price - hw_price) / hw_price * 100
                    if price_move_pct <= stag_range_pct:
                        stagnation_triggered = True
        except (ValueError, TypeError):
            pass

    # --- Phase 1 auto-cut (configurable via registry dsl key) ---
    phase1_max_minutes    = dsl_cfg.get("phase1MaxMinutes", 90)
    weak_peak_cut_minutes = dsl_cfg.get("weakPeakCutMinutes", 45)
    weak_peak_threshold   = dsl_cfg.get("weakPeakThreshold", 3.0)

    phase1_autocut = False
    phase1_autocut_reason = None
    elapsed_minutes = 0
    if state.get("createdAt"):
        try:
            created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
            elapsed_minutes = (datetime.now(timezone.utc) - created).total_seconds() / 60
        except (ValueError, TypeError):
            pass

    if phase == 1 and elapsed_minutes > 0:
        # Peak ROE tracking
        peak_roe = state.get("peakROE", 0)
        if upnl_pct > peak_roe:
            peak_roe = upnl_pct
            state["peakROE"] = peak_roe

        # Hard cap
        if elapsed_minutes >= phase1_max_minutes:
            phase1_autocut = True
            tier1_pct = tiers[0]["triggerPct"] if tiers else 5
            phase1_autocut_reason = f"Phase 1 timeout: {round(elapsed_minutes)}min, ROE never hit Tier 1 ({tier1_pct}%)"
        # Weak peak early cut
        elif elapsed_minutes >= weak_peak_cut_minutes and peak_roe < weak_peak_threshold and upnl_pct < peak_roe:
            phase1_autocut = True
            phase1_autocut_reason = f"Weak peak early cut: {round(elapsed_minutes)}min, peak ROE {round(peak_roe,1)}%, now declining"

    # --- Breach check ---
    if is_long:
        breached = price <= effective_floor
    else:
        breached = price >= effective_floor

    if breached:
        breach_count += 1
    else:
        if breach_decay_mode == "soft":
            breach_count = max(0, breach_count - 1)
        else:
            breach_count = 0
    state["currentBreachCount"] = breach_count

    should_close = (breach_count >= breaches_needed or force_close
                    or stagnation_triggered or phase1_autocut)

    # --- Close if needed ---
    closed = False
    close_result = None
    close_reason = None

    if should_close:
        wallet = state.get("wallet", strategy_cfg.get("wallet", ""))
        asset = state["asset"]
        close_coin = asset if asset.startswith("xyz:") else f"xyz:{asset}" if is_xyz else asset

        if stagnation_triggered:
            close_reason = f"Stagnation TP: ROE {round(upnl_pct,1)}%, stale {round(stag_hours_stale,1)}h"
        elif phase1_autocut:
            close_reason = phase1_autocut_reason
        else:
            close_reason = f"DSL breach: Phase {phase}, {breach_count}/{breaches_needed}, price {price}, floor {effective_floor}"

        if wallet:
            closed, close_result = close_position(wallet, close_coin, close_reason)
            if closed:
                state["active"] = False
                state["pendingClose"] = False
                state["closedAt"] = now
                state["closeReason"] = close_reason
                if "position_already_closed" in str(close_result):
                    state["closeReason"] = "position_already_closed"
            else:
                state["pendingClose"] = True

    # --- Save state ---
    state["lastCheck"] = now
    state["lastPrice"] = price
    atomic_write(state_file, state)

    # --- Build result ---
    if is_long:
        retrace_from_hw = (1 - price / hw) * 100 if hw > 0 else 0
    else:
        retrace_from_hw = (price / hw - 1) * 100 if hw > 0 else 0

    tier_name = (f"Tier {tier_idx+1} ({tiers[tier_idx]['triggerPct']}%->lock {tiers[tier_idx]['lockPct']}%)"
                 if tier_idx is not None and tier_idx >= 0 else "None")

    locked_profit = 0
    if tier_floor:
        locked_profit = round(((tier_floor - entry) if is_long else (entry - tier_floor)) * size, 2)

    verbose = os.environ.get("WOLF_DSL_VERBOSE") == "1"

    result = {
        "asset": state["asset"],
        "direction": direction,
        "strategyKey": strategy_cfg.get("_key", "unknown"),
        "status": "closed" if closed else ("pending_close" if state.get("pendingClose") else "active"),
        "upnl": round(upnl, 2),
        "upnl_pct": round(upnl_pct, 2),
        "close_reason": close_reason,
        "tier_changed": tier_changed,
        "phase1_autocut": phase1_autocut,
    }
    if phase1_autocut:
        result["elapsed_minutes"] = round(elapsed_minutes)
    if verbose:
        result.update({
            "price": price,
            "phase": phase,
            "hw": hw,
            "floor": effective_floor,
            "tier_name": tier_name,
            "locked_profit": locked_profit,
            "retrace_pct": round(retrace_from_hw, 2),
            "breach_count": breach_count,
            "breaches_needed": breaches_needed,
            "breached": breached,
            "should_close": should_close,
            "closed": closed,
            "close_result": close_result,
            "elapsed_minutes": round(elapsed_minutes),
            "stagnation_triggered": stagnation_triggered,
        })
    return result


# ===============================================================
# Main: iterate all strategies and their active DSL state files
# ===============================================================

strategies = load_all_strategies()

if not strategies:
    print(json.dumps({
        "status": "ok",
        "time": now,
        "strategies": 0,
        "positions": 0,
        "results": [],
        "message": "No enabled strategies found"
    }))
    sys.exit(0)

# Batch-fetch prices: one call for all crypto, one per unique XYZ wallet
all_mids = fetch_all_mids()
xyz_prices = {}  # wallet -> {coin -> price}

# Collect all state files across strategies
all_state_entries = []  # (state_file_path, strategy_cfg)

for key, cfg in strategies.items():
    state_files = sorted(glob.glob(dsl_state_glob(key)))
    for sf in state_files:
        all_state_entries.append((sf, cfg))

# Pre-scan state files to find XYZ wallets
for sf, cfg in all_state_entries:
    try:
        with open(sf) as f:
            s = json.load(f)
        if not s.get("active") and not s.get("pendingClose"):
            continue
        if s.get("dex") == "xyz" or s.get("asset", "").startswith("xyz:"):
            w = s.get("wallet", cfg.get("wallet", ""))
            if w and w not in xyz_prices:
                xyz_prices[w] = fetch_xyz_positions(w)
    except (json.JSONDecodeError, FileNotFoundError):
        continue

# Process each position
results = []
closed_positions = []
errors = []

for sf, cfg in all_state_entries:
    try:
        with open(sf) as f:
            state = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        errors.append({"file": os.path.basename(sf), "strategyKey": cfg.get("_key"), "error": str(e)})
        continue

    if not state.get("active") and not state.get("pendingClose"):
        continue

    asset = state.get("asset", "")
    is_xyz = state.get("dex") == "xyz" or asset.startswith("xyz:")

    # Resolve price
    price = None
    if is_xyz:
        wallet = state.get("wallet", cfg.get("wallet", ""))
        xyz_coin = asset if asset.startswith("xyz:") else f"xyz:{asset}"
        wp = xyz_prices.get(wallet, {})
        price = wp.get(xyz_coin)
    else:
        price_str = all_mids.get(asset)
        if price_str:
            price = float(price_str)

    if price is None:
        # Price fetch failed
        fails = state.get("consecutiveFetchFailures", 0) + 1
        state["consecutiveFetchFailures"] = fails
        state["lastCheck"] = now
        max_ff = state.get("maxFetchFailures", 10)
        if fails >= max_ff:
            state["active"] = False
            state["closeReason"] = f"Auto-deactivated: {fails} consecutive fetch failures"
        atomic_write(sf, state)
        errors.append({
            "file": os.path.basename(sf),
            "strategyKey": cfg.get("_key"),
            "asset": asset,
            "error": "price_fetch_failed",
            "consecutive_failures": fails,
            "deactivated": fails >= max_ff
        })
        continue

    state["consecutiveFetchFailures"] = 0
    result = process_position(sf, state, price, cfg)
    results.append(result)

    if result.get("status") == "closed":
        closed_positions.append(result)

# --- Output ---
any_closed = len(closed_positions) > 0
any_tier_change = any(r.get("tier_changed") for r in results)

print(json.dumps({
    "status": "ok",
    "time": now,
    "strategies": len(strategies),
    "positions": len(results),
    "active": len([r for r in results if r["status"] == "active"]),
    "closed_this_run": len(closed_positions),
    "results": results,
    "errors": errors if errors else None,
    "any_closed": any_closed,
    "any_tier_change": any_tier_change,
    "state_files_found": len(all_state_entries)
}))
