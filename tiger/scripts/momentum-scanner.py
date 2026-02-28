#!/usr/bin/env python3
"""
momentum-scanner.py — Catches assets with strong price moves + volume spikes.
Simpler than compression scanner — doesn't require BB squeeze, just momentum.
Runs every 5min.

Looks for:
- 1h candle with >2% move + above-average volume
- Continuation bias (move aligns with 4h trend)
- Not already overextended (RSI < 80 for longs, > 20 for shorts)
"""

import sys
import os
import json
import time
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, get_all_instruments,
    get_asset_candles, output, STATE_DIR,
    load_prescreened_candidates
)
from tiger_lib import (
    parse_candles, rsi, sma, atr, volume_ratio, confluence_score
)


def scan_asset(asset: str, context: dict, config: dict) -> dict:
    """Scan for momentum breakout on a single asset."""
    result = get_asset_candles(asset, ["1h", "4h"])
    if not result.get("success") and not result.get("data"):
        return None

    data = result.get("data", result)
    candles_1h = data.get("candles", {}).get("1h", [])
    candles_4h = data.get("candles", {}).get("4h", [])

    if len(candles_1h) < 20 or len(candles_4h) < 20:
        return None

    o1, h1, l1, c1, v1 = parse_candles(candles_1h)
    o4, h4, l4, c4, v4 = parse_candles(candles_4h)

    current_price = c1[-1]
    if current_price <= 0:
        return None

    # 1h move (last completed candle)
    move_1h = ((c1[-1] - c1[-2]) / c1[-2]) * 100 if len(c1) >= 2 else 0
    # 2h move
    move_2h = ((c1[-1] - c1[-3]) / c1[-3]) * 100 if len(c1) >= 3 else 0
    # 4h trend
    move_4h = ((c4[-1] - c4[-2]) / c4[-2]) * 100 if len(c4) >= 2 else 0

    # Need meaningful move (>1.5% in 1h or >2.5% in 2h)
    strong_1h = abs(move_1h) >= 1.5
    strong_2h = abs(move_2h) >= 2.5
    if not strong_1h and not strong_2h:
        return None

    # Direction from the move
    direction = "LONG" if move_2h > 0 else "SHORT"

    # Volume surge
    vol_r = volume_ratio(v1, short_period=2, long_period=12)
    volume_surging = vol_r is not None and vol_r > 1.5

    # RSI check — not overextended
    rsi_values = rsi(c1, 14)
    current_rsi = rsi_values[-1]
    rsi_ok = True
    if current_rsi:
        if direction == "LONG" and current_rsi > 78:
            rsi_ok = False  # Too overbought
        if direction == "SHORT" and current_rsi < 22:
            rsi_ok = False  # Too oversold

    # 4h trend alignment
    trend_aligned = (direction == "LONG" and move_4h > 0) or (direction == "SHORT" and move_4h < 0)

    # ATR for sizing
    atr_values = atr(h4, l4, c4, 14)
    current_atr = atr_values[-1] if atr_values[-1] else 0
    atr_pct = (current_atr / current_price * 100) if current_price > 0 else 0

    # SMA trend (price above/below 20 SMA on 4h)
    sma_20 = sma(c4, 20)
    sma_aligned = False
    if sma_20[-1]:
        sma_aligned = (direction == "LONG" and current_price > sma_20[-1]) or \
                      (direction == "SHORT" and current_price < sma_20[-1])

    # Funding
    funding_rate = float(context.get("funding", 0))
    funding_annualized = abs(funding_rate) * 3 * 365 * 100

    # Confluence
    factors = {
        "strong_1h_move": (strong_1h, 0.25),
        "strong_2h_move": (strong_2h, 0.15),
        "volume_surge": (volume_surging, 0.20),
        "trend_aligned_4h": (trend_aligned, 0.15),
        "rsi_not_extreme": (rsi_ok, 0.10),
        "sma_aligned": (sma_aligned, 0.10),
        "good_atr": (atr_pct > 1.5, 0.05),
    }

    score = confluence_score(factors)

    return {
        "asset": asset,
        "pattern": "MOMENTUM_BREAKOUT",
        "score": round(score, 2),
        "direction": direction,
        "current_price": current_price,
        "move_1h_pct": round(move_1h, 2),
        "move_2h_pct": round(move_2h, 2),
        "move_4h_pct": round(move_4h, 2),
        "volume_ratio": round(vol_r, 2) if vol_r else None,
        "volume_surging": volume_surging,
        "rsi": round(current_rsi, 1) if current_rsi else None,
        "rsi_ok": rsi_ok,
        "trend_aligned": trend_aligned,
        "sma_aligned": sma_aligned,
        "atr_pct": round(atr_pct, 2),
        "funding_annualized_pct": round(funding_annualized, 1),
        "max_leverage": context.get("max_leverage", 0),
        "factors": {k: v[0] for k, v in factors.items()}
    }


def main():
    config = load_config()
    state = load_state()

    if state.get("halted"):
        output({"action": "momentum_scan", "halted": True, "reason": state.get("halt_reason")})
        return

    instruments = get_all_instruments()
    if not instruments:
        output({"error": "Failed to fetch instruments"})
        return

    active_coins = set(state.get("active_positions", {}).keys())

    # Try prescreened candidates first
    candidates = load_prescreened_candidates(instruments, config)

    if candidates is None:
        # Fallback: original behavior
        candidates = []
        for inst in instruments:
            name = inst.get("name", "")
            if inst.get("is_delisted"):
                continue
            max_lev = inst.get("max_leverage", 0)
            if max_lev < config.get("min_leverage", 5):
                continue
            ctx = inst.get("context", {})
            day_vol = float(ctx.get("dayNtlVlm", 0))
            if day_vol < 500_000:
                continue
            candidates.append((name, ctx, max_lev))
        candidates.sort(key=lambda x: float(x[1].get("dayNtlVlm", 0)), reverse=True)
        candidates = candidates[:12]

    signals = []
    for name, ctx, max_lev in candidates:
        ctx["max_leverage"] = max_lev
        result = scan_asset(name, ctx, config)
        if result:
            signals.append(result)

    signals.sort(key=lambda x: x["score"], reverse=True)

    min_score = config["min_confluence_score"].get(state.get("aggression", "NORMAL"), 0.40)
    actionable = [s for s in signals if s["score"] >= min_score and s.get("rsi_ok")]
    available_slots = config["max_slots"] - len(active_coins)

    output({
        "action": "momentum_scan",
        "scanned": len(candidates),
        "signals_found": len(signals),
        "actionable": len(actionable),
        "available_slots": available_slots,
        "min_score": min_score,
        "aggression": state.get("aggression", "NORMAL"),
        "top_signals": actionable[:5],
        "all_signals": [s["asset"] + " " + s["direction"] + " " + str(s["score"]) for s in signals[:10]],
        "active_positions": list(active_coins)
    })


if __name__ == "__main__":
    main()
