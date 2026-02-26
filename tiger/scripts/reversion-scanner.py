#!/usr/bin/env python3
"""
reversion-scanner.py — Mean reversion scanner for TIGER.
Finds overextended assets with RSI extremes, divergence, and volume exhaustion.
Runs every 5min.

MANDATE: Run TIGER reversion scanner. Find mean reversion setups. Report signals.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, get_all_instruments,
    get_asset_candles, load_oi_history, output
)
from tiger_lib import (
    parse_candles, rsi, bollinger_bands, atr, volume_ratio,
    detect_rsi_divergence, oi_change_pct, confluence_score, sma
)


def scan_asset(asset: str, context: dict, config: dict, oi_hist: dict) -> dict:
    """Scan for mean reversion setup on a single asset."""
    result = get_asset_candles(asset, ["1h", "4h"])
    if not result.get("success") and not result.get("data"):
        return None

    data = result.get("data", result)
    candles_1h = data.get("candles", {}).get("1h", [])
    candles_4h = data.get("candles", {}).get("4h", [])

    if len(candles_1h) < 30 or len(candles_4h) < 25:
        return None

    o1, h1, l1, c1, v1 = parse_candles(candles_1h)
    o4, h4, l4, c4, v4 = parse_candles(candles_4h)

    # RSI on both timeframes
    rsi_1h = rsi(c1, 14)
    rsi_4h = rsi(c4, 14)
    current_rsi_1h = rsi_1h[-1]
    current_rsi_4h = rsi_4h[-1]

    if current_rsi_4h is None:
        return None

    # Determine if overextended
    overbought = current_rsi_4h >= config["rsi_overbought"]
    oversold = current_rsi_4h <= config["rsi_oversold"]
    if not overbought and not oversold:
        return None

    direction = "SHORT" if overbought else "LONG"

    # RSI divergence detection on 1h
    divergence = detect_rsi_divergence(c1, rsi_1h, lookback=20)
    divergence_aligned = (
        (divergence == "bearish" and overbought) or
        (divergence == "bullish" and oversold)
    )

    # Price extension (24h move)
    if len(c1) >= 24:
        price_24h_ago = c1[-24]
        price_change_24h = ((c1[-1] - price_24h_ago) / price_24h_ago) * 100
    else:
        price_change_24h = 0

    extended = abs(price_change_24h) > 10  # >10% in 24h = extended

    # Volume exhaustion: declining volume on continued extension
    vol_ratio_val = volume_ratio(v1, short_period=3, long_period=12)
    volume_exhaustion = vol_ratio_val is not None and vol_ratio_val < 0.7

    # BB: price at or beyond bands
    upper_4h, mid_4h, lower_4h = bollinger_bands(c4, 20)
    current_price = c4[-1]
    beyond_upper = upper_4h[-1] and current_price > upper_4h[-1]
    beyond_lower = lower_4h[-1] and current_price < lower_4h[-1]
    at_extreme_bb = (beyond_upper and overbought) or (beyond_lower and oversold)

    # ATR for stop sizing
    atr_values = atr(h4, l4, c4, 14)
    current_atr = atr_values[-1] if atr_values[-1] else 0
    atr_pct = (current_atr / current_price * 100) if current_price > 0 else 0

    # OI: crowded trade check
    oi = float(context.get("openInterest", 0))
    oi_hist_asset = oi_hist.get(asset, [])
    oi_at_high = False
    if len(oi_hist_asset) >= 24:
        oi_values = [h["oi"] for h in oi_hist_asset[-24:]]
        oi_sma = sum(oi_values) / len(oi_values)
        oi_at_high = oi > oi_sma * 1.15  # OI 15% above recent average = crowded

    # Funding alignment (funding against the crowd = we get paid)
    funding_rate = float(context.get("funding", 0))
    funding_annualized = abs(funding_rate) * 3 * 365 * 100
    funding_pays_us = (
        (direction == "SHORT" and funding_rate > 0) or  # Shorts get paid when longs pay
        (direction == "LONG" and funding_rate < 0)
    )

    # SM concentration: if smart money is on the other side, reversion more likely
    # (checked at signal evaluation by the agent, not here)

    # Confluence
    factors = {
        "rsi_extreme_4h": (True, 0.20),  # Already filtered for this
        "rsi_extreme_1h": (
            current_rsi_1h is not None and (
                (overbought and current_rsi_1h >= 70) or
                (oversold and current_rsi_1h <= 30)
            ), 0.15),
        "divergence": (divergence_aligned, 0.20),
        "price_extended": (extended, 0.10),
        "volume_exhaustion": (volume_exhaustion, 0.15),
        "at_extreme_bb": (at_extreme_bb, 0.10),
        "oi_crowded": (oi_at_high, 0.15),
        "funding_pays_us": (funding_pays_us, 0.10),
    }

    score = confluence_score(factors)

    # Calculate expected reversion target (to middle BB or SMA20)
    if mid_4h[-1]:
        expected_move_pct = abs((mid_4h[-1] - current_price) / current_price * 100)
    else:
        expected_move_pct = atr_pct * 2

    return {
        "asset": asset,
        "pattern": "MEAN_REVERSION",
        "score": round(score, 2),
        "direction": direction,
        "current_price": current_price,
        "rsi_4h": round(current_rsi_4h, 1),
        "rsi_1h": round(current_rsi_1h, 1) if current_rsi_1h else None,
        "divergence": divergence,
        "divergence_aligned": divergence_aligned,
        "price_change_24h_pct": round(price_change_24h, 1),
        "volume_exhaustion": volume_exhaustion,
        "volume_ratio": round(vol_ratio_val, 2) if vol_ratio_val else None,
        "at_extreme_bb": at_extreme_bb,
        "oi_crowded": oi_at_high,
        "expected_move_pct": round(expected_move_pct, 1),
        "atr_pct": round(atr_pct, 2),
        "funding_annualized_pct": round(funding_annualized, 1),
        "funding_pays_us": funding_pays_us,
        "max_leverage": context.get("max_leverage", 0),
        "factors": {k: v[0] for k, v in factors.items()}
    }


def main():
    config = load_config()
    state = load_state()

    if state.get("halted"):
        output({"action": "reversion_scan", "halted": True, "reason": state.get("halt_reason")})
        return

    instruments = get_all_instruments()
    if not instruments:
        output({"error": "Failed to fetch instruments"})
        return

    oi_hist = load_oi_history()
    active_coins = set(state.get("active_positions", {}).keys())

    # Filter candidates
    candidates = []
    for inst in instruments:
        name = inst.get("name", "")
        if inst.get("is_delisted") or name.startswith("xyz:"):
            continue
        if inst.get("max_leverage", 0) < config["min_leverage"]:
            continue
        ctx = inst.get("context", {})
        if float(ctx.get("dayNtlVlm", 0)) < 1_000_000:
            continue
        candidates.append((name, ctx))

    # Sort by volume, scan top 25
    candidates.sort(key=lambda x: float(x[1].get("dayNtlVlm", 0)), reverse=True)
    candidates = candidates[:8]

    signals = []
    for name, ctx in candidates:
        ctx["max_leverage"] = next((i.get("max_leverage", 0) for i in instruments if i.get("name") == name), 0)
        result = scan_asset(name, ctx, config, oi_hist)
        if result:
            signals.append(result)

    signals.sort(key=lambda x: x["score"], reverse=True)

    min_score = config["min_confluence_score"].get(state.get("aggression", "NORMAL"), 2.0)
    actionable = [s for s in signals if s["score"] >= min_score]
    available_slots = config["max_slots"] - len(active_coins)

    output({
        "action": "reversion_scan",
        "scanned": len(candidates),
        "signals_found": len(signals),
        "actionable": len(actionable),
        "available_slots": available_slots,
        "aggression": state.get("aggression", "NORMAL"),
        "top_signals": actionable[:5],
        "active_positions": list(active_coins)
    })


if __name__ == "__main__":
    main()
