#!/usr/bin/env python3
"""DSL v4.1 — Enhanced 2-phase with configurable tier ratcheting + stagnation TP.
Supports LONG and SHORT. Auto-closes positions on breach via mcporter.
v4.1 over v4:
  - Stagnation take-profit: closes if ROE >= threshold AND high water hasn't
    improved for N hours. Prevents giving back gains on stalled positions.
    Config: stagnation.minROE (default 8%), stagnation.staleHours (default 3),
    stagnation.priceRangePct (default 1.0%)
v4 over v3:
  - Error handling: graceful degradation on API failure
  - Close retry: retries + pendingClose flag for failed closes
  - Per-tier retrace: tighten trailing stops as profit grows
  - Breach decay: soft mode (decay by 1) vs hard (reset to 0)
  - Enriched output: tier_changed, elapsed_minutes, distance_to_next_tier
Backward-compatible with v3/v2 state files (all new fields have defaults).
"""
import json, sys, subprocess, os, time
from datetime import datetime, timezone

STATE_FILE = os.environ.get("DSL_STATE_FILE", "/data/workspace/trailing-stop-state.json")

with open(STATE_FILE) as f:
    state = json.load(f)

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

if not state.get("active"):
    if not state.get("pendingClose"):
        print(json.dumps({"status": "inactive"}))
        sys.exit(0)

direction = state.get("direction", "LONG").upper()
is_long = direction == "LONG"
breach_decay_mode = state.get("breachDecay", "hard")
close_retries = state.get("closeRetries", 2)
close_retry_delay = state.get("closeRetryDelaySec", 3)
max_fetch_failures = state.get("maxFetchFailures", 10)

# ─── Stagnation take-profit config ───
stag_cfg = state.get("stagnation", {})
stag_enabled = stag_cfg.get("enabled", True)
stag_min_roe = stag_cfg.get("minROE", 8.0)          # minimum ROE% to trigger
stag_stale_hours = stag_cfg.get("staleHours", 1.0)   # hours HW must be stale
stag_range_pct = stag_cfg.get("priceRangePct", 1.0)  # max price movement % in window


