#!/usr/bin/env python3
"""
oi-tracker.py — OI history sampler for TIGER.
Runs every 5min. Samples OI for all liquid assets and stores locally.
Also tracks OI for active positions more granularly.

MANDATE: Run TIGER OI tracker. Sample current OI for all assets. Store history.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, get_all_instruments,
    append_oi_snapshot, load_oi_history, output
)


def main():
    config = load_config()
    state = load_state()

    instruments = get_all_instruments()
    if not instruments:
        output({"error": "Failed to fetch instruments"})
        return

    active_coins = set(state.get("active_positions", {}).keys())
    sampled = 0
    tracked_assets = set()

    for inst in instruments:
        name = inst.get("name", "")
        if inst.get("is_delisted"):
            continue

        ctx = inst.get("context", {})
        oi = float(ctx.get("openInterest", 0))
        price = float(ctx.get("midPx", 0) or ctx.get("markPx", 0))
        day_vol = float(ctx.get("dayNtlVlm", 0))

        # Always track: active positions, BTC, ETH, and liquid assets
        should_track = (
            name in active_coins or
            name in ("BTC", "ETH") or
            day_vol > 5_000_000 or
            (inst.get("max_leverage", 0) >= config.get("min_leverage", 7) and day_vol > 1_000_000)
        )

        if should_track and oi > 0 and price > 0:
            append_oi_snapshot(name, oi, price)
            tracked_assets.add(name)
            sampled += 1

    # Report
    oi_history = load_oi_history()
    history_stats = {}
    for asset in list(active_coins) + ["BTC", "ETH"]:
        if asset in oi_history:
            entries = oi_history[asset]
            history_stats[asset] = {
                "entries": len(entries),
                "latest_oi": entries[-1]["oi"] if entries else 0,
                "oldest_ts": entries[0]["ts"] if entries else 0
            }

    output({
        "action": "oi_track",
        "sampled": sampled,
        "total_tracked_assets": len(tracked_assets),
        "active_positions_tracked": list(active_coins & tracked_assets),
        "history_stats": history_stats
    })


if __name__ == "__main__":
    main()
