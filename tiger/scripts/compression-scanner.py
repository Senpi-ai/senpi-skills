#!/usr/bin/env python3
"""
compression-scanner.py — Scan for Bollinger Band squeeze + OI accumulation breakouts.
Runs every 5min. Primary entry signal for TIGER.

MANDATE: Run TIGER compression scanner. Find BB squeeze breakouts with OI confirmation. Report signals.
"""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, save_state, get_all_instruments,
    get_asset_candles, load_oi_history, output
)
from tiger_lib import (
    parse_candles, bollinger_bands, bb_width, bb_width_percentile,
    atr, rsi, volume_ratio, oi_change_pct, confluence_score
)


def scan_asset(asset: str, context: dict, config: dict, oi_hist: dict) -> dict:
    """Analyze a single asset for compression breakout potential."""
    # Fetch candles
    result = get_asset_candles(asset, ["1h", "4h"])
    if not result.get("success") and not result.get("data"):
        return None

    data = result.get("data", result)
    candles_1h = data.get("candles", {}).get("1h", [])
    candles_4h = data.get("candles", {}).get("4h", [])

    if len(candles_1h) < 30 or len(candles_4h) < 25:
        return None

    # Parse candles
    o1, h1, l1, c1, v1 = parse_candles(candles_1h)
    o4, h4, l4, c4, v4 = parse_candles(candles_4h)

    # BB squeeze on 4h (primary signal)
    squeeze_pctl = bb_width_percentile(c4, period=20, lookback=100)
    if squeeze_pctl is None:
        return None

    # BB bands on 1h for breakout detection
    upper_1h, mid_1h, lower_1h = bollinger_bands(c1, period=20)

    # Current price vs BB bands
    current_price = c1[-1]
    if upper_1h[-1] is None or lower_1h[-1] is None:
        return None

    # Breakout detection: price breaking above upper or below lower BB
    breaking_upper = current_price > upper_1h[-1]
    breaking_lower = current_price < lower_1h[-1]
    breakout_direction = "LONG" if breaking_upper else ("SHORT" if breaking_lower else None)

    # ATR for expected move sizing
    atr_values = atr(h4, l4, c4, period=14)
    current_atr = atr_values[-1] if atr_values[-1] else 0
    atr_pct = (current_atr / current_price * 100) if current_price > 0 else 0

    # RSI
    rsi_values = rsi(c1, period=14)
    current_rsi = rsi_values[-1]

    # Volume
    vol_ratio = volume_ratio(v1, short_period=5, long_period=20)

    # OI analysis
    oi = float(context.get("openInterest", 0))
    oi_hist_asset = oi_hist.get(asset, [])
    oi_change = oi_change_pct([h["oi"] for h in oi_hist_asset], periods=12) if len(oi_hist_asset) > 12 else None

    # OI vs price divergence (rising OI + flat price = spring)
    if len(oi_hist_asset) >= 12:
        price_12_ago = oi_hist_asset[-12].get("price", current_price)
        price_change = ((current_price - price_12_ago) / price_12_ago * 100) if price_12_ago > 0 else 0
    else:
        price_change = 0

    oi_price_divergence = (oi_change is not None and oi_change > 5 and abs(price_change) < 2)

    # Funding rate
    funding_rate = float(context.get("funding", 0))
    funding_annualized = abs(funding_rate) * 3 * 365 * 100  # per-8h to annual

    # Confluence scoring
    factors = {
        "bb_squeeze": (squeeze_pctl is not None and squeeze_pctl < config["bb_squeeze_percentile"], 0.20),
        "breakout": (breakout_direction is not None, 0.20),
        "oi_building": (oi_change is not None and oi_change > config["min_oi_change_pct"], 0.15),
        "oi_price_diverge": (oi_price_divergence, 0.10),
        "volume_surge": (vol_ratio is not None and vol_ratio > 1.5, 0.15),
        "rsi_not_extreme": (current_rsi is not None and 30 < current_rsi < 70, 0.10),
        "funding_aligned": (
            (breakout_direction == "LONG" and funding_rate < 0) or
            (breakout_direction == "SHORT" and funding_rate > 0),
            0.05
        ),
        "atr_expanding": (atr_pct > 2.0, 0.05),
    }

    score = confluence_score(factors)

    # Only report if in squeeze or breaking out
    if squeeze_pctl is not None and squeeze_pctl < 40:
        return {
            "asset": asset,
            "pattern": "COMPRESSION_BREAKOUT",
            "score": round(score, 2),
            "direction": breakout_direction,
            "bb_squeeze_percentile": round(squeeze_pctl, 1),
            "breakout": breakout_direction is not None,
            "current_price": current_price,
            "upper_bb": round(upper_1h[-1], 4),
            "lower_bb": round(lower_1h[-1], 4),
            "rsi": round(current_rsi, 1) if current_rsi else None,
            "atr_pct": round(atr_pct, 2),
            "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
            "oi": oi,
            "oi_change_1h_pct": round(oi_change, 1) if oi_change else None,
            "oi_price_divergence": oi_price_divergence,
            "funding_annualized_pct": round(funding_annualized, 1),
            "max_leverage": context.get("max_leverage", 0),
            "factors": {k: v[0] for k, v in factors.items()}
        }

    return None


def main():
    config = load_config()
    state = load_state()

    if state.get("halted"):
        output({"action": "compression_scan", "halted": True, "reason": state.get("halt_reason")})
        return

    # Get all instruments
    instruments = get_all_instruments()
    if not instruments:
        output({"error": "Failed to fetch instruments"})
        return

    oi_hist = load_oi_history()

    # Filter: skip delisted, low leverage, and already-held assets in this strategy
    active_coins = set(state.get("active_positions", {}).keys())
    candidates = []
    for inst in instruments:
        name = inst.get("name", "")
        if inst.get("is_delisted"):
            continue
        max_lev = inst.get("max_leverage", 0)
        if max_lev < config["min_leverage"]:
            continue
        # Skip XYZ for now (isolated margin complexity)
        if name.startswith("xyz:"):
            continue
        ctx = inst.get("context", {})
        # Need minimum volume
        day_vol = float(ctx.get("dayNtlVlm", 0))
        if day_vol < 1_000_000:  # $1M min daily volume
            continue
        candidates.append((name, ctx, max_lev))

    # Scan top candidates (by volume, limit to 25 to manage API calls)
    candidates.sort(key=lambda x: float(x[1].get("dayNtlVlm", 0)), reverse=True)
    candidates = candidates[:8]

    signals = []
    for name, ctx, max_lev in candidates:
        ctx["max_leverage"] = max_lev
        result = scan_asset(name, ctx, config, oi_hist)
        if result:
            signals.append(result)

    # Sort by score
    signals.sort(key=lambda x: x["score"], reverse=True)

    # Filter by minimum confluence for current aggression
    min_score = config["min_confluence_score"].get(state.get("aggression", "NORMAL"), 2.0)
    actionable = [s for s in signals if s["score"] >= min_score and s.get("breakout")]
    watching = [s for s in signals if s["score"] >= 1.0 and not s.get("breakout")]

    # Check slot availability
    available_slots = config["max_slots"] - len(active_coins)

    report = {
        "action": "compression_scan",
        "scanned": len(candidates),
        "signals_found": len(signals),
        "actionable": len(actionable),
        "watching": len(watching),
        "available_slots": available_slots,
        "min_score": min_score,
        "aggression": state.get("aggression", "NORMAL"),
        "top_signals": actionable[:5],
        "watching_list": watching[:5],
        "all_signals": signals[:5],
        "active_positions": list(active_coins)
    }

    output(report)


if __name__ == "__main__":
    main()
