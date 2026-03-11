#!/usr/bin/env python3
"""shark-proximity.py — Proximity scanner for STALKING assets (every 2 min).

Phase 2 of the SHARK signal pipeline.
Watches stalking assets for price approaching their estimated liquidation zones.
When proximity >= 0.60 AND price within 3% of zone → asset enters STRIKE state.

Signals:
- Price within 3% of zone (required gate)
- Momentum accelerating toward zone (0.30)
- OI starting to crack — dropped >1% in last 10min (0.30)
- Volume surge — 15min volume > 2x average (0.20)
- Book thinning on cascade side (0.20)

Runs isolated. Output: strike promotions or heartbeat.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-proximity"

PROXIMITY_GATE = 0.05       # Widened from 3% to 5% — earlier strike
STRIKE_THRESHOLD = 0.45     # Lowered from 0.60 — more aggressive
OI_CRACK_PCT = 0.01         # 1% OI drop in 10 min = cracking
VOLUME_SURGE_MULT = 2.0     # 15min volume > 2x avg


def compute_oi_crack(history_entries: list[dict]) -> float:
    """Check if OI dropped >1% in last ~10 min (2 snapshots at 5 min).
    Returns score 0-1: 0 = no crack, 1 = significant crack.
    """
    if len(history_entries) < 3:
        return 0.0

    recent_oi = history_entries[-1].get("oi", 0)
    lookback_oi = history_entries[-3].get("oi", 0)  # ~10 min ago

    if lookback_oi <= 0:
        return 0.0

    pct_change = (recent_oi - lookback_oi) / lookback_oi

    if pct_change >= 0:
        return 0.0  # OI stable or increasing

    # Negative = OI dropping
    drop = abs(pct_change)
    if drop >= OI_CRACK_PCT:
        return min(1.0, drop / 0.05)  # Scale: 1% → 0.2, 5% → 1.0
    return drop / OI_CRACK_PCT * 0.2  # Partial credit


def compute_volume_surge(candles: list[dict]) -> float:
    """Check if recent 15min volume surges above average.
    Returns 0-1 score.
    """
    if not candles or len(candles) < 4:
        return 0.0

    # Recent ~15 min (3 candles at 5min or similar)
    recent_vol = sum(float(c.get("v", 0)) for c in candles[-3:])
    avg_vol = sum(float(c.get("v", 0)) for c in candles[:-3]) / max(1, len(candles) - 3) * 3

    if avg_vol <= 0:
        return 0.0

    ratio = recent_vol / avg_vol
    if ratio >= VOLUME_SURGE_MULT:
        return min(1.0, (ratio - 1) / 3)  # 2x → 0.33, 4x → 1.0
    return 0.0


def compute_momentum_15m(history_entries: list[dict]) -> float:
    """Compute 15-min price momentum (% change). Positive = price up."""
    if len(history_entries) < 4:
        return 0.0

    lookback = min(3, len(history_entries) - 1)  # ~15 min at 5min intervals
    old_price = history_entries[-1 - lookback].get("price", 0)
    new_price = history_entries[-1].get("price", 0)

    if old_price <= 0:
        return 0.0

    return (new_price - old_price) / old_price


def compute_book_thinness(order_book: dict, direction: str) -> float:
    """Score book thinness on cascade side. Same logic as liq-mapper."""
    levels = order_book.get("levels", [])
    if len(levels) < 2:
        return 0.5

    if direction == "SHORT":
        side = levels[0]  # Bids thin = longs cascade harder
    else:
        side = levels[1]  # Asks thin = shorts cascade harder

    if not side:
        return 0.5

    total_depth_usd = 0.0
    for level in side[:10]:
        try:
            px = float(level.get("px", 0))
            sz = float(level.get("sz", 0))
            total_depth_usd += px * sz
        except (TypeError, ValueError):
            continue

    if total_depth_usd <= 0:
        return 0.5
    if total_depth_usd < 500_000:
        return 1.0
    if total_depth_usd > 5_000_000:
        return 0.0
    return 1.0 - (total_depth_usd - 500_000) / 4_500_000


def score_proximity(distance_pct: float, momentum_score: float,
                    oi_crack_score: float, volume_score: float,
                    book_thin_score: float) -> float:
    """Compute proximity score.
    Gate: distance must be <= 3%.
    Weights: momentum 0.30, OI crack 0.30, volume 0.20, book thin 0.20.
    """
    if distance_pct > PROXIMITY_GATE:
        return 0.0

    return (momentum_score * 0.30 +
            oi_crack_score * 0.30 +
            volume_score * 0.20 +
            book_thin_score * 0.20)


def run():
    strategies = cfg.load_all_strategies()
    if not strategies:
        cfg.heartbeat(SCRIPT)

    for strat in strategies:
        sk = strat.get("strategyId")
        if not sk:
            continue

        sd = cfg.state_dir(sk)

        # Load state
        state_path = os.path.join(sd, "shark-state.json")
        state = cfg.load_json(state_path, {"stalking": [], "strike": [], "active_positions": {}})
        stalking = state.get("stalking", [])

        if not stalking:
            cfg.heartbeat(SCRIPT)
            return  # Nothing to scan

        # Load liq map
        liq_map_path = os.path.join(sd, "shark-liq-map.json")
        liq_map = cfg.load_json(liq_map_path, {})

        # Load OI history
        history_path = os.path.join(sd, "shark-oi-history.json")
        history = cfg.load_json(history_path, {})

        # Check max positions
        max_slots = strat.get("maxSlots", 2)
        active_count = len(state.get("active_positions", {}))
        if active_count >= max_slots:
            cfg.output({"status": "at_capacity", "script": SCRIPT, "strategyId": sk,
                        "active": active_count, "maxSlots": max_slots})
            continue

        new_strikes = []
        current_strikes = list(state.get("strike", []))

        for asset in stalking:
            liq_entry = liq_map.get(asset, {})
            if not liq_entry.get("stalking"):
                continue

            direction = liq_entry.get("stalking_direction")
            if not direction:
                continue

            current_price = liq_entry.get("current_price", 0)
            if current_price <= 0:
                continue

            # Determine which zone we're watching
            if direction == "SHORT":
                zone = liq_entry.get("long_liq_zone")
            else:
                zone = liq_entry.get("short_liq_zone")

            if not zone:
                continue

            zone_price = zone.get("price", 0)
            if zone_price <= 0:
                continue

            distance_pct = abs(current_price - zone_price) / current_price

            # Gate: must be within 3%
            if distance_pct > PROXIMITY_GATE:
                continue

            # Compute sub-signals
            oi_entries = history.get(asset, [])

            # Momentum toward zone — scale with leverage
            # At 10x: 0.2% price move in 15min = 2% ROE = max score
            leverage = strat.get("defaultLeverage", 10)
            mom_threshold = 0.02 / leverage  # 2% ROE expressed as price %
            momentum_15m = compute_momentum_15m(oi_entries)
            if direction == "SHORT":
                # Zone is below → negative momentum (price dropping) = toward zone
                momentum_score = max(0, min(1.0, -momentum_15m / mom_threshold))
            else:
                # Zone is above → positive momentum (price rising) = toward zone
                momentum_score = max(0, min(1.0, momentum_15m / mom_threshold))

            # OI crack
            oi_crack_score = compute_oi_crack(oi_entries)

            # Volume surge — use candle data if available, otherwise OI proxy
            # For now use a simplified volume check from OI velocity
            volume_score = 0.0  # Will be enhanced with candle data

            # Fetch order book for book thinness
            dex = "xyz" if asset.startswith("xyz:") else ""
            asset_data, err = cfg.fetch_asset_data(
                asset, candle_intervals=["5m"], include_order_book=True, dex=dex
            )

            book_data = asset_data.get("order_book", {}) if not err else {}
            book_thin_score = compute_book_thinness(book_data, direction)

            # Volume from candles
            candles_data = asset_data.get("candles", {}) if not err else {}
            candles_5m = candles_data.get("5m", []) if isinstance(candles_data, dict) else []
            if candles_5m:
                volume_score = compute_volume_surge(candles_5m)

            prox_score = score_proximity(distance_pct, momentum_score, oi_crack_score,
                                          volume_score, book_thin_score)

            if prox_score >= STRIKE_THRESHOLD:
                if asset not in current_strikes:
                    new_strikes.append(asset)
                    current_strikes.append(asset)

                # Update liq map with proximity score
                liq_entry["proximity_score"] = round(prox_score, 4)
                liq_entry["proximity_signals"] = {
                    "distance_pct": round(distance_pct, 4),
                    "momentum": round(momentum_score, 4),
                    "oi_crack": round(oi_crack_score, 4),
                    "volume": round(volume_score, 4),
                    "book_thin": round(book_thin_score, 4),
                }

        # Update state
        state["strike"] = current_strikes
        state["updated_at"] = cfg.now_iso()
        cfg.atomic_write(state_path, state)

        # Update liq map
        if liq_map:
            cfg.atomic_write(liq_map_path, liq_map)

        if new_strikes:
            cfg.output({
                "status": "strike_promoted",
                "script": SCRIPT,
                "strategyId": sk,
                "new_strikes": new_strikes,
                "all_strikes": current_strikes,
                "stalking_count": len(stalking),
            })
        else:
            cfg.output({
                "status": "ok",
                "script": SCRIPT,
                "strategyId": sk,
                "stalking": len(stalking),
                "strikes": len(current_strikes),
            })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