def _fetch_prices(dex=None):
    """Fetch mid prices via Senpi MCP market_get_prices. Returns dict of asset->price (string)."""
    try:
        args = {} if dex is None or dex == "" else {"dex": dex}
        r = subprocess.run(
            ["mcporter", "call", "senpi", "market_get_prices", "--args", json.dumps(args)],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(r.stdout)
        inner = data.get("data", data.get("result", data))
        prices = inner.get("prices") if isinstance(inner, dict) else data.get("prices")
        return prices if isinstance(prices, dict) else {}
    except Exception:
        return {}


# ─── Fetch price ───
try:
    asset_name = state["asset"]
    is_xyz = asset_name.startswith("xyz:") or state.get("dex") == "xyz"
    if not asset_name.startswith("xyz:") and is_xyz:
        asset_name = "xyz:" + asset_name
    if is_xyz:
        # XYZ DEX: use market mids from MCP (keys like xyz:XYZ100, xyz:TSLA)
        prices = _fetch_prices(dex="xyz")
        price_str = prices.get(asset_name) or prices.get(asset_name.replace("xyz:", "", 1))
        if price_str is None:
            raise Exception(f"XYZ asset {asset_name} not found in market_get_prices(dex=xyz)")
        price = float(price_str)
    else:
        # Main DEX: use market mids from MCP
        prices = _fetch_prices()
        if asset_name not in prices:
            raise Exception(f"Asset {asset_name} not found in market_get_prices")
        price = float(prices[asset_name])
    state["consecutiveFetchFailures"] = 0
except Exception as e:
    fails = state.get("consecutiveFetchFailures", 0) + 1
    state["consecutiveFetchFailures"] = fails
    state["lastCheck"] = now
    if fails >= max_fetch_failures:
        state["active"] = False
        state["closeReason"] = f"Auto-deactivated: {fails} consecutive fetch failures"
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(json.dumps({
        "status": "error",
        "error": f"price_fetch_failed: {str(e)}",
        "asset": state.get("asset"),
        "consecutive_failures": fails,
        "deactivated": fails >= max_fetch_failures,
        "pending_close": state.get("pendingClose", False),
        "time": now
    }))
    sys.exit(1)

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

# ─── Auto-fix absoluteFloor based on ROE (not raw price) ───
# retraceThreshold is ROE-based (e.g. 0.05 = 5% ROE), convert to price via leverage
retrace_roe = state["phase1"]["retraceThreshold"]
# retraceThreshold is stored as percentage (e.g. 5 = 5%), convert to decimal first
retrace_decimal = retrace_roe / 100 if retrace_roe > 1 else retrace_roe
retrace_price = retrace_decimal / leverage
if is_long:
    correct_floor = round(entry * (1 - retrace_price), 6)
else:
    correct_floor = round(entry * (1 + retrace_price), 6)
# Respect manual floor: for LONG, use the LOWER of computed vs existing (wider stop)
# For SHORT, use the HIGHER of computed vs existing (wider stop)
existing_floor = state["phase1"].get("absoluteFloor", correct_floor)
if is_long:
    final_floor = min(correct_floor, existing_floor) if existing_floor > 0 else correct_floor
else:
    final_floor = max(correct_floor, existing_floor) if existing_floor > 0 else correct_floor
state["phase1"]["absoluteFloor"] = final_floor
state["floorPrice"] = final_floor

# ─── uPnL ───
if is_long:
    upnl = (price - entry) * size
else:
    upnl = (entry - price) * size
margin = entry * size / state["leverage"]
upnl_pct = upnl / margin * 100

# ─── Update high water ───
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

# ─── Tier upgrades ───
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

# ─── Effective floor ───
if phase == 1:
    retrace_roe = state["phase1"]["retraceThreshold"]
    retrace = retrace_roe / leverage  # Convert ROE retrace to price retrace
    breaches_needed = state["phase1"]["consecutiveBreachesRequired"]
    abs_floor = state["phase1"]["absoluteFloor"]
    if is_long:
        trailing_floor = round(hw * (1 - retrace), 4)
        effective_floor = max(abs_floor, trailing_floor)
    else:
        trailing_floor = round(hw * (1 + retrace), 4)
        effective_floor = min(abs_floor, trailing_floor)
else:
    if tier_idx >= 0:
        retrace_roe = tiers[tier_idx].get("retrace", state.get("phase2", {}).get("retraceThreshold", 0.05))
    else:
        retrace_roe = state.get("phase2", {}).get("retraceThreshold", 0.05)
    retrace = retrace_roe / leverage  # Convert ROE retrace to price retrace
    breaches_needed = (tiers[tier_idx].get("breachesRequired", tiers[tier_idx].get("retraceClose", 2)) if tier_idx >= 0 
                       else state.get("phase2", {}).get("consecutiveBreachesRequired", 2))
    if is_long:
        trailing_floor = round(hw * (1 - retrace), 4)
        effective_floor = max(tier_floor or 0, trailing_floor)
    else:
        trailing_floor = round(hw * (1 + retrace), 4)
        effective_floor = min(tier_floor or float('inf'), trailing_floor)

state["floorPrice"] = round(effective_floor, 4)

# ─── Stagnation take-profit check ───
stagnation_triggered = False
stag_hours_stale = 0.0
if stag_enabled and upnl_pct >= stag_min_roe and state.get("hwTimestamp"):
    try:
        hw_time = datetime.fromisoformat(state["hwTimestamp"].replace("Z", "+00:00"))
        stag_hours_stale = (datetime.now(timezone.utc) - hw_time).total_seconds() / 3600
        if stag_hours_stale >= stag_stale_hours:
            # Check price range: has price stayed within range?
            hw_price = state["highWaterPrice"]
            if hw_price > 0:
                price_move_pct = abs(price - hw_price) / hw_price * 100
                if price_move_pct <= stag_range_pct:
                    stagnation_triggered = True
    except (ValueError, TypeError):
        pass

# ─── Breach check ───
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

should_close = breach_count >= breaches_needed or force_close or stagnation_triggered

# ─── Auto-close on breach (with retry) ───
closed = False
close_result = None

if should_close:
    wallet = state.get("wallet", "")
    asset = state["asset"]
    # XYZ assets need xyz: prefix for close_position
    close_coin = asset if asset.startswith("xyz:") else f"xyz:{asset}" if is_xyz else asset
    if wallet:
        for attempt in range(close_retries):
            try:
                cr = subprocess.run(
                    ["mcporter", "call", "senpi", "close_position", "--args",
                     json.dumps({
                         "strategyWalletAddress": wallet,
                         "coin": close_coin,
                         "reason": f"DSL {'stagnation TP' if stagnation_triggered else 'breach'}: Phase {phase}, {'stale '+str(round(stag_hours_stale,1))+'h, ROE '+str(round(upnl_pct,1))+'%' if stagnation_triggered else str(breach_count)+'/'+str(breaches_needed)+' breaches'}, price {price}, floor {effective_floor}"
                     })],
                    capture_output=True, text=True, timeout=30
                )
                result_text = cr.stdout.strip()
                no_position = "CLOSE_NO_POSITION" in result_text
                if cr.returncode == 0 and ("error" not in result_text.lower() or no_position):
                    closed = True
                    close_result = result_text if not no_position else "position_already_closed"
                    state["active"] = False
                    state["pendingClose"] = False
                    state["closedAt"] = now
                    state["closeReason"] = ("position_already_closed" if no_position else
                        f"DSL {'stagnation TP' if stagnation_triggered else 'breach'}: Phase {phase}, price {price}, floor {effective_floor}" + (f", stale {round(stag_hours_stale,1)}h" if stagnation_triggered else ""))
                    break
                else:
                    close_result = f"api_error_attempt_{attempt+1}: {result_text}"
            except Exception as e:
                close_result = f"error_attempt_{attempt+1}: {str(e)}"
            if attempt < close_retries - 1:
                time.sleep(close_retry_delay)
        if not closed:
            state["pendingClose"] = True
    else:
        close_result = "error: no wallet in state file"
        state["pendingClose"] = True

# ─── Save state ───
state["lastCheck"] = now
state["lastPrice"] = price
with open(STATE_FILE, "w") as f:
    json.dump(state, f, indent=2)

# ─── Output ───
if is_long:
    retrace_from_hw = (1 - price / hw) * 100 if hw > 0 else 0
else:
    retrace_from_hw = (price / hw - 1) * 100 if hw > 0 else 0

tier_name = f"Tier {tier_idx+1} ({tiers[tier_idx]['triggerPct']}%→lock {tiers[tier_idx]['lockPct']}%)" if tier_idx is not None and tier_idx >= 0 else "None"

previous_tier_name = None
if tier_changed:
    if previous_tier_idx is not None and previous_tier_idx >= 0:
        t = tiers[previous_tier_idx]
        previous_tier_name = f"Tier {previous_tier_idx+1} ({t['triggerPct']}%→lock {t['lockPct']}%)"
    else:
        previous_tier_name = "None (Phase 1)"

if tier_floor:
    locked_profit = round(((tier_floor - entry) if is_long else (entry - tier_floor)) * size, 2)
else:
    locked_profit = 0

elapsed_minutes = 0
if state.get("createdAt"):
    try:
        created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
        elapsed_minutes = round((datetime.now(timezone.utc) - created).total_seconds() / 60)
    except (ValueError, TypeError):
        pass

distance_to_next_tier = None
next_tier_idx = (tier_idx + 1) if tier_idx is not None else 0
if next_tier_idx < len(tiers):
    distance_to_next_tier = round(tiers[next_tier_idx]["triggerPct"] - upnl_pct, 2)

print(json.dumps({
    "status": "inactive" if closed else ("pending_close" if state.get("pendingClose") else "active"),
    "asset": state["asset"], "direction": direction,
    "price": price, "upnl": round(upnl, 2), "upnl_pct": round(upnl_pct, 2),
    "phase": phase, "hw": hw, "floor": effective_floor,
    "trailing_floor": trailing_floor, "tier_floor": tier_floor,
    "tier_name": tier_name, "locked_profit": locked_profit,
    "retrace_pct": round(retrace_from_hw, 2),
    "breach_count": breach_count, "breaches_needed": breaches_needed,
    "breached": breached, "should_close": should_close,
    "closed": closed, "close_result": close_result, "time": now,
    "tier_changed": tier_changed, "previous_tier": previous_tier_name,
    "elapsed_minutes": elapsed_minutes,
    "distance_to_next_tier_pct": distance_to_next_tier,
    "pending_close": state.get("pendingClose", False),
    "consecutive_failures": state.get("consecutiveFetchFailures", 0),
    "stagnation": {
        "enabled": stag_enabled,
        "triggered": stagnation_triggered,
        "hours_stale": round(stag_hours_stale, 2),
        "threshold_hours": stag_stale_hours,
        "min_roe": stag_min_roe,
        "current_roe": round(upnl_pct, 2)
    }
}))
