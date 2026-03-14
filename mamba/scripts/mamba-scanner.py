#!/usr/bin/env python3
# Senpi MAMBA Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""MAMBA v2.0 — Range-Bound High Water + Regime Protection.

Based on VIPER v2.1's range detection scanner. Same BB/RSI/ATR/volume logic
for detecting range-bound conditions and entering at support/resistance.

v2.0 adds three protective gates from v1.0 live data (37 trades, -31.4% ROI):
  1. BTC regime gate — no longs in bearish, no shorts in bullish
  2. Per-asset cooldown — 4 hours after a losing exit, that asset is blocked
  3. Leverage hard cap — 10x max, default 8x

Also: XYZ equities banned (weak SM data on Hyperliquid), max entries/day
reduced from 8 to 6, faster consecutive loss cooldown.

Runs every 5 minutes.
"""

import sys
import os
import json
import time
import math
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mamba_config as cfg


# ─── Cooldown State ──────────────────────────────────────────

COOLDOWN_FILE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "mamba-strategy" / "state" / "asset-cooldowns.json"


def load_cooldowns():
    try:
        if COOLDOWN_FILE.exists():
            with open(COOLDOWN_FILE) as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def save_cooldowns(cooldowns):
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    cfg.atomic_write(str(COOLDOWN_FILE), cooldowns)


def is_asset_cooled_down(coin, cooldown_minutes=240):
    cooldowns = load_cooldowns()
    if coin not in cooldowns:
        return False
    exit_ts = cooldowns[coin].get("exitTimestamp", 0)
    elapsed = (time.time() - exit_ts) / 60
    return elapsed < cooldown_minutes


# ─── Technical Indicators ─────────────────────────────────────

def calc_bb(closes, period=20, std_mult=2.0):
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


def trend_structure(candles, lookback=6):
    """Check if candles form higher lows (bullish) or lower highs (bearish)."""
    if len(candles) < lookback:
        return "NEUTRAL"
    lows = [float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
    highs = [float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH"
    elif lower_highs >= total * 0.6:
        return "BEARISH"
    return "NEUTRAL"


# ─── Gate 1: BTC Regime Filter ───────────────────────────────

def get_btc_regime():
    """Check BTC 4H trend. Returns BULLISH, BEARISH, or NEUTRAL."""
    data = cfg.mcporter_call("market_get_asset_data", asset="BTC",
                              candle_intervals=["4h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return "NEUTRAL"

    candles = data.get("data", {}).get("candles", {}).get("4h", [])
    if len(candles) < 6:
        return "NEUTRAL"

    return trend_structure(candles)


def regime_allows_direction(regime, direction):
    """BTC regime gate: no longs in bearish, no shorts in bullish."""
    if regime == "BEARISH" and direction == "LONG":
        return False
    if regime == "BULLISH" and direction == "SHORT":
        return False
    return True


# ─── Scan Assets ──────────────────────────────────────────────

def get_scan_candidates(entry_cfg):
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    candidates = []
    min_oi_usd = entry_cfg.get("minOiUsd", 5_000_000)
    banned = entry_cfg.get("bannedPrefixes", ["xyz:"])
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        coin = inst.get("coin") or inst.get("name", "")
        # Gate: ban XYZ equities
        if any(coin.lower().startswith(p) for p in banned):
            continue
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

    bb = calc_bb(closes_1h)
    if not bb:
        return None

    atr = calc_atr(candles_1h)
    if not atr:
        return None
    atr_pct = (atr / closes_1h[-1]) * 100

    rsi = calc_rsi(closes_15m)
    if rsi is None:
        return None

    if len(volumes_1h) >= 10:
        recent_vol = sum(volumes_1h[-5:]) / 5
        earlier_vol = sum(volumes_1h[-10:-5]) / 5
        vol_declining = recent_vol < earlier_vol * 0.85
    else:
        vol_declining = False

    price = closes_15m[-1]
    max_bb_width = entry_cfg.get("maxBbWidthPct", 4.0)
    max_atr = entry_cfg.get("maxAtrPct", 1.5)

    is_range = bb["width_pct"] < max_bb_width and atr_pct < max_atr
    if not is_range:
        return None

    range_position = (price - bb["lower"]) / (bb["upper"] - bb["lower"]) if bb["upper"] != bb["lower"] else 0.5

    score = 0
    reasons = []
    direction = None

    if range_position < 0.25 and rsi < entry_cfg.get("rsiOversold", 35):
        direction = "LONG"
        score += 3
        reasons.append(f"near_support_{range_position:.0%}")
        score += 2
        reasons.append(f"rsi_oversold_{rsi:.0f}")
    elif range_position > 0.75 and rsi > entry_cfg.get("rsiOverbought", 65):
        direction = "SHORT"
        score += 3
        reasons.append(f"near_resistance_{range_position:.0%}")
        score += 2
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    else:
        return None

    if bb["width_pct"] < 2.0:
        score += 1
        reasons.append("bb_squeeze")

    if vol_declining:
        score += 1
        reasons.append("vol_declining")

    if atr_pct < 0.8:
        score += 1
        reasons.append("low_atr")

    return {
        "coin": coin, "direction": direction, "score": score,
        "reasons": reasons, "price": price, "bb": bb, "rsi": rsi,
        "atr_pct": atr_pct, "range_position": range_position,
    }


# ─── Main ─────────────────────────────────────────────────────

def run():
    config = cfg.load_config()
    wallet, _ = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": f"gate={tc['gate']}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    max_positions = config.get("maxPositions", 3)
    active_coins = {p["coin"] for p in positions}

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"max positions ({len(positions)}/{max_positions})"})
        return

    entry_cfg = config.get("entry", {})
    cooldown_min = entry_cfg.get("assetCooldownMinutes", 240)

    # ── Gate 1: Get BTC regime (checked per-signal below) ─────
    regime = get_btc_regime()

    # ── Scan for range-bound setups ───────────────────────────
    candidates = get_scan_candidates(entry_cfg)
    signals = []

    for cand in candidates:
        coin = cand["coin"]

        if coin in active_coins:
            continue

        # Gate 2: Per-asset cooldown after losses
        if is_asset_cooled_down(coin, cooldown_min):
            continue

        result = analyze_asset(coin, entry_cfg)
        if not result or result["score"] < entry_cfg.get("minScore", 5):
            continue

        # Gate 1: BTC regime filter
        if not regime_allows_direction(regime, result["direction"]):
            continue

        result["regime"] = regime
        signals.append(result)

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"scanned {len(candidates)}, regime={regime}, no qualifying range setups"})
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    # Gate 3: Leverage hard cap
    lev_cfg = config.get("leverage", {})
    leverage = lev_cfg.get("default", 8)
    leverage = min(leverage, lev_cfg.get("max", 10))

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
        "regime": regime,
        "scanned": len(candidates),
        "candidates": len(signals),
    })


if __name__ == "__main__":
    run()
