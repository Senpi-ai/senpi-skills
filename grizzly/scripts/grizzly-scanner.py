#!/usr/bin/env python3
# Senpi GRIZZLY Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0 — attribution required for derivative works
# Source: https://github.com/Senpi-ai/senpi-skills
"""GRIZZLY Scanner — BTC Alpha Hunter.

Single-asset focus. Stares at BTC and nothing else — reads every signal
available (SM positioning, funding, OI, multi-timeframe trend, volume,
ETH correlation) to build the highest-conviction BTC entries at 15-20x leverage.

Deepest liquidity, most SM data, tightest spreads, most structural moves.
DSL High Water Mode with BTC-specific wide tiers.

Runs every 3 minutes.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grizzly_config as cfg


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


def volume_ratio(candles, lookback=10):
    if len(candles) < lookback + 1:
        return 1.0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = float(candles[-1].get("volume", candles[-1].get("v", candles[-1].get("vlm", 0))))
    return latest / avg if avg > 0 else 1.0


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


# ─── BTC-Specific Data ────────────────────────────────────────

def get_btc_full_picture():
    """Fetch comprehensive BTC data across all timeframes."""
    data = cfg.mcporter_call("market_get_asset_data", asset="BTC",
                              candle_intervals=["5m", "15m", "1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None
    return data.get("data", data)


def get_eth_correlation():
    """Fetch ETH data to check if it confirms BTC's move."""
    data = cfg.mcporter_call("market_get_asset_data", asset="ETH",
                              candle_intervals=["15m", "1h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return None, None
    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    mom_15m = price_momentum(candles_15m, 1) if len(candles_15m) >= 2 else 0
    mom_1h = price_momentum(candles_1h, 1) if len(candles_1h) >= 2 else 0
    return mom_15m, mom_1h


def get_btc_sm_direction():
    """Get smart money positioning specifically for BTC."""
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0, 0

    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", []))

    for m in markets:
        if m.get("coin", m.get("asset", "")) == "BTC":
            long_pct = float(m.get("longPct", m.get("pctOfGainsLong", 50)))
            trader_count = int(m.get("traderCount", m.get("numTraders", 0)))
            if long_pct > 58:
                return "LONG", long_pct, trader_count
            elif long_pct < 42:
                return "SHORT", 100 - long_pct, trader_count
            return "NEUTRAL", 50, trader_count
    return None, 0, 0


# ─── Thesis Builder (BTC Only) ───────────────────────────────

def build_btc_thesis(entry_cfg):
    """Build a conviction thesis from every BTC signal source."""

    btc_data = get_btc_full_picture()
    if not btc_data:
        return None

    candles_5m = btc_data.get("candles", {}).get("5m", [])
    candles_15m = btc_data.get("candles", {}).get("15m", [])
    candles_1h = btc_data.get("candles", {}).get("1h", [])
    candles_4h = btc_data.get("candles", {}).get("4h", [])
    funding = float(btc_data.get("funding", 0))
    oi = float(btc_data.get("openInterest", 0))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return None

    price = float(candles_5m[-1].get("close", candles_5m[-1].get("c", 0)))

    # ── REQUIRED: 4h trend structure ──────────────────────────
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None  # No conviction without macro structure

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ── REQUIRED: 1h trend agrees ─────────────────────────────
    trend_1h, trend_strength_1h = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    # ── REQUIRED: 15m momentum confirms ───────────────────────
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)

    min_mom_15m = entry_cfg.get("minMom15mPct", 0.1)
    if direction == "LONG" and mom_15m < min_mom_15m:
        return None
    if direction == "SHORT" and mom_15m > -min_mom_15m:
        return None

    # ── SCORING ───────────────────────────────────────────────
    score = 0
    reasons = []

    # 4h trend (3 pts — the foundation)
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{trend_strength_4h:.0%}")

    # 1h trend agreement (2 pts)
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")

    # 15m momentum (1 pt — already required, but strength matters)
    if abs(mom_15m) > min_mom_15m * 2:
        score += 1
        reasons.append(f"15m_strong_{mom_15m:+.2f}%")
    else:
        reasons.append(f"15m_{mom_15m:+.2f}%")

    # 5m alignment (1 pt — all 4 timeframes agree)
    if (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0):
        score += 1
        reasons.append("4TF_aligned")

    # ── SM positioning (BTC-specific, very strong signal) ─────
    sm_dir, sm_pct, sm_count = get_btc_sm_direction()
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}traders")
        if sm_pct > 65:
            score += 1
            reasons.append("sm_strongly_tilted")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        # SM opposes — hard block for BTC. SM has the best read on BTC.
        return None

    # ── Funding alignment ─────────────────────────────────────
    if (direction == "LONG" and funding < 0):
        score += 2
        reasons.append(f"funding_pays_longs_{funding:+.4f}")
    elif (direction == "SHORT" and funding > 0):
        score += 2
        reasons.append(f"funding_pays_shorts_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.005) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append(f"funding_crowded_{funding:+.4f}")

    # ── Volume confirmation ───────────────────────────────────
    vol_1h = volume_ratio(candles_1h)
    min_vol = entry_cfg.get("minVolRatio", 1.2)
    if vol_1h >= min_vol:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.7:
        score -= 1
        reasons.append("vol_weak")

    vol_trend_1h = volume_trend(candles_1h)
    if vol_trend_1h > 15:
        score += 1
        reasons.append(f"vol_rising_{vol_trend_1h:+.0f}%")

    # ── OI growth (new money entering BTC) ────────────────────
    vol_recent = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:])
    vol_earlier = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1
        reasons.append(f"oi_growing_{oi_proxy:+.0f}%")

    # ── ETH correlation confirmation ──────────────────────────
    eth_mom_15m, eth_mom_1h = get_eth_correlation()
    if eth_mom_15m is not None and eth_mom_1h is not None:
        eth_agrees = (direction == "LONG" and eth_mom_15m > 0 and eth_mom_1h > 0) or \
                     (direction == "SHORT" and eth_mom_15m < 0 and eth_mom_1h < 0)
        if eth_agrees:
            score += 1
            reasons.append(f"eth_confirms_{eth_mom_1h:+.2f}%")
        # ETH disagreement is not a block — BTC leads, ETH follows

    # ── RSI filter ────────────────────────────────────────────
    closes_1h = [float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > entry_cfg.get("rsiMaxLong", 74):
        return None
    if direction == "SHORT" and rsi < entry_cfg.get("rsiMinShort", 26):
        return None
    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # ── 4h momentum strength bonus ────────────────────────────
    if abs(mom_4h) > 1.0:
        score += 1
        reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

    return {
        "coin": "BTC",
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "momentum": {"5m": mom_5m, "15m": mom_15m, "1h": mom_1h, "4h": mom_4h},
        "sm_direction": sm_dir,
        "sm_pct": sm_pct,
        "funding": funding,
        "rsi": rsi,
        "vol_ratio": vol_1h,
    }


# ─── Thesis Re-Evaluation ────────────────────────────────────

def evaluate_btc_position(direction, entry_cfg):
    """Re-evaluate BTC thesis. Returns (still_valid, invalidation_reasons)."""
    btc_data = get_btc_full_picture()
    if not btc_data:
        return True, ["data_unavailable_hold"]

    candles_1h = btc_data.get("candles", {}).get("1h", [])
    candles_4h = btc_data.get("candles", {}).get("4h", [])
    funding = float(btc_data.get("funding", 0))

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
    sm_dir, sm_pct, _ = get_btc_sm_direction()
    if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        invalidations.append(f"sm_flipped_{sm_dir}_{sm_pct:.0f}%")

    # Funding extreme against position?
    threshold = entry_cfg.get("fundingExtremeThreshold", 0.012)
    if direction == "LONG" and funding > threshold:
        invalidations.append(f"funding_extreme_{funding:+.4f}")
    elif direction == "SHORT" and funding < -threshold:
        invalidations.append(f"funding_extreme_{funding:+.4f}")

    # Volume died for 3+ hours?
    if len(candles_1h) >= 12:
        recent_vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:]]
        avg_vol = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-12:-3]) / 9
        if avg_vol > 0 and all(v < avg_vol * 0.3 for v in recent_vols):
            invalidations.append("volume_dried_up_3h")

    # ETH diverging strongly? (BTC up, ETH down for 2+ hours = warning)
    eth_15m, eth_1h = get_eth_correlation()
    if eth_1h is not None:
        if direction == "LONG" and eth_1h < -1.0:
            invalidations.append(f"eth_diverging_{eth_1h:+.1f}%")
        elif direction == "SHORT" and eth_1h > 1.0:
            invalidations.append(f"eth_diverging_{eth_1h:+.1f}%")

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
    entry_cfg = config.get("entry", {})
    btc_position = next((p for p in positions if p["coin"] == "BTC"), None)

    # CHECK 1: Re-evaluate thesis if holding BTC
    if btc_position:
        still_valid, reasons = evaluate_btc_position(btc_position["direction"], entry_cfg)
        if not still_valid:
            cfg.output({
                "success": True,
                "action": "thesis_exit",
                "exits": [{
                    "coin": "BTC",
                    "direction": btc_position["direction"],
                    "reasons": reasons,
                    "upnl": btc_position.get("upnl", 0),
                }],
                "note": "BTC thesis invalidated — conviction broken",
            })
            return
        # Thesis intact — hold
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"BTC {btc_position['direction']} thesis intact, holding"})
        return

    # CHECK 2: Entry cap
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

    # CHECK 3: Build BTC thesis
    thesis = build_btc_thesis(entry_cfg)

    if not thesis or thesis["score"] < entry_cfg.get("minScore", 10):
        note = "no BTC thesis" if not thesis else f"BTC score {thesis['score']} below {entry_cfg.get('minScore', 10)}"
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": note})
        return

    # Conviction-scaled leverage
    lev_cfg = config.get("leverage", {})
    if thesis["score"] >= 14:
        leverage = lev_cfg.get("max", 20)
    elif thesis["score"] >= 12:
        leverage = lev_cfg.get("high", 18)
    elif thesis["score"] >= 10:
        leverage = lev_cfg.get("default", 15)
    else:
        leverage = lev_cfg.get("min", 12)

    # Conviction-scaled margin
    base_margin_pct = entry_cfg.get("marginPctBase", 0.30)
    if thesis["score"] >= 14:
        margin_pct = base_margin_pct * 1.5
    elif thesis["score"] >= 12:
        margin_pct = base_margin_pct * 1.25
    else:
        margin_pct = base_margin_pct
    margin = round(account_value * margin_pct, 2)

    cfg.output({
        "success": True,
        "signal": thesis,
        "entry": {
            "coin": "BTC",
            "direction": thesis["direction"],
            "leverage": leverage,
            "margin": margin,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
    })


if __name__ == "__main__":
    run()
