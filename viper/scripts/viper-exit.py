#!/usr/bin/env python3
"""
viper-exit.py — VIPER exit management.
Runs every 5 min. Manages trailing locks, bounce aging, time stops, PnL guards.
Tier 2 cron.

MANDATE: Process exit signals by priority. Apply trailing, aging, time stop rules.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from viper_config import (
    load_config, load_state, save_state, load_range, log_trade,
    get_clearinghouse, now_utc, hours_since,
    output, output_heartbeat, output_error
)


def evaluate_position(coin, pos, state, config):
    """Evaluate one position for exit signals."""
    actions = []
    current_balance = state.get("startingEquity", 5000)

    # We need actual PnL from clearinghouse — this script expects
    # the agent to have fetched it and stored unrealizedPnl in state
    # For now, work with what's in state
    entry_price = pos.get("entryPrice", 0)
    stop_price = pos.get("stopPrice", 0)
    tp_price = pos.get("tpPrice", 0)
    opened_at = pos.get("openedAt")
    bounce_number = pos.get("bounceNumber", 1)
    trailing_lock = pos.get("trailingLock", 0)
    high_water_pnl = pos.get("highWaterPnl", 0)
    direction = pos.get("direction", "LONG")
    range_asset = pos.get("rangeAsset", coin)
    range_data = load_range(config, range_asset) if range_asset else {}

    range_width = range_data.get("rangeWidthPct", 3)
    hold_hours = hours_since(opened_at) if opened_at else 0

    # 1. Time stop
    if range_width < 3:
        max_hours = config.get("timeStopNarrowHours", 24)
    else:
        max_hours = config.get("timeStopWideHours", 36)

    if hold_hours >= max_hours:
        actions.append({
            "type": "TIME_STOP",
            "coin": coin,
            "action": "CLOSE",
            "priority": "HIGH",
            "reason": f"Position open {hold_hours:.1f}h (limit {max_hours}h). Force close."
        })
        return actions

    # 2. PnL guard — daily loss check
    daily_loss = state.get("safety", {}).get("dailyLossPct", 0)
    max_daily = config.get("maxDailyLossPct", 6)
    if daily_loss >= max_daily:
        actions.append({
            "type": "DAILY_LOSS",
            "coin": coin,
            "action": "CLOSE",
            "priority": "CRITICAL",
            "reason": f"Daily loss {daily_loss}% hit limit {max_daily}%. Close all."
        })
        return actions

    # 3. Consecutive stops check
    consecutive = state.get("safety", {}).get("consecutiveStopsPerRange", {}).get(range_asset, 0)
    max_consecutive = config.get("maxConsecutiveStops", 3)
    if consecutive >= max_consecutive:
        actions.append({
            "type": "CONSECUTIVE_STOPS",
            "coin": coin,
            "action": "CLOSE",
            "priority": "HIGH",
            "reason": f"{consecutive} consecutive stops on {range_asset}. Range is broken."
        })
        return actions

    # 4. Mid-range TP check
    if hold_hours > 12 or bounce_number > 4:
        actions.append({
            "type": "MID_RANGE_TP",
            "coin": coin,
            "action": "PARTIAL",
            "priority": "MEDIUM",
            "reason": f"Hold {hold_hours:.1f}h, bounce #{bounce_number}. Take 50% at midpoint."
        })

    # 5. Range weakening check
    rng = state.get("ranges", {}).get(range_asset, {})
    if rng.get("rangeHealth") == "WEAKENING":
        actions.append({
            "type": "RANGE_WEAKENING",
            "coin": coin,
            "action": "TIGHTEN",
            "priority": "MEDIUM",
            "reason": f"{range_asset} range weakening. Tighten stop to midpoint."
        })

    # 6. Break warning
    if rng.get("breakWarning"):
        actions.append({
            "type": "BREAK_WARNING",
            "coin": coin,
            "action": "TIGHTEN",
            "priority": "HIGH",
            "reason": f"{range_asset} break warning. Tighten to breakeven."
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
        # Also check drawdown even with no positions
        output_heartbeat()
        return

    all_actions = []
    for coin, pos in positions.items():
        actions = evaluate_position(coin, pos, state, config)
        all_actions.extend(actions)

    if not all_actions:
        output_heartbeat()
        return

    # Sort by priority
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    all_actions.sort(key=lambda a: priority_order.get(a.get("priority", "MEDIUM"), 2))

    closes = [a for a in all_actions if a.get("action") == "CLOSE"]
    partials = [a for a in all_actions if a.get("action") == "PARTIAL"]
    tightens = [a for a in all_actions if a.get("action") == "TIGHTEN"]

    save_state(config, state)

    output({
        "success": True,
        "actions": all_actions,
        "closes": [a["coin"] for a in closes],
        "partials": [a["coin"] for a in partials],
        "tightens": [a["coin"] for a in tightens],
        "summary": f"{len(closes)} close, {len(partials)} partial, {len(tightens)} tighten"
    })


if __name__ == "__main__":
    main()
