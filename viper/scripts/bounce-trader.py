#!/usr/bin/env python3
"""
bounce-trader.py — VIPER bounce execution engine.
Runs every 2 min. Monitors price relative to mapped boundaries,
posts limit orders at support/resistance. Tier 2 cron.

MANDATE: If price in entry zone + slots available → post limit order at boundary.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from viper_config import (
    load_config, load_state, save_state, load_range,
    get_asset_data, now_utc, hours_since,
    output, output_heartbeat, output_error
)
from viper_lib import (
    parse_candles, is_in_zone, is_in_dead_zone,
    get_leverage, get_position_size_pct
)


def evaluate_asset(asset, rng, range_data, state, config):
    """Evaluate one asset for bounce entry opportunity."""
    if rng.get("phase") != "TRADING":
        return None
    if rng.get("breakWarning"):
        return None
    if rng.get("rangeHealth") == "AGING":
        bounces = rng.get("bouncesTraded", 0)
        max_bounces = config.get("maxBouncesPerRange", 8)
        if bounces >= max_bounces:
            return None

    # Already have a position on this asset?
    if asset in state.get("activePositions", {}):
        return None

    # Already have a pending order?
    pending = state.get("pendingOrders", {}).get(asset)
    if pending:
        # Check if stale (>15 min)
        posted_at = pending.get("postedAt")
        if posted_at and hours_since(posted_at) * 60 < 15:
            return None  # Still fresh
        else:
            return {"action": "CANCEL_REPOST", "asset": asset}

    # Get current price
    try:
        data = get_asset_data(asset, intervals=["1h"], include_book=False)
        asset_data = data.get("data", data)
        candles = asset_data.get("candles", {}).get("1h", [])
        if not candles:
            return None
        _, _, _, closes, _ = parse_candles(candles)
        current_price = closes[-1]
    except Exception:
        return None

    sup_level = range_data.get("supportLevel")
    res_level = range_data.get("resistanceLevel")
    sup_zone = range_data.get("supportZone")
    res_zone = range_data.get("resistanceZone")

    if not sup_level or not res_level:
        return None

    # Dead zone check
    if is_in_dead_zone(current_price, sup_level, res_level):
        return None

    # Determine entry direction and price
    direction = None
    entry_price = None

    if sup_zone and is_in_zone(current_price, sup_zone):
        direction = "LONG"
        entry_price = sup_level
    elif res_zone and is_in_zone(current_price, res_zone):
        direction = "SHORT"
        entry_price = res_level

    if direction is None:
        return None

    # Check slots
    max_slots = config.get("maxSlots", 2)
    active_count = len(state.get("activePositions", {}))
    if active_count >= max_slots:
        return None

    # Check daily trade limit
    max_daily = config.get("maxDailyTrades", 6)
    trades_today = state.get("safety", {}).get("tradesToday", 0)
    if trades_today >= max_daily:
        return None

    # Sizing
    range_width = range_data.get("rangeWidthPct", 3)
    score = rng.get("rangeScore", 0.65)
    size_pct = get_position_size_pct(range_width, score, config)

    # Bounce aging reduction
    bounces = rng.get("bouncesTraded", 0)
    if 5 <= bounces <= 6:
        reduce = config.get("agingReducePct5_6", 25)
        size_pct = size_pct * (100 - reduce) / 100
    elif 7 <= bounces <= 8:
        reduce = config.get("agingReducePct7_8", 50)
        size_pct = size_pct * (100 - reduce) / 100

    leverage = get_leverage(range_width, config)

    # Calculate TP and stop
    atr_4h = range_data.get("atr4h", abs(res_level - sup_level) * 0.1)
    tp_buffer_pct = config.get("tpBufferPct", 5) / 100

    if direction == "LONG":
        tp_price = res_level - (res_level - sup_level) * tp_buffer_pct
        stop_price = sup_level - 0.5 * atr_4h
    else:
        tp_price = sup_level + (res_level - sup_level) * tp_buffer_pct
        stop_price = res_level + 0.5 * atr_4h

    return {
        "action": "ENTER",
        "asset": asset,
        "direction": direction,
        "entryPrice": round(entry_price, 6),
        "tpPrice": round(tp_price, 6),
        "stopPrice": round(stop_price, 6),
        "leverage": leverage,
        "sizePct": round(size_pct, 1),
        "bounceNumber": bounces + 1,
        "rangeScore": score,
    }


def main():
    try:
        config = load_config()
        state = load_state(config)
    except Exception as e:
        output_error(f"config_load_failed: {e}")

    if state.get("safety", {}).get("halted"):
        output_heartbeat()
        return

    ranges = state.get("ranges", {})
    trading_ranges = {k: v for k, v in ranges.items() if v.get("phase") == "TRADING"}

    if not trading_ranges:
        output_heartbeat()
        return

    signals = []
    for asset, rng in trading_ranges.items():
        range_data = load_range(config, asset)
        result = evaluate_asset(asset, rng, range_data, state, config)
        if result:
            signals.append(result)

    if not signals:
        output_heartbeat()
        return

    # Build action plan (per guide §2.5 — plan first, execute in caller)
    entries = [s for s in signals if s["action"] == "ENTER"]
    cancels = [s for s in signals if s["action"] == "CANCEL_REPOST"]

    output({
        "success": True,
        "signals": signals,
        "entries": entries,
        "cancels": cancels,
        "summary": f"{len(entries)} entries, {len(cancels)} cancels"
    })


if __name__ == "__main__":
    main()
