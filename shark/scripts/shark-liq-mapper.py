#!/usr/bin/env python3
"""shark-liq-mapper.py — Liquidation zone estimation + scoring (every 5 min).

Phase 1 of the SHARK signal pipeline.
1. Reads OI history to find where OI built up at specific price ranges
2. Estimates liquidation zones based on entry price ranges + leverage (from funding)
3. Scores assets by proximity, OI size, leverage, momentum, book depth
4. Assets scoring >= 0.55 enter STALKING watchlist in shark-state.json

Runs isolated. Output: scored asset count or heartbeat.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-liq-mapper"

# Scoring thresholds — PREDATOR MODE
STALKING_THRESHOLD = 0.42         # Lowered from 0.55 — cast wider net
OI_BUILDUP_MIN_USD = 5_000_000   # Lowered from $10M — catch smaller clusters
PROXIMITY_THRESHOLD = 0.07        # Widened from 5% to 7% — earlier detection
TOP_CANDIDATES_FOR_DETAIL = 12    # More candidates get detailed analysis


def estimate_liq_zones(history_entries: list[dict], current_price: float,
                        current_funding: float) -> dict:
    """Estimate liquidation zones from OI history.

    Looks for periods where OI increased significantly, estimates average entry
    price as midpoint of the price range during OI buildup, then calculates
    liquidation zones based on estimated leverage.

    Returns dict with long_liq_zone and short_liq_zone info.
    """
    if len(history_entries) < 6:  # Need at least 30 min of data
        return {}

    avg_leverage = cfg.estimate_leverage_from_funding(current_funding)

    # Find OI buildup periods: where OI increased over consecutive snapshots
    # We look at the full history to find the dominant buildup range
    long_buildup_oi = 0.0
    long_entry_prices = []
    short_buildup_oi = 0.0
    short_entry_prices = []

    for i in range(1, len(history_entries)):
        prev = history_entries[i - 1]
        curr = history_entries[i]
        oi_delta = curr.get("oi", 0) - prev.get("oi", 0)
        price_at = curr.get("price", 0)
        funding = curr.get("funding", 0)

        if oi_delta <= 0 or price_at <= 0:
            continue

        # Positive OI increase: new positions opened
        oi_usd_added = oi_delta * price_at

        if funding >= 0:
            # Positive funding = longs pay shorts = longs dominant
            long_buildup_oi += oi_usd_added
            long_entry_prices.append((price_at, oi_usd_added))
        else:
            # Negative funding = shorts pay longs = shorts dominant
            short_buildup_oi += oi_usd_added
            short_entry_prices.append((price_at, oi_usd_added))

    result = {}

    # Weighted average entry for longs
    if long_entry_prices and long_buildup_oi > 0:
        weighted_entry = sum(p * w for p, w in long_entry_prices) / sum(w for _, w in long_entry_prices)
        liq_price = weighted_entry * (1 - 1 / avg_leverage)
        result["long_liq_zone"] = {
            "price": round(liq_price, 6),
            "estimated_oi": round(long_buildup_oi, 2),
            "avg_leverage": round(avg_leverage, 1),
            "avg_entry": round(weighted_entry, 6),
        }

    # Weighted average entry for shorts
    if short_entry_prices and short_buildup_oi > 0:
        weighted_entry = sum(p * w for p, w in short_entry_prices) / sum(w for _, w in short_entry_prices)
        liq_price = weighted_entry * (1 + 1 / avg_leverage)
        result["short_liq_zone"] = {
            "price": round(liq_price, 6),
            "estimated_oi": round(short_buildup_oi, 2),
            "avg_leverage": round(avg_leverage, 1),
            "avg_entry": round(weighted_entry, 6),
        }

    return result


def score_asset(zones: dict, current_price: float, momentum_toward: float,
                book_thin_score: float, leverage: int = 10) -> tuple[float, str | None]:
    """Score an asset for stalking potential. Returns (score, direction).

    Weights:
    - Large OI concentration: 0.25
    - High leverage: 0.20
    - Proximity to zone: 0.25
    - Momentum toward zone: 0.20
    - Thin book on cascade side: 0.10

    Price thresholds scale with leverage — a 0.3% price move at 10x = 3% ROE.
    """
    best_score = 0.0
    best_dir = None

    for side, direction in [("long_liq_zone", "SHORT"), ("short_liq_zone", "LONG")]:
        zone = zones.get(side)
        if not zone:
            continue

        zone_price = zone["price"]
        estimated_oi = zone["estimated_oi"]
        avg_lev = zone["avg_leverage"]

        if zone_price <= 0 or current_price <= 0:
            continue

        # 1. OI concentration (0.25)
        if estimated_oi >= OI_BUILDUP_MIN_USD:
            oi_score = min(1.0, estimated_oi / (OI_BUILDUP_MIN_USD * 5))  # Scales up to 5x threshold
        else:
            oi_score = estimated_oi / OI_BUILDUP_MIN_USD * 0.5  # Partial credit

        # 2. High leverage (0.20)
        if avg_lev >= 10:
            lev_score = min(1.0, (avg_lev - 5) / 15)  # 10x → 0.33, 15x → 0.67, 20x → 1.0
        else:
            lev_score = max(0, (avg_lev - 5) / 10)

        # 3. Proximity to zone (0.25)
        distance = abs(current_price - zone_price) / current_price
        if distance <= PROXIMITY_THRESHOLD:
            prox_score = 1.0 - (distance / PROXIMITY_THRESHOLD)  # Closer = higher
        else:
            prox_score = 0.0  # Too far

        # 4. Momentum toward zone (0.20)
        # Scale with leverage: 3% ROE worth of price movement = max score
        # At 10x: 0.3% price move = 3% ROE = max score
        mom_threshold = 0.03 / leverage  # 3% ROE expressed as price %
        # momentum_toward: positive means price is moving toward the zone
        if direction == "SHORT":
            # Longs being liquidated → zone is below → momentum negative (price dropping) = good
            mom_score = max(0, min(1.0, -momentum_toward / mom_threshold))
        else:
            # Shorts being liquidated → zone is above → momentum positive (price rising) = good
            mom_score = max(0, min(1.0, momentum_toward / mom_threshold))

        # 5. Thin book (0.10)
        thin_score = book_thin_score  # Pre-calculated

        total = (oi_score * 0.25 + lev_score * 0.20 + prox_score * 0.25 +
                 mom_score * 0.20 + thin_score * 0.10)

        if total > best_score:
            best_score = total
            best_dir = direction

    return round(best_score, 4), best_dir


def compute_momentum(history_entries: list[dict]) -> float:
    """Compute price momentum as % change over last ~1h (12 snapshots at 5min)."""
    if len(history_entries) < 3:
        return 0.0

    lookback = min(12, len(history_entries))
    old_price = history_entries[-lookback].get("price", 0)
    new_price = history_entries[-1].get("price", 0)

    if old_price <= 0:
        return 0.0

    return (new_price - old_price) / old_price


def compute_book_thinness(order_book: dict, direction: str) -> float:
    """Score how thin the order book is on the cascade side.

    For SHORT direction (long liquidation): check bid side thinness (longs sell into bids)
    For LONG direction (short liquidation): check ask side thinness (shorts buy into asks)

    Returns 0-1 score (1 = very thin = cascade likely to be violent).
    """
    levels = order_book.get("levels", [])
    if len(levels) < 2:
        return 0.5  # Unknown → neutral

    if direction == "SHORT":
        # Check bids (levels[0]) — thin bids = longs cascade harder
        side = levels[0]
    else:
        # Check asks (levels[1]) — thin asks = shorts cascade harder
        side = levels[1]

    if not side:
        return 0.5

    # Sum depth across first 10 levels in USD
    total_depth_usd = 0.0
    for level in side[:10]:
        try:
            px = float(level.get("px", 0))
            sz = float(level.get("sz", 0))
            total_depth_usd += px * sz
        except (TypeError, ValueError):
            continue

    # Heuristic: < $500K in top 10 levels = thin, > $5M = thick
    if total_depth_usd <= 0:
        return 0.5
    if total_depth_usd < 500_000:
        return 1.0
    if total_depth_usd > 5_000_000:
        return 0.0

    # Linear scale between $500K and $5M
    return 1.0 - (total_depth_usd - 500_000) / 4_500_000


def run():
    strategies = cfg.load_all_strategies()
    if not strategies:
        cfg.heartbeat(SCRIPT)

    for strat in strategies:
        sk = strat.get("strategyId")
        if not sk:
            continue

        sd = cfg.state_dir(sk)
        default_leverage = strat.get("defaultLeverage", 10)

        # Load OI history
        history_path = os.path.join(sd, "shark-oi-history.json")
        history = cfg.load_json(history_path, {})
        if not history:
            cfg.output({"status": "waiting", "script": SCRIPT, "strategyId": sk,
                        "reason": "no OI history yet — tracker needs to run first"})
            continue

        # Get current instruments for latest data
        instruments, err = cfg.fetch_instruments()
        if err:
            cfg.output_error(SCRIPT, f"fetch instruments: {err}", strategyId=sk)
            continue

        # Build lookup for current instrument data
        inst_lookup = {}
        for inst in instruments:
            name = inst.get("name")
            if name and not inst.get("is_delisted"):
                inst_lookup[name] = inst

        # Phase 1: Estimate liquidation zones for all tracked assets
        candidates = []
        for asset_name, entries in history.items():
            if len(entries) < 6:
                continue

            # Ban xyz assets — unreliable cascade signals, cost us $339 in losses
            if asset_name.startswith("xyz:"):
                continue

            inst = inst_lookup.get(asset_name)
            if not inst:
                continue

            ctx = inst.get("context", {})
            current_price = float(ctx.get("midPx") or ctx.get("markPx") or 0)
            current_funding = float(ctx.get("funding", 0))

            if current_price <= 0:
                continue

            zones = estimate_liq_zones(entries, current_price, current_funding)
            if not zones:
                continue

            momentum = compute_momentum(entries)

            candidates.append({
                "name": asset_name,
                "zones": zones,
                "current_price": current_price,
                "momentum": momentum,
                "current_funding": current_funding,
            })

        # Sort by closest proximity to any liq zone
        def closest_proximity(c):
            best = 1.0
            for side in ("long_liq_zone", "short_liq_zone"):
                z = c["zones"].get(side)
                if z:
                    dist = abs(c["current_price"] - z["price"]) / c["current_price"]
                    best = min(best, dist)
            return best

        candidates.sort(key=closest_proximity)

        # Fetch detailed data (order book) for top candidates
        top = candidates[:TOP_CANDIDATES_FOR_DETAIL]
        liq_map = {}
        stalking = []

        for cand in top:
            asset_name = cand["name"]
            # Determine dex
            dex = ""
            if asset_name.startswith("xyz:"):
                dex = "xyz"

            # Fetch order book
            asset_data, err = cfg.fetch_asset_data(
                asset_name, candle_intervals=[], include_order_book=True, dex=dex
            )

            book_data = asset_data.get("order_book", {}) if not err else {}

            # Score both directions and pick best
            for direction in ["SHORT", "LONG"]:
                book_thin = compute_book_thinness(book_data, direction)

            # Actually score
            score, direction = score_asset(
                cand["zones"], cand["current_price"], cand["momentum"],
                compute_book_thinness(book_data, "SHORT")  # Will be re-computed per side inside
            )

            # Better: compute per-side book thinness
            best_score = 0.0
            best_dir = None
            for side, d in [("long_liq_zone", "SHORT"), ("short_liq_zone", "LONG")]:
                zone = cand["zones"].get(side)
                if not zone:
                    continue
                thin = compute_book_thinness(book_data, d)
                s, _ = score_asset({side: zone}, cand["current_price"], cand["momentum"], thin, default_leverage)
                if s > best_score:
                    best_score = s
                    best_dir = d

            if best_dir is None:
                continue

            zone_key = "long_liq_zone" if best_dir == "SHORT" else "short_liq_zone"
            zone = cand["zones"].get(zone_key, {})

            proximity = abs(cand["current_price"] - zone.get("price", 0)) / cand["current_price"] if cand["current_price"] > 0 else 1.0

            entry = {
                "long_liq_zone": cand["zones"].get("long_liq_zone"),
                "short_liq_zone": cand["zones"].get("short_liq_zone"),
                "current_price": cand["current_price"],
                "proximity_to_long_liq": abs(cand["current_price"] - cand["zones"].get("long_liq_zone", {}).get("price", 0)) / cand["current_price"] if cand["zones"].get("long_liq_zone") and cand["current_price"] > 0 else None,
                "proximity_to_short_liq": abs(cand["current_price"] - cand["zones"].get("short_liq_zone", {}).get("price", 0)) / cand["current_price"] if cand["zones"].get("short_liq_zone") and cand["current_price"] > 0 else None,
                "stalking": best_score >= STALKING_THRESHOLD,
                "stalking_direction": best_dir if best_score >= STALKING_THRESHOLD else None,
                "score": best_score,
                "updated_at": cfg.now_iso(),
            }
            liq_map[asset_name] = entry

            if best_score >= STALKING_THRESHOLD:
                stalking.append(asset_name)

        # Also score remaining candidates without detailed book data
        for cand in candidates[TOP_CANDIDATES_FOR_DETAIL:]:
            asset_name = cand["name"]
            score, direction = score_asset(
                cand["zones"], cand["current_price"], cand["momentum"], 0.5, default_leverage
            )
            zone_key = "long_liq_zone" if direction == "SHORT" else "short_liq_zone"

            entry = {
                "long_liq_zone": cand["zones"].get("long_liq_zone"),
                "short_liq_zone": cand["zones"].get("short_liq_zone"),
                "current_price": cand["current_price"],
                "proximity_to_long_liq": abs(cand["current_price"] - cand["zones"].get("long_liq_zone", {}).get("price", 0)) / cand["current_price"] if cand["zones"].get("long_liq_zone") and cand["current_price"] > 0 else None,
                "proximity_to_short_liq": abs(cand["current_price"] - cand["zones"].get("short_liq_zone", {}).get("price", 0)) / cand["current_price"] if cand["zones"].get("short_liq_zone") and cand["current_price"] > 0 else None,
                "stalking": score >= STALKING_THRESHOLD,
                "stalking_direction": direction if score >= STALKING_THRESHOLD else None,
                "score": score,
                "updated_at": cfg.now_iso(),
            }
            liq_map[asset_name] = entry

            if score >= STALKING_THRESHOLD:
                stalking.append(asset_name)

        # Save liq map
        liq_map_path = os.path.join(sd, "shark-liq-map.json")
        cfg.atomic_write(liq_map_path, liq_map)

        # Update shark-state.json stalking list (preserve active positions and strike list)
        state_path = os.path.join(sd, "shark-state.json")
        state = cfg.load_json(state_path, {
            "stalking": [],
            "strike": [],
            "active_positions": {},
            "updated_at": cfg.now_iso(),
        })
        state["stalking"] = stalking
        # Remove from strike if no longer stalking
        state["strike"] = [a for a in state.get("strike", []) if a in stalking]
        state["updated_at"] = cfg.now_iso()
        cfg.atomic_write(state_path, state)

        cfg.output({
            "status": "ok",
            "script": SCRIPT,
            "strategyId": sk,
            "assets_scored": len(liq_map),
            "stalking": stalking,
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
