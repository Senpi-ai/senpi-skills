#!/usr/bin/env python3
"""DSL v5 — Strategy-scoped state, MCP price fetch, delete on close.
v5 over v4:
  - State path: DSL_STATE_DIR / DSL_STRATEGY_ID / {asset}.json (required)
  - Asset filename: xyz:SILVER → xyz--SILVER.json (filesystem-safe)
  - Price via MCP: senpi market_get_prices or allMids with dex for xyz assets
  - On close: delete state file (no archive)
State schema unchanged from v4.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path & config
# ---------------------------------------------------------------------------

DEFAULT_STATE_DIR = "/data/workspace/dsl"


def asset_to_filename(asset: str) -> str:
    """xyz:SILVER → xyz--SILVER (filesystem-safe)."""
    if asset.startswith("xyz:"):
        return asset.replace(":", "--", 1)
    return asset


def resolve_state_file() -> tuple[str, str | None]:
    """Return (state_file_path, error_message). error_message is None if valid."""
    state_dir = os.environ.get("DSL_STATE_DIR", DEFAULT_STATE_DIR)
    strategy_id = os.environ.get("DSL_STRATEGY_ID", "").strip()
    asset = os.environ.get("DSL_ASSET", "").strip()
    if not strategy_id or not asset:
        return "", "DSL_STRATEGY_ID and DSL_ASSET required"
    path = os.path.join(state_dir, strategy_id, f"{asset_to_filename(asset)}.json")
    if not os.path.isfile(path):
        return path, "state_file_not_found"
    return path, None


def dex_and_lookup_symbol(asset: str) -> tuple[str, str]:
    """Return (dex, lookup_symbol) for MCP. Main dex: dex='', xyz: dex='xyz'."""
    if asset.startswith("xyz:"):
        return "xyz", asset.split(":", 1)[1]
    return "", asset


# ---------------------------------------------------------------------------
# Price fetch (MCP)
# ---------------------------------------------------------------------------

def _parse_price_from_response(data: dict, response_key: str) -> str | None:
    """Extract price string from MCP response (market_get_prices envelope or flat allMids).
    response_key: for main use bare symbol (e.g. ETH); for xyz use prefixed (e.g. xyz:SILVER).
    """
    if "prices" in data:
        return data["prices"].get(response_key)
    return data.get(response_key)


def _unwrap_mcp_response(raw: dict) -> dict | None:
    """Unwrap MCP envelope if present: { success, data: { prices, ... } } -> { prices, ... }."""
    if not raw or not isinstance(raw, dict):
        return None
    if "data" in raw and isinstance(raw.get("data"), dict):
        return raw["data"]
    return raw


def fetch_price_mcp(dex: str, lookup_symbol: str) -> tuple[float | None, str | None]:
    """Fetch mid price via MCP only: senpi market_get_prices then allMids fallback.
    MCP expects dex '' for main (passing 'main' causes INTERNAL error). xyz response uses keys like xyz:SILVER.
    """
    try:
        dex = dex.strip() if dex else ""
        if dex.lower() == "main":
            dex = ""
        is_xyz = dex.lower() == "xyz"
        response_key = f"xyz:{lookup_symbol}" if is_xyz else lookup_symbol

        args_mgp = {"assets": [lookup_symbol], "dex": dex}
        r = subprocess.run(
            ["mcporter", "call", "senpi", "market_get_prices", "--args", json.dumps(args_mgp)],
            capture_output=True, text=True, timeout=15,
        )
        data = None
        if r.returncode == 0 and r.stdout:
            try:
                raw = json.loads(r.stdout)
                data = _unwrap_mcp_response(raw)
            except json.JSONDecodeError:
                pass
        price_str = _parse_price_from_response(data, response_key) if data else None

        if price_str is None:
            args_am = {"dex": dex} if dex else {}
            r = subprocess.run(
                ["mcporter", "call", "senpi", "allMids", "--args", json.dumps(args_am)],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout:
                try:
                    raw = json.loads(r.stdout)
                    data = _unwrap_mcp_response(raw)
                    price_str = _parse_price_from_response(data, response_key)
                except json.JSONDecodeError:
                    pass
            elif r.returncode != 0 and data is None:
                return None, (r.stderr or r.stdout or "non-zero exit")

        if price_str is None:
            return None, f"no price for {lookup_symbol} (dex={dex or 'main'})"
        return float(price_str), None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)
    except (TypeError, ValueError) as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Trading logic: high water, tiers, floor, breach
# ---------------------------------------------------------------------------

def update_high_water(state: dict, price: float, is_long: bool) -> float:
    """Update state high water; return new hw."""
    hw = state["highWaterPrice"]
    if is_long and price > hw:
        hw = price
        state["highWaterPrice"] = hw
    elif not is_long and price < hw:
        hw = price
        state["highWaterPrice"] = hw
    return hw


def apply_tier_upgrades(
    state: dict, upnl_pct: float, is_long: bool, hw: float
) -> tuple[int, float | None, bool, int]:
    """Apply tier upgrades based on ROE. Mutates state. Returns (tier_idx, tier_floor, tier_changed, previous_tier_idx).
    Tier floor = entry + (hw - entry) * lockPct/100 (LONG) or entry - (entry - hw) * lockPct/100 (SHORT):
    lockPct is the fraction of the entry→hw range to lock, not ROE %.
    """
    tiers = state["tiers"]
    tier_idx = state["currentTierIndex"]
    tier_floor = state["tierFloorPrice"]
    phase = state["phase"]
    breach_count = state["currentBreachCount"]
    entry = state["entryPrice"]
    previous_tier_idx = tier_idx
    tier_changed = False

    for i, tier in enumerate(tiers):
        if i <= tier_idx:
            continue
        if upnl_pct >= tier["triggerPct"]:
            tier_idx = i
            tier_changed = True
            # Floor = entry + fraction of (entry → hw) range; lockPct = that fraction
            if is_long:
                tier_floor = round(entry + (hw - entry) * tier["lockPct"] / 100, 4)
            else:
                tier_floor = round(entry - (entry - hw) * tier["lockPct"] / 100, 4)
            state["currentTierIndex"] = tier_idx
            state["tierFloorPrice"] = tier_floor
            if phase == 1 and tier_idx >= state.get("phase2TriggerTier", 0):
                state["phase"] = 2
                breach_count = 0
                state["currentBreachCount"] = 0
                phase = 2

    return tier_idx, tier_floor, tier_changed, previous_tier_idx


def compute_effective_floor(
    state: dict, phase: int, tier_idx: int, tier_floor: float | None, hw: float, is_long: bool
) -> tuple[float, float, int, float]:
    """Return (effective_floor, trailing_floor, breaches_needed, retrace)."""
    tiers = state["tiers"]
    if phase == 1:
        retrace = state["phase1"]["retraceThreshold"]
        breaches_needed = state["phase1"]["consecutiveBreachesRequired"]
        abs_floor = state["phase1"]["absoluteFloor"]
        if is_long:
            trailing_floor = round(hw * (1 - retrace), 4)
            effective_floor = max(abs_floor, trailing_floor)
        else:
            trailing_floor = round(hw * (1 + retrace), 4)
            effective_floor = min(abs_floor, trailing_floor)
        return effective_floor, trailing_floor, breaches_needed, retrace

    retrace = (
        tiers[tier_idx].get("retrace", state["phase2"]["retraceThreshold"])
        if tier_idx >= 0
        else state["phase2"]["retraceThreshold"]
    )
    breaches_needed = state["phase2"]["consecutiveBreachesRequired"]
    if is_long:
        trailing_floor = round(hw * (1 - retrace), 4)
        effective_floor = max(tier_floor or 0, trailing_floor)
    else:
        trailing_floor = round(hw * (1 + retrace), 4)
        effective_floor = min(tier_floor or float("inf"), trailing_floor)
    return effective_floor, trailing_floor, breaches_needed, retrace


def update_breach_count(state: dict, breached: bool, decay_mode: str) -> int:
    """Update state currentBreachCount; return new count."""
    count = state["currentBreachCount"]
    if breached:
        count += 1
    else:
        count = max(0, count - 1) if decay_mode == "soft" else 0
    state["currentBreachCount"] = count
    return count


# ---------------------------------------------------------------------------
# Close position (MCP)
# ---------------------------------------------------------------------------

def try_close_position(
    state: dict,
    price: float,
    phase: int,
    breach_count: int,
    breaches_needed: int,
    effective_floor: float,
    now: str,
    close_retries: int,
    close_retry_delay: float,
) -> tuple[bool, str | None]:
    """Attempt close via senpi:close_position. Mutates state. Returns (closed, close_result)."""
    wallet = state.get("wallet", "")
    coin = state["asset"]
    if not wallet:
        state["pendingClose"] = True
        return False, "error: no wallet in state file"

    reason = (
        f"DSL breach: Phase {phase}, {breach_count}/{breaches_needed} breaches, "
        f"price {price}, floor {effective_floor}"
    )
    for attempt in range(close_retries):
        try:
            cr = subprocess.run(
                ["mcporter", "call", "senpi", "close_position", "--args",
                 json.dumps({"strategyWalletAddress": wallet, "coin": coin, "reason": reason})],
                capture_output=True, text=True, timeout=30,
            )
            result_text = cr.stdout.strip()
            if cr.returncode == 0 and "error" not in result_text.lower():
                state["active"] = False
                state["pendingClose"] = False
                state["closedAt"] = now
                state["closeReason"] = f"DSL breach: Phase {phase}, price {price}, floor {effective_floor}"
                return True, result_text
            close_result = f"api_error_attempt_{attempt+1}: {result_text}"
        except Exception as e:
            close_result = f"error_attempt_{attempt+1}: {str(e)}"
        if attempt < close_retries - 1:
            time.sleep(close_retry_delay)
    state["pendingClose"] = True
    return False, close_result


# ---------------------------------------------------------------------------
# Persist: save or delete state file
# ---------------------------------------------------------------------------

def save_or_delete_state(
    state: dict, state_file: str, closed: bool, now: str, close_result: str | None
) -> str | None:
    """Persist state (save or delete file). Caller must set state['lastPrice'] before calling.
    If closed but delete fails, writes state (active: False) so cleanup can proceed later.
    """
    state["lastCheck"] = now
    if closed:
        try:
            os.remove(state_file)
            return close_result
        except OSError as e:
            # Fallback: write state so dsl-cleanup sees active: false and can clean strategy
            try:
                with open(state_file, "w") as f:
                    json.dump(state, f, indent=2)
            except OSError:
                pass
            return (close_result or "") + f"; delete_failed: {e}"
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
    return close_result


# ---------------------------------------------------------------------------
# Output builder
# ---------------------------------------------------------------------------

def build_output(
    state: dict,
    *,
    price: float,
    direction: str,
    upnl: float,
    upnl_pct: float,
    phase: int,
    hw: float,
    effective_floor: float,
    trailing_floor: float,
    tier_floor: float | None,
    tier_idx: int,
    tiers: list,
    tier_changed: bool,
    previous_tier_idx: int,
    breach_count: int,
    breaches_needed: int,
    breached: bool,
    should_close: bool,
    closed: bool,
    close_result: str | None,
    now: str,
) -> dict:
    """Build the single JSON object printed to stdout."""
    is_long = direction == "LONG"
    entry = state["entryPrice"]
    size = state["size"]

    retrace_from_hw = (
        (1 - price / hw) * 100 if hw > 0 else 0
    ) if is_long else (
        (price / hw - 1) * 100 if hw > 0 else 0
    )
    tier_name = (
        f"Tier {tier_idx+1} ({tiers[tier_idx]['triggerPct']}%→lock {tiers[tier_idx]['lockPct']}%)"
        if tier_idx >= 0 else "None"
    )
    previous_tier_name = None
    if tier_changed:
        if previous_tier_idx >= 0:
            t = tiers[previous_tier_idx]
            previous_tier_name = f"Tier {previous_tier_idx+1} ({t['triggerPct']}%→lock {t['lockPct']}%)"
        else:
            previous_tier_name = "None (Phase 1)"
    locked_profit = (
        round(((tier_floor - entry) if is_long else (entry - tier_floor)) * size, 2)
        if tier_floor else 0
    )
    elapsed_minutes = 0
    if state.get("createdAt"):
        try:
            created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
            elapsed_minutes = round((datetime.now(timezone.utc) - created).total_seconds() / 60)
        except (ValueError, TypeError):
            pass
    distance_to_next_tier = None
    if tier_idx + 1 < len(tiers):
        distance_to_next_tier = round(tiers[tier_idx + 1]["triggerPct"] - upnl_pct, 2)

    status = "inactive" if closed else ("pending_close" if state.get("pendingClose") else "active")
    return {
        "status": status,
        "asset": state["asset"],
        "direction": direction,
        "price": price,
        "upnl": round(upnl, 2),
        "upnl_pct": round(upnl_pct, 2),
        "phase": phase,
        "hw": hw,
        "floor": effective_floor,
        "trailing_floor": trailing_floor,
        "tier_floor": tier_floor,
        "tier_name": tier_name,
        "locked_profit": locked_profit,
        "retrace_pct": round(retrace_from_hw, 2),
        "breach_count": breach_count,
        "breaches_needed": breaches_needed,
        "breached": breached,
        "should_close": should_close,
        "closed": closed,
        "close_result": close_result,
        "time": now,
        "tier_changed": tier_changed,
        "previous_tier": previous_tier_name,
        "elapsed_minutes": elapsed_minutes,
        "distance_to_next_tier_pct": distance_to_next_tier,
        "pending_close": state.get("pendingClose", False),
        "consecutive_failures": state.get("consecutiveFetchFailures", 0),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    state_file, path_error = resolve_state_file()
    if path_error:
        err = {"status": "error", "error": path_error}
        if path_error == "state_file_not_found":
            err["path"] = state_file
        # stdout so agent can see it (e.g. after close-and-delete, cron keeps running until disabled)
        print(json.dumps(err))
        sys.exit(1)

    with open(state_file) as f:
        state = json.load(f)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not state.get("active") and not state.get("pendingClose"):
        print(json.dumps({"status": "inactive"}))
        return

    direction = state.get("direction", "LONG").upper()
    is_long = direction == "LONG"
    asset = state["asset"]
    dex, lookup_symbol = dex_and_lookup_symbol(asset)

    # Fetch price
    price, fetch_error = fetch_price_mcp(dex, lookup_symbol)
    if fetch_error is not None:
        fails = state.get("consecutiveFetchFailures", 0) + 1
        state["consecutiveFetchFailures"] = fails
        state["lastCheck"] = now
        max_failures = state.get("maxFetchFailures", 10)
        if fails >= max_failures:
            state["active"] = False
            state["closeReason"] = f"Auto-deactivated: {fails} consecutive fetch failures"
        try:
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
        except OSError:
            pass
        print(json.dumps({
            "status": "error",
            "error": f"price_fetch_failed: {fetch_error}",
            "asset": state.get("asset"),
            "consecutive_failures": fails,
            "deactivated": fails >= max_failures,
            "pending_close": state.get("pendingClose", False),
            "time": now,
        }))
        sys.exit(1)

    state["consecutiveFetchFailures"] = 0
    state["lastPrice"] = price
    entry = state["entryPrice"]
    size = state["size"]
    leverage = state["leverage"]
    hw = update_high_water(state, price, is_long)

    upnl = (price - entry) * size if is_long else (entry - price) * size
    margin = entry * size / leverage
    upnl_pct = upnl / margin * 100

    tier_idx, tier_floor, tier_changed, previous_tier_idx = apply_tier_upgrades(
        state, upnl_pct, is_long, hw
    )
    phase = state["phase"]
    breach_count = state["currentBreachCount"]

    effective_floor, trailing_floor, breaches_needed, _ = compute_effective_floor(
        state, phase, tier_idx, tier_floor, hw, is_long
    )
    state["floorPrice"] = round(effective_floor, 4)

    breached = price <= effective_floor if is_long else price >= effective_floor
    breach_count = update_breach_count(state, breached, state.get("breachDecay", "hard"))
    force_close = state.get("pendingClose", False)
    should_close = breach_count >= breaches_needed or force_close

    closed = False
    close_result = None
    if should_close:
        closed, close_result = try_close_position(
            state, price, phase, breach_count, breaches_needed, effective_floor, now,
            state.get("closeRetries", 2), state.get("closeRetryDelaySec", 3),
        )

    close_result = save_or_delete_state(state, state_file, closed, now, close_result)

    out = build_output(
        state,
        price=price,
        direction=direction,
        upnl=upnl,
        upnl_pct=upnl_pct,
        phase=phase,
        hw=hw,
        effective_floor=effective_floor,
        trailing_floor=trailing_floor,
        tier_floor=tier_floor,
        tier_idx=tier_idx,
        tiers=state["tiers"],
        tier_changed=tier_changed,
        previous_tier_idx=previous_tier_idx,
        breach_count=breach_count,
        breaches_needed=breaches_needed,
        breached=breached,
        should_close=should_close,
        closed=closed,
        close_result=close_result,
        now=now,
    )
    print(json.dumps(out))


if __name__ == "__main__":
    main()
