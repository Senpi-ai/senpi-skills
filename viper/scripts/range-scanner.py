#!/usr/bin/env python3
"""
range-scanner.py — VIPER range detection scanner.
Runs every 15 min. Scans top 30 assets by volume for range-bound conditions.
Outputs range candidates with scores. Tier 1 cron.

MANDATE: Data collection + threshold scoring. No trading actions.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from viper_config import (
    load_config, load_state, save_state,
    get_all_instruments, get_asset_data,
    output, output_heartbeat, output_error
)
from viper_lib import (
    parse_candles, adx, bollinger_bands, bb_width_percentile,
    find_support_resistance, count_touches, range_score
)

VERBOSE = os.environ.get("VIPER_VERBOSE") == "1"


def scan_asset(asset, candles_4h, config):
    """Evaluate one asset for range-bound conditions."""
    opens, highs, lows, closes, volumes = parse_candles(candles_4h)
    if len(closes) < 30:
        return None

    # ADX(14)
    adx_vals, _, _ = adx(highs, lows, closes, 14)
    current_adx = adx_vals[-1] if adx_vals[-1] is not None else 99

    # Check ADX < threshold
    adx_threshold = config.get("adxThreshold", 20)
    if current_adx >= adx_threshold:
        return None

    # Check ADX has been below threshold for ~12h (3 bars on 4h)
    adx_min_bars = config.get("adxMinHours", 12) // 4
    recent_adx = [a for a in adx_vals[-adx_min_bars:] if a is not None]
    if not recent_adx or any(a >= adx_threshold for a in recent_adx):
        return None

    # BB width percentile
    _, _, _, bb_width = bollinger_bands(closes, 20, 2)
    bb_pctl = bb_width_percentile(bb_width, 100)
    bb_threshold = config.get("bbWidthPercentile", 30)
    if bb_pctl is None or bb_pctl >= bb_threshold:
        return None

    # Support / Resistance detection
    sup_level, sup_zone, res_level, res_zone = find_support_resistance(highs, lows, closes, 48)
    if sup_level is None or res_level is None:
        return None

    # Range width check
    range_width = ((res_level - sup_level) / sup_level) * 100
    min_width = config.get("minRangeWidthPct", 2)
    if range_width < min_width:
        return None

    # Touch counts
    min_touches = config.get("minTouchesPerSide", 3)
    sup_touches = count_touches(lows[-48:], sup_level, 0.3)
    res_touches = count_touches(highs[-48:], res_level, 0.3)
    if sup_touches < min_touches or res_touches < min_touches:
        return None

    # Score
    score = range_score(current_adx, bb_pctl, sup_touches, res_touches, range_width)
    min_score = config.get("minRangeScore", 60) / 100

    if score < min_score:
        return None

    result = {
        "asset": asset,
        "rangeScore": score,
        "adx": round(current_adx, 1),
        "bbWidthPercentile": bb_pctl,
        "supportLevel": round(sup_level, 4),
        "resistanceLevel": round(res_level, 4),
        "rangeWidthPct": round(range_width, 2),
        "touchesSupport": sup_touches,
        "touchesResistance": res_touches,
    }
    if VERBOSE:
        result["supportZone"] = [round(z, 4) for z in sup_zone] if sup_zone else None
        result["resistanceZone"] = [round(z, 4) for z in res_zone] if res_zone else None

    return result


def main():
    try:
        config = load_config()
        state = load_state(config)
    except Exception as e:
        output_error(f"config_load_failed: {e}")

    if state.get("safety", {}).get("halted"):
        output_heartbeat()
        return

    # Fetch instruments
    try:
        instruments = get_all_instruments()
    except Exception as e:
        output_error(f"instruments_fetch_failed: {e}")

    # Filter: volume > min, not delisted
    min_vol = config.get("minDailyVolume", 5000000)
    candidates = []
    for inst in instruments:
        if inst.get("is_delisted"):
            continue
        ctx = inst.get("context", {})
        vol = float(ctx.get("dayNtlVlm", 0))
        funding = abs(float(ctx.get("funding", 0)))
        max_funding = config.get("maxFundingPer8h", 3) / 10000  # 3 = 0.03%

        if vol >= min_vol and funding <= max_funding:
            candidates.append(inst["name"])

    # Sort by volume, take top 30
    candidates = candidates[:30]

    # Scan each
    results = []
    for asset in candidates:
        try:
            data = get_asset_data(asset, intervals=["4h"], include_book=False)
            asset_data = data.get("data", data)
            candles = asset_data.get("candles", {}).get("4h", [])
            result = scan_asset(asset, candles, config)
            if result:
                results.append(result)
        except Exception:
            continue  # Graceful degradation per guide §6.3

    if not results:
        output_heartbeat()
        return

    results.sort(key=lambda x: x["rangeScore"], reverse=True)

    # Update state with new candidates
    max_ranges = config.get("maxRanges", 3)
    existing_ranges = state.get("ranges", {})
    changes = []

    for r in results:
        asset = r["asset"]
        if asset in existing_ranges:
            # Update score
            existing_ranges[asset]["rangeScore"] = r["rangeScore"]
            existing_ranges[asset]["adxCurrent"] = r["adx"]
            existing_ranges[asset]["bbWidthPercentile"] = r["bbWidthPercentile"]
        elif len(existing_ranges) < max_ranges:
            existing_ranges[asset] = {
                "phase": "VALIDATING",
                "rangeScore": r["rangeScore"],
                "adxCurrent": r["adx"],
                "bbWidthPercentile": r["bbWidthPercentile"],
                "rangeHealth": "HEALTHY",
                "breakWarning": False,
                "bouncesTraded": 0,
                "bouncesWon": 0,
            }
            changes.append(f"{asset} (score {r['rangeScore']:.2f})")

    state["ranges"] = existing_ranges
    save_state(config, state)

    output({
        "success": True,
        "candidates": len(results),
        "newRanges": changes,
        "topScores": [{"asset": r["asset"], "score": r["rangeScore"]} for r in results[:5]],
        "summary": f"{len(results)} candidates, {len(changes)} new" if changes else f"{len(results)} candidates, no new"
    })


if __name__ == "__main__":
    main()
