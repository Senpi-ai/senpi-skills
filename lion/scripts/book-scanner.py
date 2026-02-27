#!/usr/bin/env python3
"""
book-scanner.py — LION L2 order book imbalance detection.
Runs every 30s. Finds persistent bid/ask imbalances for fade entries. Tier 1 cron.

Checks top 5 L2 levels for 3:1+ imbalance, persistence via state tracking.
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from lion_config import (
    load_config, load_state, save_state, get_asset_data,
    get_all_instruments, now_utc,
    output, output_heartbeat, output_error
)
from lion_lib import compute_book_imbalance, is_imbalance_near_price


def main():
    try:
        config = load_config()
        state = load_state(config)
    except Exception as e:
        output_error(f"config_load_failed: {e}")

    if state.get("safety", {}).get("halted"):
        output_heartbeat()
        return

    # Check slots and daily limits
    max_slots = config.get("maxSlots", 2)
    active_count = len(state.get("activePositions", {}))
    if active_count >= max_slots:
        output_heartbeat()
        return

    max_daily = config.get("maxDailyTrades", 4)
    trades_today = state.get("safety", {}).get("tradesToday", 0)
    if trades_today >= max_daily:
        output_heartbeat()
        return

    # Get liquid assets
    instruments = get_all_instruments()
    if not instruments:
        output_heartbeat()
        return

    min_volume = config.get("bookMinDailyVolume", 20000000)
    liquid_assets = []
    for inst in instruments:
        vol = float(inst.get("dayNtlVlm", inst.get("volume24h", 0)))
        if vol >= min_volume:
            liquid_assets.append(inst.get("name", inst.get("coin", "")))

    # Don't scan assets we already have positions in
    active_assets = set(state.get("activePositions", {}).keys())
    scan_assets = [a for a in liquid_assets if a not in active_assets][:15]  # Limit to 15

    imbalance_ratio_threshold = config.get("bookImbalanceRatio", 3)
    persistence_required = config.get("bookPersistenceChecks", 2)
    proximity_bps = config.get("bookProximityPct", 50)

    # Load previous imbalance tracking from state
    book_watch = state.get("watchlist", {}).get("bookImbalances", {})

    signals = []
    updated_watch = {}

    for asset in scan_assets:
        result = get_asset_data(asset, intervals=[], include_book=True)
        if not result or result.get("error"):
            continue

        data = result.get("data", result)
        order_book = data.get("orderBook", data.get("order_book", {}))
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        if not bids or not asks:
            continue

        # Compute imbalance
        ratio, bid_depth, ask_depth, thick_side = compute_book_imbalance(bids, asks, levels=5)

        # Check if imbalance exceeds threshold
        effective_ratio = max(ratio, 1 / ratio) if ratio > 0 else 1
        if effective_ratio < imbalance_ratio_threshold:
            continue

        # Check proximity to current price
        current_price = float(data.get("markPrice", data.get("price", 0)))
        if current_price == 0:
            continue

        near_price = is_imbalance_near_price(bids, asks, current_price, proximity_bps)
        if not near_price:
            continue

        # Track persistence
        prev = book_watch.get(asset, {})
        prev_count = prev.get("count", 0)
        prev_side = prev.get("thickSide")

        if thick_side == prev_side:
            new_count = prev_count + 1
        else:
            new_count = 1

        updated_watch[asset] = {
            "thickSide": thick_side,
            "ratio": effective_ratio,
            "count": new_count,
            "lastSeen": now_utc().isoformat(),
        }

        # Actionable if persistent
        if new_count >= persistence_required:
            # Fade the thick side: if bids are thick, price likely to drop → SHORT
            # If asks are thick, price likely to rise → LONG
            # Wait — imbalance fade means the thick side gets swept then reverts
            # Thick bids = buyers → sweep down (liq thin asks) → snap back up → LONG after sweep
            fade_direction = "SHORT" if thick_side == "BID" else "LONG"

            size_pct = config.get("sizingBookImbalance", 8)
            leverage = 5

            signals.append({
                "asset": asset,
                "actionable": True,
                "pattern": "BOOK_IMBALANCE",
                "thickSide": thick_side,
                "ratio": effective_ratio,
                "bidDepth": round(bid_depth, 2),
                "askDepth": round(ask_depth, 2),
                "persistenceCount": new_count,
                "entryDirection": fade_direction,
                "sizePct": size_pct,
                "leverage": leverage,
                "currentPrice": current_price,
            })

    # Save book watch state
    state.setdefault("watchlist", {})["bookImbalances"] = updated_watch
    save_state(config, state)

    if not signals:
        output_heartbeat()
        return

    output({
        "success": True,
        "signals": signals,
        "summary": f"{len(signals)} book imbalance signals"
    })


if __name__ == "__main__":
    main()
