#!/usr/bin/env python3
"""VIPER Scanner — Range-Bound Liquidity Sniper.

Detects assets in tight ranges (low ATR, tight BBands, declining volume),
enters at support/resistance boundaries with tight stops.

Works when trending strategies are idle. The chop predator.

Runs every 5 minutes.
"""

import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import viper_config as cfg


# ─── Technical Indicators ─────────────────────────────────────

def calc_bb(closes, period=20, std_mult=2.0):
    """Bollinger Bands: returns (upper, middle, lower, width_pct)."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    if middle == 0:
        return None
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    width_pct = (upper - lower) / middle * 100
    return {"upper": upper, "middle": middle, "lower": lower, "width_pct": width_pct}


def calc_rsi(closes, period=14):
    """RSI from close prices."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g = gains[-period:]
    l = losses[-period:]
    avg_g = sum(g) / period
    avg_l = sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def calc_atr(candles, period=14):
    """Average True Range from candle data."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i].get("high", candles[i].get("h", 0)))
        l = float(candles[i].get("low", candles[i].get("l", 0)))
        pc = float(candles[i - 1].get("close", candles[i - 1].get("c", 0)))
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs[-period:]) / period


def extract_closes(candles):
    return [float(c.get("close", c.get("c", 0))) for c in candles if c.get("close") or c.get("c")]


def extract_volumes(candles):
    return [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles]


# ─── Scan Assets ──────────────────────────────────────────────

def get_scan_candidates(entry_cfg):
    """Get top assets by USD-weighted OI as scan candidates."""
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    candidates = []
    min_oi_usd = entry_cfg.get("minOiUsd", 5_000_000)
    for inst in instruments:
        coin = inst.get("coin") or inst.get("name", "")
        oi = float(inst.get("openInterest", 0))
        mark_px = float(inst.get("markPx", inst.get("midPx", 0)))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if coin and oi_usd > min_oi_usd:
            candidates.append({"coin": coin, "oi": oi, "oi_usd": oi_usd, "price": mark_px})
    candidates.sort(key=lambda x: x["oi_usd"], reverse=True)
    return candidates[:30]


def analyze_asset(coin, entry_cfg):
    """Analyze one asset for range-bound conditions."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["15m", "1h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return None

    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])

    if len(candles_1h) < 24 or len(candles_15m) < 20:
        return None

    closes_1h = extract_closes(candles_1h)
    closes_15m = extract_closes(candles_15m)
    volumes_1h = extract_volumes(candles_1h)

    # Bollinger Bands on 1h
    bb = calc_bb(closes_1h)
    if not bb:
        return None

    # ATR on 1h
    atr = calc_atr(candles_1h)
    if not atr:
        return None
    atr_pct = (atr / closes_1h[-1]) * 100

    # RSI on 15m
    rsi = calc_rsi(closes_15m)
    if rsi is None:
        return None

    # Volume declining?
    if len(volumes_1h) >= 10:
        recent_vol = sum(volumes_1h[-5:]) / 5
        earlier_vol = sum(volumes_1h[-10:-5]) / 5
        vol_declining = recent_vol < earlier_vol * 0.85
    else:
        vol_declining = False

    price = closes_15m[-1]
    max_bb_width = entry_cfg.get("maxBbWidthPct", 4.0)
    max_atr = entry_cfg.get("maxAtrPct", 1.5)

    # Range detection: tight BBands + low ATR
    is_range = bb["width_pct"] < max_bb_width and atr_pct < max_atr

    if not is_range:
        return None

    # Determine direction based on price position in range
    range_position = (price - bb["lower"]) / (bb["upper"] - bb["lower"]) if bb["upper"] != bb["lower"] else 0.5

    score = 0
    reasons = []
    direction = None

    # Near lower band + oversold RSI = LONG
    if range_position < 0.25 and rsi < entry_cfg.get("rsiOversold", 35):
        direction = "LONG"
        score += 3
        reasons.append(f"near_support_{range_position:.0%}")
        score += 2
        reasons.append(f"rsi_oversold_{rsi:.0f}")
    # Near upper band + overbought RSI = SHORT
    elif range_position > 0.75 and rsi > entry_cfg.get("rsiOverbought", 65):
        direction = "SHORT"
        score += 3
        reasons.append(f"near_resistance_{range_position:.0%}")
        score += 2
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    else:
        return None  # Not at boundary

    # Bonus: tight bands (squeeze)
    if bb["width_pct"] < 2.0:
        score += 1
        reasons.append("bb_squeeze")

    # Bonus: declining volume (range consolidation)
    if vol_declining:
        score += 1
        reasons.append("vol_declining")

    # Bonus: very low ATR
    if atr_pct < 0.8:
        score += 1
        reasons.append("low_atr")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "bb": bb,
        "rsi": rsi,
        "atr_pct": atr_pct,
        "range_position": range_position,
    }


def run():
    config = cfg.load_config()
    wallet, _ = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": f"gate={tc['gate']}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    max_positions = config.get("maxPositions", 3)
    active_coins = {p["coin"] for p in positions}

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": f"max positions ({len(positions)}/{max_positions})"})
        return

    entry_cfg = config.get("entry", {})
    candidates = get_scan_candidates(entry_cfg)
    signals = []

    for cand in candidates:
        if cand["coin"] in active_coins:
            continue
        result = analyze_asset(cand["coin"], entry_cfg)
        if result and result["score"] >= entry_cfg.get("minScore", 5):
            signals.append(result)

    if not signals:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": f"scanned {len(candidates)}, no range setups"})
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    leverage = config.get("leverage", {}).get("default", 8)
    margin_pct = entry_cfg.get("marginPct", 0.28)
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
