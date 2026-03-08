#!/usr/bin/env python3
"""PANTHER Scanner — Breakout Scalper.

Detects Bollinger Band squeezes resolving into breakouts with volume confirmation.
Tightest stops, fastest timeout. If the breakout doesn't follow through in 15-20min, cut.

Runs every 2 minutes.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panther_config as cfg


def calc_bb(closes, period=20, std_mult=2.0):
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    if middle == 0:
        return None
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    return {
        "upper": middle + std_mult * std,
        "lower": middle - std_mult * std,
        "middle": middle,
        "width_pct": (2 * std_mult * std) / middle * 100,
        "std": std,
    }


def calc_bb_history(closes, period=20, lookback=6):
    """Calculate BB width for last N bars to detect squeeze → expansion."""
    widths = []
    for i in range(lookback):
        end = len(closes) - i
        if end < period:
            break
        bb = calc_bb(closes[:end], period)
        if bb:
            widths.append(bb["width_pct"])
    widths.reverse()
    return widths


def volume_ratio(candles, lookback=10):
    """Latest bar volume vs average of previous bars."""
    if len(candles) < lookback + 1:
        return 1.0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = float(candles[-1].get("volume", candles[-1].get("v", candles[-1].get("vlm", 0))))
    return latest / avg if avg > 0 else 1.0


def extract_closes(candles):
    return [float(c.get("close", c.get("c", 0))) for c in candles if c.get("close") or c.get("c")]


def get_scan_candidates():
    """Top 30 assets by OI."""
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    candidates = []
    for inst in instruments:
        coin = inst.get("coin") or inst.get("name", "")
        oi = float(inst.get("openInterest", 0))
        if coin and oi > 500_000:
            candidates.append({"coin": coin, "oi": oi})
    candidates.sort(key=lambda x: x["oi"], reverse=True)
    return candidates[:30]


def analyze_asset(coin, entry_cfg):
    """Check for BB squeeze → breakout on 5m timeframe."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["5m", "15m"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return None

    candles_5m = data.get("data", {}).get("candles", {}).get("5m", [])
    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])

    if len(candles_5m) < 25 or len(candles_15m) < 20:
        return None

    closes_5m = extract_closes(candles_5m)
    closes_15m = extract_closes(candles_15m)
    price = closes_5m[-1]

    # Current BB on 5m
    bb = calc_bb(closes_5m)
    if not bb:
        return None

    # BB width history (last 6 bars) to detect squeeze → expansion
    bb_widths = calc_bb_history(closes_5m, lookback=6)
    if len(bb_widths) < 4:
        return None

    # Squeeze detection: width was contracting, now expanding
    min_width = min(bb_widths[:-1])
    current_width = bb_widths[-1]
    max_squeeze_width = entry_cfg.get("maxSqueezeWidthPct", 2.5)
    min_expansion_ratio = entry_cfg.get("minExpansionRatio", 1.3)

    was_squeezed = min_width < max_squeeze_width
    is_expanding = current_width > min_width * min_expansion_ratio

    if not (was_squeezed and is_expanding):
        return None

    # Volume confirmation
    vol_ratio = volume_ratio(candles_5m)
    min_vol = entry_cfg.get("minVolRatio", 1.5)
    if vol_ratio < min_vol:
        return None

    # Direction: which side of BB did price break?
    if price > bb["upper"]:
        direction = "LONG"
        breakout_strength = (price - bb["upper"]) / bb["std"] if bb["std"] > 0 else 0
    elif price < bb["lower"]:
        direction = "SHORT"
        breakout_strength = (bb["lower"] - price) / bb["std"] if bb["std"] > 0 else 0
    else:
        return None  # Price still inside bands — no breakout yet

    # 15m trend confirmation
    bb_15m = calc_bb(closes_15m)
    if bb_15m:
        if direction == "LONG" and price < bb_15m["middle"]:
            return None  # Breakout against 15m trend
        if direction == "SHORT" and price > bb_15m["middle"]:
            return None

    # Score
    score = 0
    reasons = []

    score += 3
    reasons.append(f"bb_breakout_{direction.lower()}")

    if breakout_strength > 1.5:
        score += 2
        reasons.append(f"strong_break_{breakout_strength:.1f}std")
    elif breakout_strength > 0.5:
        score += 1
        reasons.append(f"break_{breakout_strength:.1f}std")

    if vol_ratio > 2.5:
        score += 2
        reasons.append(f"vol_spike_{vol_ratio:.1f}x")
    elif vol_ratio > min_vol:
        score += 1
        reasons.append(f"vol_confirm_{vol_ratio:.1f}x")

    if current_width / min_width > 2.0:
        score += 1
        reasons.append("explosive_expansion")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "bb_width": current_width,
        "squeeze_width": min_width,
        "vol_ratio": vol_ratio,
        "breakout_strength": breakout_strength,
    }


def run():
    config = cfg.load_config()
    wallet, _ = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": f"gate={tc['gate']}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    max_positions = config.get("maxPositions", 3)
    active_coins = {p["coin"] for p in positions}

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": "max positions"})
        return

    entry_cfg = config.get("entry", {})
    candidates = get_scan_candidates()
    signals = []

    for cand in candidates:
        if cand["coin"] in active_coins:
            continue
        result = analyze_asset(cand["coin"], entry_cfg)
        if result and result["score"] >= entry_cfg.get("minScore", 5):
            signals.append(result)

    if not signals:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": f"scanned {len(candidates)}, no breakouts"})
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    leverage = config.get("leverage", {}).get("default", 10)
    margin_pct = entry_cfg.get("marginPct", 0.12)
    margin = round(account_value * margin_pct, 2)

    cfg.output({
        "success": True,
        "signal": best,
        "entry": {
            "coin": best["coin"],
            "direction": best["direction"],
            "leverage": leverage,
            "margin": margin,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "scanned": len(candidates),
        "candidates": len(signals),
    })


if __name__ == "__main__":
    run()
