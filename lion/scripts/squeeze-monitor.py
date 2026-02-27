#!/usr/bin/env python3
"""
squeeze-monitor.py — LION squeeze detection.
Runs every 15min. Tracks extreme funding + rising OI + price compression.
Only signals entry on trigger events, never on buildup alone. Tier 2 cron.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from lion_config import (
    load_config, load_state, save_state, load_oi_history,
    get_all_instruments, get_asset_data, get_sm_markets, now_utc,
    output, output_heartbeat, output_error
)
from lion_lib import (
    is_squeeze_building, squeeze_direction, oi_change_pct,
    funding_annualized, parse_candles
)


def check_trigger_event(asset, history, candles_1h, config):
    """Check if a squeeze trigger event is happening now.
    Triggers: price breaks 24h range, OI starts dropping, cascade begins on crowded side.
    Returns (triggered: bool, reason: str)"""
    if not candles_1h or len(candles_1h) < 24:
        return False, None

    _, highs, lows, closes, _ = parse_candles(candles_1h)

    # 24h high/low
    high_24h = max(highs[-24:])
    low_24h = min(lows[-24:])
    current = closes[-1]

    # Trigger 1: Price breaks 24h range
    range_pct = (high_24h - low_24h) / low_24h * 100 if low_24h > 0 else 0
    if current > high_24h or current < low_24h:
        return True, "RANGE_BREAK"

    # Trigger 2: OI starting to drop (the unwind begins)
    if history and len(history) >= 16:
        oi_5m = oi_change_pct(history, 5)
        oi_15m = oi_change_pct(history, 15)
        if oi_5m is not None and oi_5m < -3:
            return True, "OI_UNWIND"

    # Trigger 3: Volume spike suggesting forced liquidations
    if len(candles_1h) >= 2:
        recent_vol = float(candles_1h[-1].get("v", candles_1h[-1].get("volume", 0)))
        avg_vol = sum(float(c.get("v", c.get("volume", 0))) for c in candles_1h[-24:]) / 24
        if avg_vol > 0 and recent_vol > avg_vol * 3:
            return True, "VOLUME_SPIKE"

    return False, None


def main():
    try:
        config = load_config()
        state = load_state(config)
    except Exception as e:
        output_error(f"config_load_failed: {e}")

    if state.get("safety", {}).get("halted"):
        output_heartbeat()
        return

    # Check slots
    max_slots = config.get("maxSlots", 2)
    active_count = len(state.get("activePositions", {}))
    slots_available = active_count < max_slots

    max_daily = config.get("maxDailyTrades", 4)
    trades_today = state.get("safety", {}).get("tradesToday", 0)
    daily_ok = trades_today < max_daily

    # Get instruments with funding
    instruments = get_all_instruments()
    if not instruments:
        output_heartbeat()
        return

    oi_history = load_oi_history()
    sm_markets = get_sm_markets(limit=50)
    sm_map = {}
    for m in sm_markets:
        name = m.get("asset", m.get("name", ""))
        sm_map[name] = m

    squeeze_threshold = config.get("squeezeFundingPer8h", 8) / 10000

    building = []
    actionable = []

    for inst in instruments:
        asset = inst.get("name", inst.get("coin", ""))
        funding = inst.get("funding", inst.get("fundingRate"))
        if funding is None:
            continue
        funding = float(funding)

        if abs(funding) < squeeze_threshold:
            continue

        # Get OI trend from history
        asset_history = oi_history.get(asset, [])
        oi_trend = oi_change_pct(asset_history, 60) if len(asset_history) >= 61 else None  # 1h

        # Price change 24h
        price_24h = None
        if asset_history and len(asset_history) >= 2:
            first_price = asset_history[0].get("price", 0)
            last_price = asset_history[-1].get("price", 0)
            if first_price > 0:
                price_24h = abs((last_price - first_price) / first_price * 100)

        squeeze_ok = is_squeeze_building(funding, oi_trend, price_24h or 999, config)

        if not squeeze_ok:
            continue

        direction = squeeze_direction(funding)
        if not direction:
            continue

        annualized = round(funding_annualized(funding), 1)

        entry = {
            "asset": asset,
            "fundingPer8h": round(funding * 100, 4),
            "fundingAnnualized": annualized,
            "oiTrend1hPct": oi_trend,
            "priceChange24hPct": round(price_24h, 2) if price_24h else None,
            "squeezeDirection": direction,
        }

        # SM alignment check
        sm = sm_map.get(asset)
        if sm:
            entry["smAligned"] = True  # Could check direction alignment
        else:
            entry["smAligned"] = False

        # Check for trigger event
        candle_data = get_asset_data(asset, intervals=["1h"])
        candles_1h = []
        if candle_data and not candle_data.get("error"):
            d = candle_data.get("data", candle_data)
            candles_1h = d.get("candles", {}).get("1h", [])

        triggered, trigger_reason = check_trigger_event(
            asset, asset_history, candles_1h, config
        )

        if triggered and slots_available and daily_ok:
            # Determine sizing
            if annualized > 200:
                size_pct = config.get("sizingStrongSqueeze", 15)
            else:
                size_pct = config.get("sizingModerateSqueeze", 10)

            leverage = config.get("leverageSqueeze", 8)

            entry["actionable"] = True
            entry["pattern"] = "SQUEEZE"
            entry["triggerReason"] = trigger_reason
            entry["entryDirection"] = direction
            entry["sizePct"] = size_pct
            entry["leverage"] = leverage
            actionable.append(entry)
        else:
            entry["actionable"] = False
            entry["status"] = "BUILDING" if not triggered else "TRIGGER_NO_SLOT"
            building.append(entry)

    # Update squeeze watchlist in state
    squeeze_watch = {}
    for b in building:
        squeeze_watch[b["asset"]] = {
            "direction": b["squeezeDirection"],
            "funding": b["fundingPer8h"],
            "annualized": b["fundingAnnualized"],
            "flaggedAt": now_utc().isoformat(),
        }
    state.setdefault("watchlist", {})["squeezes"] = squeeze_watch
    save_state(config, state)

    if not actionable and not building:
        output_heartbeat()
        return

    output({
        "success": True,
        "actionable": actionable,
        "building": [{"asset": b["asset"], "direction": b["squeezeDirection"],
                       "annualized": b["fundingAnnualized"]} for b in building[:5]],
        "summary": f"{len(actionable)} triggered, {len(building)} building"
    })


if __name__ == "__main__":
    main()
