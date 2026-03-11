#!/usr/bin/env python3
# Senpi BISON Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0 — attribution required for derivative works
# Source: https://github.com/Senpi-ai/senpi-skills
"""BISON Scanner — Conviction Holder.

Top 10 assets by volume only. Enters when multi-signal thesis converges.
Holds through pullbacks with wide DSL bands that tighten as profit grows.
Re-evaluates thesis every scan — exits when the reason you entered dies,
not when price retraces 1.5%.

The big-game hunter. Fewer trades, longer holds, bigger moves.

Runs every 5 minutes.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bison_config as cfg


# ─── Technical Helpers ────────────────────────────────────────

def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Check if candles form higher lows (bullish) or lower highs (bearish)."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
    highs = [float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def volume_trend(candles, lookback=6):
    if len(candles) < lookback + 2:
        return 0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ─── Data Fetchers ────────────────────────────────────────────

def get_top_assets(n=10):
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    assets = []
    for inst in instruments:
        coin = inst.get("coin") or inst.get("name", "")
        vol = float(inst.get("dayNtlVlm", inst.get("volume24h", 0)))
        mark_px = float(inst.get("markPx", inst.get("midPx", 0)))
        if coin and vol > 0:
            assets.append({"coin": coin, "volume": vol, "price": mark_px})
    assets.sort(key=lambda x: x["volume"], reverse=True)
    return assets[:n]


def get_sm_direction(coin):
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", []))
    for m in markets:
        if m.get("coin", m.get("asset", "")) == coin:
            long_pct = float(m.get("longPct", m.get("pctOfGainsLong", 50)))
            if long_pct > 58:
                return "LONG", long_pct
            elif long_pct < 42:
                return "SHORT", 100 - long_pct
            return "NEUTRAL", 50
    return None, 0


# ─── Thesis Builder ───────────────────────────────────────────

def build_thesis(coin, entry_cfg):
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["15m", "1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None

    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    funding = float(data.get("data", {}).get("funding", 0))

    if len(candles_1h) < 8 or len(candles_4h) < 6:
        return None

    price = float(candles_15m[-1].get("close", candles_15m[-1].get("c", 0))) if candles_15m else 0

    # REQUIRED: 4h trend structure
    trend_4h, trend_strength = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # REQUIRED: 1h trend agrees
    trend_1h, _ = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    # REQUIRED: 1h momentum confirms direction
    mom_1h = price_momentum(candles_1h, 2)
    if direction == "LONG" and mom_1h < entry_cfg.get("minMom1hPct", 0.5):
        return None
    if direction == "SHORT" and mom_1h > -entry_cfg.get("minMom1hPct", 0.5):
        return None

    score = 0
    reasons = []

    # 4h trend (3 pts)
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{trend_strength:.0%}")

    # 1h confirms (2 pts)
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")

    # SM alignment
    sm_dir, sm_pct = get_sm_direction(coin)
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        if entry_cfg.get("smHardBlock", True):
            return None

    # Funding alignment
    if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0):
        score += 2
        reasons.append(f"funding_aligned_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append("funding_crowded")

    # Volume trend
    vol_1h = volume_trend(candles_1h)
    if vol_1h > entry_cfg.get("minVolTrendPct", 10):
        score += 1
        reasons.append(f"vol_rising_{vol_1h:+.0f}%")

    # OI proxy (volume acceleration)
    vol_recent = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:])
    vol_earlier = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1
        reasons.append(f"oi_growing_{oi_proxy:+.0f}%")

    # RSI filter
    closes_1h = [float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > entry_cfg.get("rsiMaxLong", 72):
        return None
    if direction == "SHORT" and rsi < entry_cfg.get("rsiMinShort", 28):
        return None
    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4h momentum strength
    mom_4h = price_momentum(candles_4h, 1)
    if abs(mom_4h) > 1.5:
        score += 1
        reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "price": price, "trend_4h": trend_4h, "momentum_1h": mom_1h,
        "momentum_4h": mom_4h, "sm_direction": sm_dir, "funding": funding,
        "rsi": rsi, "volume_trend": vol_1h,
    }


# ─── Thesis Re-Evaluation ────────────────────────────────────

def evaluate_held_position(coin, direction, entry_cfg):
    """Returns (still_valid, invalidation_reasons)."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return True, ["data_unavailable_hold"]

    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    funding = float(data.get("data", {}).get("funding", 0))

    if len(candles_4h) < 6:
        return True, ["insufficient_data_hold"]

    invalidations = []

    # 4h trend flipped?
    trend_4h, _ = trend_structure(candles_4h)
    if direction == "LONG" and trend_4h == "BEARISH":
        invalidations.append("4h_trend_flipped_bearish")
    elif direction == "SHORT" and trend_4h == "BULLISH":
        invalidations.append("4h_trend_flipped_bullish")

    # SM flipped against?
    sm_dir, _ = get_sm_direction(coin)
    if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        invalidations.append(f"sm_flipped_{sm_dir}")

    # Funding extreme against position?
    threshold = entry_cfg.get("fundingExtremeThreshold", 0.015)
    if direction == "LONG" and funding > threshold:
        invalidations.append(f"funding_extreme_{funding:+.4f}")
    elif direction == "SHORT" and funding < -threshold:
        invalidations.append(f"funding_extreme_{funding:+.4f}")

    # Volume dried up 3+ hours?
    if len(candles_1h) >= 12:
        recent_vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:]]
        avg_vol = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-12:-3]) / 9
        if avg_vol > 0 and all(v < avg_vol * 0.3 for v in recent_vols):
            invalidations.append("volume_dried_up_3h")

    return (len(invalidations) == 0), invalidations


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
    active_coins = {p["coin"]: p for p in positions}
    entry_cfg = config.get("entry", {})

    # CHECK 1: Re-evaluate thesis for held positions
    thesis_exits = []
    for pos in positions:
        still_valid, reasons = evaluate_held_position(pos["coin"], pos["direction"], entry_cfg)
        if not still_valid:
            thesis_exits.append({
                "coin": pos["coin"], "direction": pos["direction"],
                "reasons": reasons, "upnl": pos.get("upnl", 0),
            })

    if thesis_exits:
        cfg.output({
            "success": True, "action": "thesis_exit", "exits": thesis_exits,
            "note": "thesis invalidated — conviction broken, exit position",
        })
        return

    # CHECK 2: Dynamic entry cap
    dynamic = entry_cfg.get("dynamicSlots", {})
    if dynamic.get("enabled", True):
        base_max = dynamic.get("baseMax", 3)
        day_pnl = tc.get("realizedPnl", 0)
        effective_max = base_max
        for t in dynamic.get("unlockThresholds", []):
            if day_pnl >= t.get("pnl", 999999):
                effective_max = t.get("maxEntries", effective_max)
        max_entries = min(effective_max, dynamic.get("absoluteMax", 6))
    else:
        max_entries = config.get("risk", {}).get("maxEntriesPerDay", 4)

    if tc.get("entries", 0) >= max_entries:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": f"max entries ({max_entries})"})
        return

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "max positions"})
        return

    # CHECK 3: Scan top assets for new thesis
    top_n = config.get("topAssets", 10)
    candidates = get_top_assets(top_n)
    min_score = entry_cfg.get("minScore", 8)
    signals = []

    for asset in candidates:
        if asset["coin"] in active_coins:
            continue
        thesis = build_thesis(asset["coin"], entry_cfg)
        if thesis and thesis["score"] >= min_score:
            signals.append(thesis)

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"scanned top {len(candidates)}, no conviction thesis"})
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    if best["score"] >= 12:
        margin_pct = base_margin_pct * 1.5
    elif best["score"] >= 10:
        margin_pct = base_margin_pct * 1.25
    else:
        margin_pct = base_margin_pct
    margin = round(account_value * margin_pct, 2)

    leverage = config.get("leverage", {}).get("default", 10)

    cfg.output({
        "success": True, "signal": best,
        "entry": {
            "coin": best["coin"], "direction": best["direction"],
            "leverage": leverage, "margin": margin,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "scanned": len(candidates), "candidates": len(signals),
    })


if __name__ == "__main__":
    run()
