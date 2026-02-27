#!/usr/bin/env python3
"""
cascade-detector.py — LION cascade detection.
Runs every 90s. Combines OI history with price velocity, volume, and funding
to detect liquidation cascades and identify entry windows. Tier 2 cron.

MANDATE: If cascade in ENTRY_WINDOW + slots available → output entry signal.
CRITICAL: NEVER signal entry during ACTIVE phase.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from lion_config import (
    load_config, load_state, save_state, load_oi_history,
    get_asset_data, now_utc,
    output, output_heartbeat, output_error
)
from lion_lib import (
    detect_cascade_phase, price_velocity_pct, volume_spike_ratio,
    cascade_position_size_pct, cascade_leverage, parse_candles
)


def evaluate_asset(asset, history, config, state):
    """Evaluate one asset for cascade entry opportunity."""
    if len(history) < 16:
        return None

    # Cascade phase detection from OI history
    phase, metrics = detect_cascade_phase(history, config)

    if phase == "NONE":
        return None

    # Price velocity check
    price_vel = price_velocity_pct(history, min(15, len(history) - 1))
    price_threshold = config.get("cascadePriceVelocityPct", 2)

    # Volume spike — compare recent vs avg from history
    if len(history) >= 240:
        recent_vols = [h.get("volume15m", 0) for h in history[-15:]]
        older_vols = [h.get("volume15m", 0) for h in history[-240:-15]]
        avg_recent = sum(recent_vols) / max(len(recent_vols), 1)
        avg_older = sum(older_vols) / max(len(older_vols), 1)
        vol_ratio = volume_spike_ratio(avg_recent, avg_older)
    else:
        vol_ratio = 1.0

    vol_threshold = config.get("cascadeVolumeMultiplier", 3)

    # Funding spike
    funding_spike = False
    if history[-1].get("funding") is not None:
        funding = abs(history[-1]["funding"])
        funding_threshold = config.get("cascadeFundingSpikePer8h", 5) / 10000
        funding_spike = funding >= funding_threshold

    # Build result
    result = {
        "asset": asset,
        "cascadePhase": phase,
        "oiChange15mPct": metrics.get("oiChange15mPct"),
        "oiVelocity": metrics.get("oiVelocity"),
        "oiAcceleration": metrics.get("oiAcceleration"),
        "priceVelocityPct": price_vel,
        "volumeRatio": vol_ratio,
        "fundingSpike": funding_spike,
    }

    # Determine if all cascade conditions are met
    oi_cliff = abs(metrics.get("oiChange15mPct", 0)) >= config.get("cascadeOiCliffPct", 8)
    price_move = price_vel is not None and abs(price_vel) >= price_threshold
    vol_spike = vol_ratio >= vol_threshold

    result["allConditionsMet"] = oi_cliff and price_move and vol_spike and funding_spike

    # Cascade direction: if OI dropped and price dropped → longs got liquidated → enter LONG
    if price_vel is not None and price_vel < 0:
        result["cascadeDirection"] = "DOWN"
        result["entryDirection"] = "LONG"
    else:
        result["cascadeDirection"] = "UP"
        result["entryDirection"] = "SHORT"

    # Only signal entry at ENTRY_WINDOW with all conditions
    if phase == "ENTRY_WINDOW" and result["allConditionsMet"]:
        oi_drop = abs(metrics.get("oiChange15mPct", 8))
        size_pct = cascade_position_size_pct(oi_drop, vol_ratio, config)
        leverage = cascade_leverage(abs(price_vel or 3), config)

        result["actionable"] = True
        result["sizePct"] = size_pct
        result["leverage"] = leverage
        result["pattern"] = "CASCADE_REVERSAL"

        # Compute target (snapback % of cascade move)
        snapback_pct = config.get("snapbackTargetPct", 50) / 100
        current_price = history[-1]["price"]
        cascade_move = current_price * abs(price_vel) / 100
        if result["entryDirection"] == "LONG":
            result["targetPrice"] = round(current_price + cascade_move * snapback_pct, 6)
        else:
            result["targetPrice"] = round(current_price - cascade_move * snapback_pct, 6)
    else:
        result["actionable"] = False

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

    # Check slots
    max_slots = config.get("maxSlots", 2)
    active_count = len(state.get("activePositions", {}))
    slots_available = active_count < max_slots

    # Check daily trade limit
    max_daily = config.get("maxDailyTrades", 4)
    trades_today = state.get("safety", {}).get("tradesToday", 0)
    daily_limit_ok = trades_today < max_daily

    # Load OI history
    oi_history = load_oi_history()
    if not oi_history:
        output_heartbeat()
        return

    cascades = []
    watching = []

    for asset, history in oi_history.items():
        result = evaluate_asset(asset, history, config, state)
        if result is None:
            continue

        if result["actionable"] and slots_available and daily_limit_ok:
            cascades.append(result)
        elif result["cascadePhase"] in ("BUILDING", "ACTIVE", "STABILIZING"):
            watching.append({
                "asset": asset,
                "phase": result["cascadePhase"],
                "oiChange15mPct": result["oiChange15mPct"],
            })

    # Update watchlist in state
    pre_cascade = {}
    for w in watching:
        pre_cascade[w["asset"]] = {
            "phase": w["phase"],
            "oiChange": w["oiChange15mPct"],
            "flaggedAt": now_utc().isoformat(),
        }
    state.setdefault("watchlist", {})["preCascade"] = pre_cascade
    save_state(config, state)

    if not cascades and not watching:
        output_heartbeat()
        return

    output({
        "success": True,
        "cascades": cascades,
        "watching": [{"asset": w["asset"], "phase": w["phase"]} for w in watching],
        "slotsAvailable": slots_available,
        "dailyLimitOk": daily_limit_ok,
        "summary": f"{len(cascades)} actionable, {len(watching)} watching"
    })


if __name__ == "__main__":
    main()
