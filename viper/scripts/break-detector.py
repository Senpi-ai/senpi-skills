#!/usr/bin/env python3
"""
break-detector.py — VIPER range break detection.
Runs every 2 min. Checks for range break signals. Tier 1 cron.
Takes precedence over bounce-trader when both fire.

MANDATE: If break confirmed → close ALL positions on asset, cancel orders, cooldown.
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from viper_config import (
    load_config, load_state, save_state, load_range,
    get_asset_data, get_all_instruments, now_utc, hours_since,
    output, output_heartbeat, output_error
)
from viper_lib import parse_candles, adx, volume_ratio


def check_break(asset, rng, range_data, instruments_map, config):
    """Check if a range has broken. Returns break info or None."""
    sup_level = range_data.get("supportLevel")
    res_level = range_data.get("resistanceLevel")
    if not sup_level or not res_level:
        return None

    # Fetch 1h candles
    try:
        data = get_asset_data(asset, intervals=["1h", "4h"], include_book=False)
    except Exception:
        return None

    asset_data = data.get("data", data)
    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])

    if len(candles_1h) < 3:
        return None

    _, highs_1h, lows_1h, closes_1h, volumes_1h = parse_candles(candles_1h)

    current_close = closes_1h[-1]
    prev_close = closes_1h[-2]

    break_signals = []
    break_direction = None

    # Signal 1: 1h close outside range + volume > 2x average
    vol_mult = config.get("breakVolumeMultiplier", 2)
    vol_r = volume_ratio(volumes_1h, recent_bars=1, lookback_bars=24)

    if current_close > res_level and vol_r >= vol_mult:
        break_signals.append("close_above_resistance_with_volume")
        break_direction = "LONG"
    elif current_close < sup_level and vol_r >= vol_mult:
        break_signals.append("close_below_support_with_volume")
        break_direction = "SHORT"

    # Signal 2: ADX crosses above threshold
    if candles_4h and len(candles_4h) >= 15:
        _, h4, l4, c4, _ = parse_candles(candles_4h)
        adx_vals, _, _ = adx(h4, l4, c4, 14)
        adx_threshold = config.get("breakAdxThreshold", 25)
        if adx_vals[-1] is not None and adx_vals[-1] >= adx_threshold:
            break_signals.append(f"adx_above_{adx_threshold}")
            if break_direction is None:
                break_direction = "LONG" if current_close > (sup_level + res_level) / 2 else "SHORT"

    # Signal 3: OI surge (check from instruments)
    inst = instruments_map.get(asset)
    if inst:
        # We don't have OI history in VIPER (that's LION's domain)
        # But we can check if current OI is much higher than context suggests
        # This is a simplified check — flag for future enhancement
        pass

    # Signal 4: Two consecutive 1h closes outside range
    if len(closes_1h) >= 2:
        if prev_close > res_level and current_close > res_level:
            break_signals.append("two_consecutive_closes_above")
            break_direction = break_direction or "LONG"
        elif prev_close < sup_level and current_close < sup_level:
            break_signals.append("two_consecutive_closes_below")
            break_direction = break_direction or "SHORT"

    # Signal 5: Funding spike
    if inst:
        funding = abs(float(inst.get("context", {}).get("funding", 0)))
        funding_threshold = config.get("breakFundingThresholdPer8h", 5) / 10000
        if funding >= funding_threshold:
            break_signals.append("funding_spike")

    if not break_signals:
        # Check for warning (single signal but not confirmed)
        warning = False
        if current_close > res_level or current_close < sup_level:
            warning = True
        return {"warning": warning, "asset": asset} if warning else None

    # Confirmed break
    return {
        "confirmed": True,
        "asset": asset,
        "direction": break_direction,
        "signals": break_signals,
        "signalCount": len(break_signals),
        "currentPrice": round(current_close, 6),
        "volumeRatio": vol_r,
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
    trading_ranges = {k: v for k, v in ranges.items()
                      if v.get("phase") in ("TRADING", "VALIDATING")}

    if not trading_ranges:
        output_heartbeat()
        return

    # Fetch instruments once for funding check
    try:
        instruments = get_all_instruments()
        inst_map = {i["name"]: i for i in instruments if not i.get("is_delisted")}
    except Exception:
        inst_map = {}

    breaks = []
    warnings = []

    for asset, rng in trading_ranges.items():
        range_data = load_range(config, asset)
        result = check_break(asset, rng, range_data, inst_map, config)

        if result is None:
            continue

        if result.get("confirmed"):
            breaks.append(result)
            # Update state
            cooldown_hours = config.get("breakCooldownHours", 4)
            ranges[asset]["phase"] = "COOLDOWN"
            state.setdefault("cooldown", {})[asset] = {
                "brokeAt": now_utc().isoformat(),
                "breakDirection": result["direction"],
                "cooldownUntil": None,  # Agent calculates from brokeAt + cooldownHours
            }
        elif result.get("warning"):
            warnings.append(result["asset"])
            ranges[asset]["breakWarning"] = True

    # Clean expired cooldowns
    for asset, cd in list(state.get("cooldown", {}).items()):
        broke_at = cd.get("brokeAt")
        cooldown_hours = config.get("breakCooldownHours", 4)
        if broke_at and hours_since(broke_at) >= cooldown_hours:
            del state["cooldown"][asset]
            if asset in ranges and ranges[asset].get("phase") == "COOLDOWN":
                del ranges[asset]

    state["ranges"] = ranges
    save_state(config, state)

    if not breaks and not warnings:
        output_heartbeat()
        return

    output({
        "success": True,
        "breaks": breaks,
        "warnings": warnings,
        "actions": {
            "closeAll": [b["asset"] for b in breaks],
            "tightenStops": warnings,
        },
        "summary": f"{len(breaks)} breaks, {len(warnings)} warnings"
    })


if __name__ == "__main__":
    main()
