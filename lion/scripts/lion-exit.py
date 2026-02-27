#!/usr/bin/env python3
"""
lion-exit.py — LION pattern-specific exit management.
Runs every 60s. Evaluates each position for pattern-specific exit signals.
Tier 2 cron.

Exit rules from SKILL.md:
- CASCADE_REVERSAL: target 40-60% snapback, 2h time stop, 70% lock at 50% target,
  emergency on 5% more OI drop
- BOOK_IMBALANCE: target pre-sweep level, 30min time stop, 80% lock at 60% target,
  emergency on imbalance flip
- SQUEEZE: 3-5% move target, 12h time stop, tiered locks, emergency on funding normalize
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from lion_config import (
    load_config, load_state, save_state, load_oi_history,
    get_asset_data, get_all_instruments, now_utc, minutes_since, hours_since,
    output, output_heartbeat, output_error
)
from lion_lib import oi_change_pct


def evaluate_cascade_exit(asset, pos, config, oi_history, current_price):
    """Evaluate exit for CASCADE_REVERSAL position."""
    actions = []
    entry_price = pos.get("entryPrice", current_price)
    direction = pos.get("direction", "LONG")
    opened_at = pos.get("openedAt")

    # ROE calculation
    if direction == "LONG":
        roe_pct = (current_price - entry_price) / entry_price * 100 * pos.get("leverage", 6)
    else:
        roe_pct = (entry_price - current_price) / entry_price * 100 * pos.get("leverage", 6)

    # Time stop: 2 hours
    time_stop_min = config.get("cascadeTimeStopMin", 120)
    elapsed_min = minutes_since(opened_at) if opened_at else 0

    # 1. Time stop (CRITICAL priority)
    if elapsed_min >= time_stop_min:
        actions.append({
            "action": "close", "priority": "CRITICAL",
            "reason": f"TIME_STOP: {elapsed_min:.0f}min elapsed, limit {time_stop_min}",
            "roe": round(roe_pct, 2)
        })
        return actions

    # 2. Emergency: OI drops 5% more after entry (second wave)
    history = oi_history.get(asset, [])
    if history and len(history) >= 6:
        oi_since_entry = oi_change_pct(history, 5)
        if oi_since_entry is not None and oi_since_entry < -5:
            actions.append({
                "action": "close", "priority": "CRITICAL",
                "reason": f"SECOND_WAVE: OI dropped {oi_since_entry}% since entry",
                "roe": round(roe_pct, 2)
            })
            return actions

    # 3. Target hit (50% of cascade move recaptured)
    snapback_target = config.get("snapbackTargetPct", 50)
    target_price = pos.get("targetPrice")
    if target_price:
        if (direction == "LONG" and current_price >= target_price) or \
           (direction == "SHORT" and current_price <= target_price):
            actions.append({
                "action": "close", "priority": "HIGH",
                "reason": f"TARGET_HIT: {snapback_target}% snapback reached",
                "roe": round(roe_pct, 2)
            })
            return actions

    # 4. Trailing lock: 70% lock when at 50% of target ROE
    high_water_roe = pos.get("highWaterRoe", roe_pct)
    if roe_pct > high_water_roe:
        high_water_roe = roe_pct

    lock_threshold_roe = roe_pct * 0.5  # 50% of target
    if high_water_roe > lock_threshold_roe and high_water_roe > 1:
        floor_roe = high_water_roe * 0.70
        if roe_pct < floor_roe and roe_pct < high_water_roe:
            actions.append({
                "action": "close", "priority": "HIGH",
                "reason": f"TRAILING_LOCK: ROE {roe_pct:.1f}% < 70% of HW {high_water_roe:.1f}%",
                "roe": round(roe_pct, 2)
            })

    return actions


def evaluate_book_exit(asset, pos, config, current_price):
    """Evaluate exit for BOOK_IMBALANCE position."""
    actions = []
    entry_price = pos.get("entryPrice", current_price)
    direction = pos.get("direction", "LONG")
    opened_at = pos.get("openedAt")
    leverage = pos.get("leverage", 5)

    if direction == "LONG":
        roe_pct = (current_price - entry_price) / entry_price * 100 * leverage
    else:
        roe_pct = (entry_price - current_price) / entry_price * 100 * leverage

    # Time stop: 30 min
    elapsed_min = minutes_since(opened_at) if opened_at else 0
    if elapsed_min >= 30:
        actions.append({
            "action": "close", "priority": "CRITICAL",
            "reason": f"TIME_STOP: {elapsed_min:.0f}min elapsed, 30min limit",
            "roe": round(roe_pct, 2)
        })
        return actions

    # Target: return to pre-sweep level (entry price is post-sweep)
    pre_sweep_price = pos.get("preSweepPrice", entry_price)
    if pre_sweep_price:
        if (direction == "LONG" and current_price >= pre_sweep_price) or \
           (direction == "SHORT" and current_price <= pre_sweep_price):
            actions.append({
                "action": "close", "priority": "HIGH",
                "reason": "TARGET_HIT: Pre-sweep level reached",
                "roe": round(roe_pct, 2)
            })
            return actions

    # Trailing lock: 80% at 60% of target
    high_water_roe = pos.get("highWaterRoe", roe_pct)
    if roe_pct > high_water_roe:
        high_water_roe = roe_pct
    if high_water_roe > 1:
        floor_roe = high_water_roe * 0.80
        if roe_pct < floor_roe:
            actions.append({
                "action": "close", "priority": "HIGH",
                "reason": f"TRAILING_LOCK: ROE {roe_pct:.1f}% < 80% of HW {high_water_roe:.1f}%",
                "roe": round(roe_pct, 2)
            })

    return actions


def evaluate_squeeze_exit(asset, pos, config, instruments):
    """Evaluate exit for SQUEEZE position."""
    actions = []
    entry_price = pos.get("entryPrice", 0)
    direction = pos.get("direction", "LONG")
    opened_at = pos.get("openedAt")
    leverage = pos.get("leverage", 8)

    # Get current price from instruments
    current_price = 0
    for inst in instruments:
        if inst.get("name", inst.get("coin", "")) == asset:
            current_price = float(inst.get("markPx", inst.get("price", 0)))
            break
    if current_price == 0:
        return actions

    if direction == "LONG":
        roe_pct = (current_price - entry_price) / entry_price * 100 * leverage
        move_pct = (current_price - entry_price) / entry_price * 100
    else:
        roe_pct = (entry_price - current_price) / entry_price * 100 * leverage
        move_pct = (entry_price - current_price) / entry_price * 100

    # Time stop: 12 hours
    elapsed_h = hours_since(opened_at) if opened_at else 0
    if elapsed_h >= 12:
        actions.append({
            "action": "close", "priority": "CRITICAL",
            "reason": f"TIME_STOP: {elapsed_h:.1f}h elapsed, 12h limit",
            "roe": round(roe_pct, 2)
        })
        return actions

    # Emergency: funding normalizes
    for inst in instruments:
        if inst.get("name", inst.get("coin", "")) == asset:
            funding = inst.get("funding", inst.get("fundingRate"))
            if funding is not None:
                funding = abs(float(funding))
                normalize_threshold = 0.0003  # 0.03% per 8h
                if funding < normalize_threshold:
                    actions.append({
                        "action": "close", "priority": "HIGH",
                        "reason": f"FUNDING_NORMALIZED: {funding*100:.4f}% < 0.03%",
                        "roe": round(roe_pct, 2)
                    })
                    return actions
            break

    # Target: 3-5% move
    if move_pct >= 5:
        actions.append({
            "action": "close", "priority": "HIGH",
            "reason": f"TARGET_HIT: {move_pct:.1f}% move",
            "roe": round(roe_pct, 2)
        })
        return actions

    # Tiered trailing locks
    high_water_roe = pos.get("highWaterRoe", roe_pct)
    if roe_pct > high_water_roe:
        high_water_roe = roe_pct

    # 50% lock at 2% move, 70% lock at 3% move
    if move_pct >= 3 and high_water_roe > 0:
        floor_roe = high_water_roe * 0.70
        if roe_pct < floor_roe:
            actions.append({
                "action": "close", "priority": "HIGH",
                "reason": f"TRAILING_70: ROE {roe_pct:.1f}% < floor {floor_roe:.1f}%",
                "roe": round(roe_pct, 2)
            })
    elif move_pct >= 2 and high_water_roe > 0:
        floor_roe = high_water_roe * 0.50
        if roe_pct < floor_roe:
            actions.append({
                "action": "close", "priority": "MEDIUM",
                "reason": f"TRAILING_50: ROE {roe_pct:.1f}% < floor {floor_roe:.1f}%",
                "roe": round(roe_pct, 2)
            })

    return actions


def main():
    try:
        config = load_config()
        state = load_state(config)
    except Exception as e:
        output_error(f"config_load_failed: {e}")

    positions = state.get("activePositions", {})
    if not positions:
        output_heartbeat()
        return

    oi_history = load_oi_history()
    instruments = get_all_instruments()

    all_actions = []

    for asset, pos in positions.items():
        pattern = pos.get("pattern", "")
        current_price = 0
        for inst in instruments:
            if inst.get("name", inst.get("coin", "")) == asset:
                current_price = float(inst.get("markPx", inst.get("price", 0)))
                break

        if pattern == "CASCADE_REVERSAL":
            actions = evaluate_cascade_exit(asset, pos, config, oi_history, current_price)
        elif pattern == "BOOK_IMBALANCE":
            actions = evaluate_book_exit(asset, pos, config, current_price)
        elif pattern == "SQUEEZE":
            actions = evaluate_squeeze_exit(asset, pos, config, instruments)
        else:
            actions = []

        # Update high water ROE
        if current_price > 0 and pos.get("entryPrice"):
            entry = pos["entryPrice"]
            lev = pos.get("leverage", 6)
            if pos.get("direction") == "LONG":
                roe = (current_price - entry) / entry * 100 * lev
            else:
                roe = (entry - current_price) / entry * 100 * lev
            pos["highWaterRoe"] = max(pos.get("highWaterRoe", 0), roe)
            pos["currentRoe"] = round(roe, 2)

        for a in actions:
            a["asset"] = asset
            a["pattern"] = pattern
            all_actions.append(a)

    # Sort by priority
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    all_actions.sort(key=lambda a: priority_order.get(a.get("priority"), 3))

    save_state(config, state)

    if not all_actions:
        output_heartbeat()
        return

    output({
        "success": True,
        "actions": all_actions,
        "summary": f"{len(all_actions)} exit signals"
    })


if __name__ == "__main__":
    main()
