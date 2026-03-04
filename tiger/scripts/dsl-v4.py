#!/usr/bin/env python3
"""DSL v4 — Enhanced 2-phase with configurable tier ratcheting.
Supports LONG and SHORT. Auto-closes positions on breach via mcporter.

Modes:
- Single-file mode: set DSL_STATE_FILE=/path/to/dsl-ASSET.json
- Combined mode: if DSL_STATE_FILE is unset, iterates all dsl-*.json under
  state/{strategyId}/ (or all instances if strategyId is unavailable)

Units convention:
- upnl_pct is whole-number percent (2.1 = 2.1%)
- triggerPct / lockPct support both decimal (0.05) and whole (5) inputs
"""

import glob
import json
import os
import sys
from datetime import datetime, timezone

from tiger_config import resolve_dependencies


def _iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_decimal_pct(value):
    """Normalize percent inputs: 5 -> 0.05, 0.05 -> 0.05, 1 -> 0.01."""
    v = float(value)
    return v / 100.0 if v >= 1 else v


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _apply_dsl_retrace_overrides(state, config):
    """Allow config-level DSL retrace overrides (ROAR integration)."""
    overrides = config.get("dslRetrace", {})
    if not isinstance(overrides, dict):
        return

    phase1 = overrides.get("phase1")
    phase2 = overrides.get("phase2")

    if phase1 is not None:
        state.setdefault("phase1", {})["retraceThreshold"] = _safe_float(phase1, 0.015)
    if phase2 is not None:
        state.setdefault("phase2", {})["retraceThreshold"] = _safe_float(phase2, 0.012)


def _resolve_state_files(config, workspace, state_file_env):
    if state_file_env:
        return [state_file_env]

    instance_key = config.get("strategyId")
    if instance_key:
        pattern = os.path.join(workspace, "state", instance_key, "dsl-*.json")
        return sorted(glob.glob(pattern))

    pattern = os.path.join(workspace, "state", "*", "dsl-*.json")
    return sorted(glob.glob(pattern))


