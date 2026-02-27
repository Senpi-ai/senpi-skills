#!/usr/bin/env python3
"""
oi-monitor.py — LION OI data collection.
Runs every 60s. Samples OI, price, volume, funding for all liquid assets.
Stores in shared history/oi-history.json. Tier 1 cron.

MANDATE: Data collection only — no trading actions.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from lion_config import (
    load_config, get_all_instruments, append_oi_snapshot,
    output_heartbeat, output_error, output
)

VERBOSE = os.environ.get("LION_VERBOSE") == "1"


def main():
    try:
        config = load_config()
    except Exception as e:
        output_error(f"config_load_failed: {e}")

    min_vol = config.get("minDailyVolume", 5000000)

    try:
        instruments = get_all_instruments()
    except Exception as e:
        output_error(f"instruments_fetch_failed: {e}", actionable=True)

    if not instruments:
        output_error("no_instruments_returned")

    sampled = 0
    errors = 0

    for inst in instruments:
        if inst.get("is_delisted"):
            continue

        asset = inst.get("name", "")
        ctx = inst.get("context", {})
        vol = float(ctx.get("dayNtlVlm", 0))
        if vol < min_vol:
            continue

        try:
            oi = float(ctx.get("openInterest", ctx.get("oi", 0)))
            price = float(ctx.get("markPx", ctx.get("midPx", 0)))
            funding = float(ctx.get("funding", 0))
            # volume15m is approximated from daily volume / 96 (fifteen-min slices)
            vol_15m = vol / 96

            append_oi_snapshot(asset, oi, price, vol_15m, funding,
                             max_entries=config.get("oiHistoryMaxEntries", 240))
            sampled += 1
        except Exception:
            errors += 1
            continue

    if sampled == 0:
        output_error("no_assets_sampled", actionable=True)

    if VERBOSE:
        output({"success": True, "sampled": sampled, "errors": errors})
    else:
        output_heartbeat()


if __name__ == "__main__":
    main()
