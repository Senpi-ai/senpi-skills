#!/usr/bin/env python3
"""
funding-scanner.py — Funding rate arbitrage scanner for TIGER.
Finds extreme funding rates with directional alignment.
Runs every 30min.

MANDATE: Run TIGER funding scanner. Find extreme funding rate opportunities. Report signals.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, get_all_instruments,
    get_asset_candles, get_sm_markets, load_oi_history, output
)
from tiger_lib import (
    parse_candles, rsi, sma, atr, volume_ratio,
    oi_change_pct, confluence_score
)


def analyze_funding(asset: str, context: dict, config: dict, sm_data: dict, oi_hist: dict) -> dict:
    """Analyze funding rate opportunity for a single asset."""
    funding_rate = float(context.get("funding", 0))
    funding_annualized = funding_rate * 3 * 365 * 100  # per-8h → annualized %

    if abs(funding_annualized) < config["min_funding_annualized_pct"]:
        return None

    # Direction: go opposite to the crowd (collect funding)
    # Positive funding = longs pay shorts → go SHORT to collect
    # Negative funding = shorts pay longs → go LONG to collect
    direction = "SHORT" if funding_rate > 0 else "LONG"

    # Estimate daily funding income at leverage
    # Funding is per-8h on notional, 3x per day
    # At 10x: daily funding income = funding_rate * 3 * 10 * 100 (as % of margin)
    leverage = min(config["max_leverage"], context.get("max_leverage", 10))
    daily_funding_pct_margin = abs(funding_rate) * 3 * leverage * 100
    weekly_funding_pct_margin = daily_funding_pct_margin * 7

    # Fetch candles for technical alignment check
    result = get_asset_candles(asset, ["1h", "4h"])
    if not result.get("success") and not result.get("data"):
        return None

    data = result.get("data", result)
    candles_1h = data.get("candles", {}).get("1h", [])
    candles_4h = data.get("candles", {}).get("4h", [])

    if len(candles_4h) < 25:
        return None

    _, h4, l4, c4, v4 = parse_candles(candles_4h)
    current_price = c4[-1]

    # Trend alignment: SMA20 direction
    sma20 = sma(c4, 20)
    sma_trend = None
    if sma20[-1] and sma20[-5]:
        sma_trend = "UP" if sma20[-1] > sma20[-5] else "DOWN"

    trend_aligned = (
        (direction == "LONG" and sma_trend == "UP") or
        (direction == "SHORT" and sma_trend == "DOWN")
    )

    # RSI check (don't go long at RSI 80 even if funding is good)
    rsi_values = rsi(c4, 14)
    current_rsi = rsi_values[-1]
    rsi_safe = True
    if current_rsi:
        if direction == "LONG" and current_rsi > 70:
            rsi_safe = False
        if direction == "SHORT" and current_rsi < 30:
            rsi_safe = False

    # OI check: high OI = funding imbalance is real and persistent
    oi = float(context.get("openInterest", 0))
    oi_hist_asset = oi_hist.get(asset, [])
    oi_stable = True
    if len(oi_hist_asset) >= 24:
        oi_values = [h["oi"] for h in oi_hist_asset[-24:]]
        oi_avg = sum(oi_values) / len(oi_values)
        oi_stable = oi > oi_avg * 0.85  # OI hasn't dropped more than 15%

    # SM alignment check
    sm_info = sm_data.get(asset, {})
    sm_aligned = False
    if sm_info:
        # SM traders on our side?
        sm_direction = sm_info.get("direction", "")
        sm_aligned = sm_direction.upper() == direction

    # Confluence
    factors = {
        "extreme_funding": (True, 0.25),  # Already filtered for this
        "trend_aligned": (trend_aligned, 0.20),
        "rsi_safe": (rsi_safe, 0.15),
        "oi_stable": (oi_stable, 0.15),
        "sm_aligned": (sm_aligned, 0.10),
        "high_daily_yield": (daily_funding_pct_margin > 5, 0.10),
        "volume_healthy": (float(context.get("dayNtlVlm", 0)) > 10_000_000, 0.05),
    }

    score = confluence_score(factors)

    # Risk: if funding flips, you lose the yield AND might be counter-trend
    # Higher score = more confidence funding will persist
    return {
        "asset": asset,
        "pattern": "FUNDING_ARB",
        "score": round(score, 2),
        "direction": direction,
        "current_price": current_price,
        "funding_rate_8h": round(funding_rate * 100, 4),  # as %
        "funding_annualized_pct": round(funding_annualized, 1),
        "daily_yield_pct_margin": round(daily_funding_pct_margin, 2),
        "weekly_yield_pct_margin": round(weekly_funding_pct_margin, 1),
        "leverage": leverage,
        "trend_aligned": trend_aligned,
        "sma_trend": sma_trend,
        "rsi": round(current_rsi, 1) if current_rsi else None,
        "rsi_safe": rsi_safe,
        "oi_stable": oi_stable,
        "sm_aligned": sm_aligned,
        "max_leverage": context.get("max_leverage", 0),
        "factors": {k: v[0] for k, v in factors.items()}
    }


def main():
    config = load_config()
    state = load_state()

    if state.get("halted"):
        output({"action": "funding_scan", "halted": True, "reason": state.get("halt_reason")})
        return

    instruments = get_all_instruments()
    if not instruments:
        # Retry once — instruments fetch occasionally fails
        import time
        time.sleep(3)
        instruments = get_all_instruments()
    if not instruments:
        output({"error": "Failed to fetch instruments after retry"})
        return

    # Fetch SM data once
    sm_markets = get_sm_markets(limit=50)
    sm_data = {}
    for m in sm_markets:
        token = m.get("token", "")
        if token not in sm_data or m.get("pct_of_top_traders_gain", 0) > sm_data[token].get("pct_of_top_traders_gain", 0):
            sm_data[token] = m

    oi_hist = load_oi_history()

    # Find assets with extreme funding
    candidates = []
    for inst in instruments:
        name = inst.get("name", "")
        if inst.get("is_delisted") or name.startswith("xyz:"):
            continue
        if inst.get("max_leverage", 0) < config["min_leverage"]:
            continue
        ctx = inst.get("context", {})
        funding = float(ctx.get("funding", 0))
        funding_ann = abs(funding) * 3 * 365 * 100
        if funding_ann >= config["min_funding_annualized_pct"]:
            ctx["max_leverage"] = inst.get("max_leverage", 0)
            candidates.append((name, ctx, funding_ann))

    # Sort by funding magnitude
    candidates.sort(key=lambda x: x[2], reverse=True)
    candidates = candidates[:8]  # Reduced from 15 — prevents API timeouts (~4s per candle fetch)

    signals = []
    for name, ctx, _ in candidates:
        result = analyze_funding(name, ctx, config, sm_data, oi_hist)
        if result:
            signals.append(result)

    signals.sort(key=lambda x: x["score"], reverse=True)

    min_score = config["min_confluence_score"].get(state.get("aggression", "NORMAL"), 2.0)
    actionable = [s for s in signals if s["score"] >= min_score]
    active_coins = set(state.get("active_positions", {}).keys())

    output({
        "action": "funding_scan",
        "scanned": len(candidates),
        "extreme_funding_assets": len(candidates),
        "signals_found": len(signals),
        "actionable": len(actionable),
        "available_slots": config["max_slots"] - len(active_coins),
        "aggression": state.get("aggression", "NORMAL"),
        "top_signals": actionable[:5],
        "all_extreme_funding": [
            {"asset": s["asset"], "funding_ann": s["funding_annualized_pct"],
             "daily_yield": s["daily_yield_pct_margin"], "direction": s["direction"]}
            for s in signals[:10]
        ],
        "active_positions": list(active_coins)
    })


if __name__ == "__main__":
    main()
