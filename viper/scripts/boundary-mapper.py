#!/usr/bin/env python3
"""
boundary-mapper.py — VIPER boundary mapping.
Runs every 1 hour. Maps support/resistance zones for VALIDATING ranges,
refreshes boundaries for TRADING ranges. Tier 1 cron.

MANDATE: Advance validated ranges to TRADING. Refresh existing boundaries.
"""

import sys
import os
from datetime import timezone
sys.path.insert(0, os.path.dirname(__file__))

from viper_config import (
    load_config, load_state, save_state, load_range, save_range,
    get_asset_data, get_orderbook, now_utc,
    output, output_heartbeat, output_error
)
from viper_lib import (
    parse_candles, find_support_resistance, count_touches, atr as compute_atr
)


def map_boundaries(asset, config):
    """Map support/resistance zones from 1h candle data."""
    try:
        data = get_asset_data(asset, intervals=["1h", "4h"], include_book=False)
    except Exception as e:
        return None, f"fetch_failed: {e}"

    asset_data = data.get("data", data)
    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])

    if len(candles_1h) < 48:
        return None, "insufficient_1h_candles"

    opens, highs, lows, closes, volumes = parse_candles(candles_1h)

    # Find S/R from 1h wicks (last 48 bars = 48 hours)
    sup_level, sup_zone, res_level, res_zone = find_support_resistance(
        highs, lows, closes, min(48, len(highs))
    )

    if sup_level is None or res_level is None:
        return None, "no_clear_boundaries"

    # Validate zones don't overlap
    if sup_zone and res_zone and sup_zone[1] >= res_zone[0]:
        return None, "zones_overlap"

    # Range width check
    range_width = ((res_level - sup_level) / sup_level) * 100
    min_width = config.get("minRangeWidthPct", 2)
    if range_width < min_width:
        return None, f"range_too_narrow: {range_width:.1f}%"

    # Compute ATR from 4h for stop placement
    if candles_4h:
        _, h4, l4, c4, _ = parse_candles(candles_4h)
        atr_vals = compute_atr(h4, l4, c4, 14)
        current_atr = atr_vals[-1] if atr_vals[-1] is not None else (res_level - sup_level) * 0.1
    else:
        current_atr = (res_level - sup_level) * 0.1

    # Touch counts
    sup_touches = count_touches(lows[-48:], sup_level, 0.3)
    res_touches = count_touches(highs[-48:], res_level, 0.3)

    # Dead zone (middle 40%)
    range_size = res_level - sup_level
    dead_low = sup_level + range_size * 0.30
    dead_high = res_level - range_size * 0.30

    midpoint = (sup_level + res_level) / 2

    return {
        "supportLevel": round(sup_level, 6),
        "supportZone": [round(sup_zone[0], 6), round(sup_zone[1], 6)] if sup_zone else None,
        "resistanceLevel": round(res_level, 6),
        "resistanceZone": [round(res_zone[0], 6), round(res_zone[1], 6)] if res_zone else None,
        "rangeWidthPct": round(range_width, 2),
        "rangeMidpoint": round(midpoint, 6),
        "deadZone": [round(dead_low, 6), round(dead_high, 6)],
        "touchesSupport": sup_touches,
        "touchesResistance": res_touches,
        "atr4h": round(current_atr, 6),
        "currentPrice": round(closes[-1], 6),
    }, None


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
    if not ranges:
        output_heartbeat()
        return

    phase_changes = []
    refreshed = []
    removed = []

    for asset, rng in list(ranges.items()):
        phase = rng.get("phase", "VALIDATING")

        if phase in ("BROKEN", "COOLDOWN"):
            continue

        boundaries, error = map_boundaries(asset, config)

        if boundaries is None:
            if phase == "VALIDATING":
                del ranges[asset]
                removed.append(f"{asset}: {error}")
            continue

        # Update range state file
        range_data = load_range(config, asset)
        range_data.update(boundaries)

        if phase == "VALIDATING":
            min_touches = config.get("minTouchesPerSide", 3)
            if (boundaries["touchesSupport"] >= min_touches and
                    boundaries["touchesResistance"] >= min_touches):
                rng["phase"] = "TRADING"
                rng["confirmedAt"] = now_utc().isoformat()
                phase_changes.append({
                    "asset": asset,
                    "rangeWidthPct": boundaries["rangeWidthPct"],
                    "support": boundaries["supportLevel"],
                    "resistance": boundaries["resistanceLevel"],
                    "touchesSupport": boundaries["touchesSupport"],
                    "touchesResistance": boundaries["touchesResistance"],
                })
            else:
                removed.append(f"{asset}: insufficient touches ({boundaries['touchesSupport']}S/{boundaries['touchesResistance']}R)")
                del ranges[asset]
                continue

        elif phase == "TRADING":
            # Refresh boundaries
            rng["adxCurrent"] = rng.get("adxCurrent", 20)
            refreshed.append(asset)

            # Check bounce aging
            bounces = rng.get("bouncesTraded", 0)
            max_bounces = config.get("maxBouncesPerRange", 8)
            if bounces >= max_bounces:
                rng["rangeHealth"] = "AGING"

        save_range(config, asset, range_data)

    state["ranges"] = ranges
    save_state(config, state)

    if not phase_changes and not removed:
        output_heartbeat()
        return

    output({
        "success": True,
        "phaseChanges": phase_changes,
        "refreshed": refreshed,
        "removed": removed,
        "summary": f"{len(phase_changes)} confirmed, {len(refreshed)} refreshed, {len(removed)} removed"
    })


if __name__ == "__main__":
    main()
