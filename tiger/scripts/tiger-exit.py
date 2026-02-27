#!/usr/bin/env python3
"""
tiger-exit.py — Target-aware exit management for TIGER.
Runs every 5min alongside risk guardian. Manages trailing exits based on
goal progress, not fixed ROE tiers like WOLF.

MANDATE: Run TIGER exit manager. Check positions for target-aware exits. Report actions needed.
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, save_state, get_clearinghouse,
    days_remaining, output
)


def evaluate_position(pos_data: dict, active_pos: dict, config: dict, state: dict) -> dict:
    """Evaluate a single position for exit signals."""
    coin = pos_data.get("coin", "")
    entry_price = float(pos_data.get("entryPx", 0))
    current_price = float(pos_data.get("positionValue", 0))  # Will recalc from mark
    unrealized_pnl = float(pos_data.get("unrealizedPnl", 0))
    margin = float(pos_data.get("marginUsed", 0))
    size = float(pos_data.get("szi", 0))
    leverage = float(pos_data.get("leverage", {}).get("value", 1) if isinstance(pos_data.get("leverage"), dict) else pos_data.get("leverage", 1))

    if margin <= 0:
        return None

    roe_pct = (unrealized_pnl / margin) * 100
    is_long = size > 0

    # Get tracked state for this position
    tracked = active_pos.get(coin, {})
    high_water_roe = max(tracked.get("high_water_roe", roe_pct), roe_pct)
    opened_at = tracked.get("opened_at", "")
    pattern = tracked.get("pattern", "unknown")

    # Update high water mark
    if coin in active_pos:
        active_pos[coin]["high_water_roe"] = high_water_roe

    # ─── Exit Logic ───

    aggression = state.get("aggression", "NORMAL")
    remaining = state.get("days_remaining", 7)
    daily_rate = state.get("daily_rate_needed", 10)

    # Calculate daily target in USD
    daily_target_usd = state["current_balance"] * (daily_rate / 100) if daily_rate else 0

    actions = []

    # 0. FALSE BREAKOUT CHECK (Compression Breakout only)
    # If price re-entered BB range within 2 candles of breakout, cut immediately
    if pattern == "COMPRESSION_BREAKOUT" and tracked.get("breakout_candle_index"):
        candles_since_breakout = tracked.get("candles_since_breakout", 0)
        bb_reentry = tracked.get("bb_reentry", False)
        if candles_since_breakout <= 2 and bb_reentry and roe_pct < 3:
            actions.append({
                "type": "FALSE_BREAKOUT",
                "action": "CLOSE",
                "reason": f"Price re-entered BB range within {candles_since_breakout} candles — false breakout. Cut immediately.",
                "priority": "HIGH"
            })

    # 1. Daily target hit → take profit
    if unrealized_pnl > 0 and daily_target_usd > 0 and unrealized_pnl >= daily_target_usd:
        actions.append({
            "type": "DAILY_TARGET_HIT",
            "action": "CLOSE",
            "reason": f"Position +${unrealized_pnl:.2f} hit daily target ${daily_target_usd:.2f}. Take profit.",
            "priority": "HIGH"
        })

    # 2. Trailing lock based on aggression
    lock_pct_map = config.get("trailing_lock_pct", {})
    lock_pct = lock_pct_map.get(aggression, 0.60)

    # Deadline override: last day = 85%+ lock
    if remaining <= 1:
        lock_pct = max(lock_pct, 0.85)
    elif remaining <= 2:
        lock_pct = max(lock_pct, 0.75)

    # Trailing stop: if ROE dropped below lock% of high water
    if high_water_roe > 5 and roe_pct > 0:
        locked_level = high_water_roe * lock_pct
        if roe_pct < locked_level:
            actions.append({
                "type": "TRAILING_LOCK",
                "action": "CLOSE",
                "reason": f"ROE {roe_pct:.1f}% fell below {lock_pct*100:.0f}% lock of peak {high_water_roe:.1f}% (floor: {locked_level:.1f}%)",
                "priority": "HIGH"
            })

    # 3. Ahead of pace + positive = conservative take at 50%+ daily target
    if aggression == "CONSERVATIVE" and unrealized_pnl > daily_target_usd * 0.5:
        actions.append({
            "type": "CONSERVATIVE_TP",
            "action": "PARTIAL_75",
            "reason": f"Ahead of pace, take 75% of +${unrealized_pnl:.2f} position",
            "priority": "MEDIUM"
        })

    # 4. Stagnation: positive ROE but no new high in 2+ hours
    # (simplified: if high_water hasn't changed significantly and we're in profit)
    if roe_pct > 3 and abs(high_water_roe - roe_pct) < 1 and tracked.get("stagnant_checks", 0) >= 24:
        actions.append({
            "type": "STAGNATION",
            "action": "CLOSE",
            "reason": f"ROE stagnant at ~{roe_pct:.1f}% for 2+ hours. Take profit and rotate.",
            "priority": "MEDIUM"
        })

    # Track stagnation
    if coin in active_pos:
        prev_hw = tracked.get("prev_high_water", 0)
        if abs(high_water_roe - prev_hw) < 0.5:
            active_pos[coin]["stagnant_checks"] = tracked.get("stagnant_checks", 0) + 1
        else:
            active_pos[coin]["stagnant_checks"] = 0
        active_pos[coin]["prev_high_water"] = high_water_roe

    # 5. Losing position past time limit (30 min + negative = cut)
    if opened_at and roe_pct < -2:
        try:
            from datetime import datetime, timezone
            opened = datetime.fromisoformat(opened_at)
            elapsed_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
            if elapsed_min > 30 and roe_pct < 0:
                actions.append({
                    "type": "TIME_STOP",
                    "action": "CLOSE",
                    "reason": f"Negative ROE ({roe_pct:.1f}%) after {elapsed_min:.0f}min. Cut loss.",
                    "priority": "MEDIUM"
                })
        except Exception:
            pass

    # 6. Deadline close: Day 7+ = close everything
    if remaining <= 0:
        actions.append({
            "type": "DEADLINE",
            "action": "CLOSE",
            "reason": "Deadline reached. Closing all positions.",
            "priority": "CRITICAL"
        })

    if not actions:
        return None

    # Return highest priority action
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    actions.sort(key=lambda a: priority_order.get(a.get("priority", "LOW"), 3))

    return {
        "coin": coin,
        "roe_pct": round(roe_pct, 1),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "high_water_roe": round(high_water_roe, 1),
        "lock_pct": lock_pct,
        "pattern": pattern,
        "actions": actions,
        "primary_action": actions[0]
    }


def main():
    config = load_config()
    state = load_state()

    if not config.get("strategy_wallet"):
        output({"error": "TIGER not set up."})
        return

    wallet = config["strategy_wallet"]
    ch = get_clearinghouse(wallet)
    if ch.get("error"):
        output({"error": f"Clearinghouse failed: {ch['error']}"})
        return

    ch_data = ch.get("data", ch)
    positions = ch_data.get("assetPositions", [])

    active_pos = state.get("active_positions", {})
    exit_signals = []

    for p in positions:
        pos = p.get("position", p)
        coin = pos.get("coin", "")
        result = evaluate_position(pos, active_pos, config, state)
        if result:
            exit_signals.append(result)

    # Update state with high water marks
    state["active_positions"] = active_pos
    save_state(state)

    # Categorize actions
    close_needed = [e for e in exit_signals if e["primary_action"]["action"] == "CLOSE"]
    partial_needed = [e for e in exit_signals if "PARTIAL" in e["primary_action"]["action"]]

    output({
        "action": "exit_check",
        "positions_checked": len(positions),
        "exit_signals": len(exit_signals),
        "close_needed": [{"coin": e["coin"], "reason": e["primary_action"]["reason"]} for e in close_needed],
        "partial_needed": [{"coin": e["coin"], "reason": e["primary_action"]["reason"]} for e in partial_needed],
        "all_signals": exit_signals,
        "aggression": state.get("aggression", "NORMAL"),
        "days_remaining": round(state.get("days_remaining", 7), 1)
    })


if __name__ == "__main__":
    main()