def _process_state_file(state_file, config, deps):
    now = _iso_now()
    get_prices = deps["get_prices"]
    close_position = deps["close_position"]
    atomic_write = deps["atomic_write"]

    try:
        with open(state_file) as f:
            state = json.load(f)
    except Exception as e:
        return {
            "status": "error",
            "state_file": state_file,
            "error": f"state_load_failed: {e}",
            "time": now,
        }, True

    asset = state.get("asset")

    if not state.get("active"):
        if not state.get("pendingClose"):
            return {
                "status": "inactive",
                "asset": asset,
                "state_file": state_file,
                "time": now,
            }, False

    _apply_dsl_retrace_overrides(state, config)

    direction = state.get("direction", "LONG").upper()
    is_long = direction == "LONG"
    breach_decay_mode = state.get("breachDecay", "hard")
    max_fetch_failures = int(state.get("maxFetchFailures", 10))

    try:
        prices_result = get_prices([asset] if asset else None)
        if not prices_result or prices_result.get("error"):
            raise RuntimeError(prices_result.get("error", "empty response") if prices_result else "no response")
        if "prices" in prices_result:
            mids = prices_result["prices"]
        else:
            mids = prices_result
        price = float(mids[asset])
        state["consecutiveFetchFailures"] = 0
    except Exception as e:
        fails = state.get("consecutiveFetchFailures", 0) + 1
        state["consecutiveFetchFailures"] = fails
        state["lastCheck"] = now
        if fails >= max_fetch_failures:
            state["active"] = False
            state["closeReason"] = f"Auto-deactivated: {fails} consecutive fetch failures"
        atomic_write(state_file, state)
        return {
            "status": "error",
            "error": f"price_fetch_failed: {str(e)}",
            "asset": asset,
            "state_file": state_file,
            "consecutive_failures": fails,
            "deactivated": fails >= max_fetch_failures,
            "pending_close": state.get("pendingClose", False),
            "time": now,
        }, True

    entry = float(state["entryPrice"])
    size = float(state["size"])
    leverage = float(state["leverage"])
    hw = float(state["highWaterPrice"])
    phase = int(state["phase"])
    breach_count = int(state.get("currentBreachCount", 0))
    tier_idx = int(state.get("currentTierIndex", -1))
    tier_floor = state.get("tierFloorPrice")
    tiers = state.get("tiers", [])
    force_close = state.get("pendingClose", False)

    # uPnL
    if is_long:
        upnl = (price - entry) * size
    else:
        upnl = (entry - price) * size
    margin = entry * size / leverage
    upnl_pct = upnl / margin * 100

    # Update high water
    if is_long and price > hw:
        hw = price
        state["highWaterPrice"] = hw
    elif not is_long and price < hw:
        hw = price
        state["highWaterPrice"] = hw

    # Tier upgrades
    previous_tier_idx = tier_idx
    tier_changed = False
    for i, tier in enumerate(tiers):
        if i <= tier_idx:
            continue

        trigger_pct = _as_decimal_pct(tier.get("triggerPct", 0))
        lock_pct = _as_decimal_pct(tier.get("lockPct", 0))

        if upnl_pct >= trigger_pct * 100:
            tier_idx = i
            tier_changed = True
            if is_long:
                tier_floor = round(entry * (1 + lock_pct / leverage), 4)
            else:
                tier_floor = round(entry * (1 - lock_pct / leverage), 4)
            state["currentTierIndex"] = tier_idx
            state["tierFloorPrice"] = tier_floor
            if phase == 1:
                phase2_trigger = state.get("phase2TriggerTier", 0)
                if tier_idx >= phase2_trigger:
                    phase = 2
                    state["phase"] = 2
                    breach_count = 0
                    state["currentBreachCount"] = 0

    # Effective floor
    if phase == 1:
        retrace = _safe_float(state["phase1"]["retraceThreshold"], 0.015)
        breaches_needed = int(state["phase1"]["consecutiveBreachesRequired"])
        abs_floor = float(state["phase1"]["absoluteFloor"])
        if is_long:
            trailing_floor = round(hw * (1 - retrace), 4)
            effective_floor = max(abs_floor, trailing_floor)
        else:
            trailing_floor = round(hw * (1 + retrace), 4)
            effective_floor = min(abs_floor, trailing_floor)
    else:
        if tier_idx >= 0:
            retrace = _safe_float(
                tiers[tier_idx].get("retrace", state["phase2"]["retraceThreshold"]),
                _safe_float(state["phase2"]["retraceThreshold"], 0.012),
            )
        else:
            retrace = _safe_float(state["phase2"]["retraceThreshold"], 0.012)
        breaches_needed = int(state["phase2"]["consecutiveBreachesRequired"])
        if is_long:
            trailing_floor = round(hw * (1 - retrace), 4)
            effective_floor = max(float(tier_floor or 0), trailing_floor)
        else:
            trailing_floor = round(hw * (1 + retrace), 4)
            effective_floor = min(float(tier_floor or float("inf")), trailing_floor)

    state["floorPrice"] = round(effective_floor, 4)

    # Breach check
    breached = price <= effective_floor if is_long else price >= effective_floor

    if breached:
        breach_count += 1
    else:
        if breach_decay_mode == "soft":
            breach_count = max(0, breach_count - 1)
        else:
            breach_count = 0

    state["currentBreachCount"] = breach_count
    should_close = breach_count >= breaches_needed or force_close

    # Auto-close on breach
    closed = False
    close_result = None

    if should_close:
        wallet = state.get("wallet", "")
        if wallet and asset:
            reason = (
                f"DSL breach: Phase {phase}, {breach_count}/{breaches_needed} breaches, "
                f"price {price}, floor {effective_floor}"
            )
            result = close_position(wallet, asset, reason=reason)
            if isinstance(result, dict) and not result.get("error"):
                closed = True
                close_result = json.dumps(result)
                state["active"] = False
                state["pendingClose"] = False
                state["closedAt"] = now
                state["closeReason"] = reason
            else:
                err = result.get("error", "unknown") if isinstance(result, dict) else "unknown"
                close_result = f"close_failed: {err}"
                state["pendingClose"] = True
        else:
            close_result = "error: no wallet or asset in state file"
            state["pendingClose"] = True

    # Save state atomically
    state["lastCheck"] = now
    state["lastPrice"] = price
    atomic_write(state_file, state)

    retrace_from_hw = ((1 - price / hw) * 100) if (is_long and hw > 0) else ((price / hw - 1) * 100 if hw > 0 else 0)

    tier_name = "None"
    if tier_idx >= 0:
        t = tiers[tier_idx]
        t_trigger = _as_decimal_pct(t.get("triggerPct", 0)) * 100
        t_lock = _as_decimal_pct(t.get("lockPct", 0)) * 100
        tier_name = f"Tier {tier_idx + 1} ({t_trigger:.0f}%→lock {t_lock:.0f}%)"

    previous_tier_name = None
    if tier_changed:
        if previous_tier_idx >= 0:
            t = tiers[previous_tier_idx]
            t_trigger = _as_decimal_pct(t.get("triggerPct", 0)) * 100
            t_lock = _as_decimal_pct(t.get("lockPct", 0)) * 100
            previous_tier_name = f"Tier {previous_tier_idx + 1} ({t_trigger:.0f}%→lock {t_lock:.0f}%)"
        else:
            previous_tier_name = "None (Phase 1)"

    if tier_floor:
        locked_profit = round(((float(tier_floor) - entry) if is_long else (entry - float(tier_floor))) * size, 2)
    else:
        locked_profit = 0

    elapsed_minutes = 0
    if state.get("createdAt"):
        try:
            created = datetime.fromisoformat(str(state["createdAt"]).replace("Z", "+00:00"))
            elapsed_minutes = round((datetime.now(timezone.utc) - created).total_seconds() / 60)
        except (ValueError, TypeError):
            pass

    distance_to_next_tier = None
    next_tier_idx = tier_idx + 1
    if next_tier_idx < len(tiers):
        next_trigger = _as_decimal_pct(tiers[next_tier_idx].get("triggerPct", 0)) * 100
        distance_to_next_tier = round(next_trigger - upnl_pct, 2)

    result = {
        "status": "inactive" if closed else ("pending_close" if state.get("pendingClose") else "active"),
        "asset": asset,
        "direction": direction,
        "state_file": state_file,
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

    return result, False


def main(deps=None, env=None):
    deps = deps or resolve_dependencies()
    env = env or os.environ
    config = deps["load_config"]()
    workspace = deps["workspace"]
    state_file_env = env.get("DSL_STATE_FILE")
    files = _resolve_state_files(config, workspace, state_file_env)

    if state_file_env:
        result, errored = _process_state_file(files[0], config, deps)
        print(json.dumps(result))
        sys.exit(1 if errored else 0)

    if not files:
        print(json.dumps({
            "status": "idle",
            "mode": "combined",
            "processed": 0,
            "message": "no_dsl_state_files",
            "time": _iso_now(),
        }))
        return

    results = []
    error_count = 0
    closed_count = 0
    active_count = 0

    for state_file in files:
        result, errored = _process_state_file(state_file, config, deps)
        if errored:
            error_count += 1
        if result.get("closed"):
            closed_count += 1
        if result.get("status") == "active":
            active_count += 1
        # Only include results with meaningful state changes
        if result.get("closed") or (
            result.get("status") not in ("inactive",) and (
                result.get("tier_changed") or
                result.get("breached") or result.get("pending_close") or
                result.get("status") == "error"
            )
        ):
            results.append(result)

    # Nothing actionable → heartbeat
    if not results:
        print(json.dumps({"success": True, "heartbeat": "HEARTBEAT_OK"}))
        return

    print(json.dumps({
        "status": "ok" if error_count == 0 else "partial_error",
        "mode": "combined",
        "processed": len(files),
        "active": active_count,
        "closed": closed_count,
        "results": results,
        "time": _iso_now(),
    }))


if __name__ == "__main__":
    main()
