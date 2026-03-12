#!/usr/bin/env python3
# Senpi COBRA Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""COBRA v2.0 — Volume-Momentum Convergence.

Enters only when three independent signals converge:
1. Price momentum (multi-timeframe: 5m + 15m + 1h all agreeing)
2. Volume confirmation (current bar volume 1.8x+ above average)
3. Open interest confirmation (OI increasing = new money entering)

v2.0 changes from v1.0:
  - minScore: 8 → 10
  - minVolRatio: 1.3 → 1.8
  - minMomentum5mPct: 0.3 → 0.5
  - SM: score penalty → hard block
  - Added thesis re-evaluation for held positions
  - Added DSL High Water Mode
  - maxPositions: 4 → 3
  - Dynamic slots base: 4 → 3

Runs every 5 minutes (was 3 — reduced to cut marginal entries).
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cobra_config as cfg


# ─── Technical Helpers ────────────────────────────────────────

def price_momentum(candles, n_bars=1):
    """Price change % over last n bars."""
    if len(candles) < n_bars + 1:
        return 0
    old = float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def volume_ratio(candles, lookback=10):
    """Latest bar volume vs average of previous bars."""
    if len(candles) < lookback + 1:
        return 1.0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = float(candles[-1].get("volume", candles[-1].get("v", candles[-1].get("vlm", 0))))
    return latest / avg if avg > 0 else 1.0


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
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


def extract_closes(candles):
    return [float(c.get("close", c.get("c", 0))) for c in candles if c.get("close") or c.get("c")]


# ─── OI Tracking ──────────────────────────────────────────────

def get_oi_change(coin):
    """Get OI change over recent period. Positive = new money entering."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["15m"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None, None, None

    asset_data = data.get("data", data)

    # Current OI
    oi = float(asset_data.get("openInterest", 0))

    # Funding rate
    funding = float(asset_data.get("funding", 0))

    # OI from candle data (if available)
    candles = asset_data.get("candles", {}).get("15m", [])
    if len(candles) >= 4:
        # Estimate OI trend from volume trend (proxy)
        recent_vol = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-2:])
        earlier_vol = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-4:-2])
        oi_trend = (recent_vol - earlier_vol) / earlier_vol * 100 if earlier_vol > 0 else 0
    else:
        oi_trend = 0

    return oi, oi_trend, funding


def get_sm_direction(coin):
    """Check smart money direction for this asset."""
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0

    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", []))

    for m in markets:
        if m.get("coin", m.get("asset", "")) == coin:
            long_pct = float(m.get("longPct", m.get("pctOfGainsLong", 50)))
            if long_pct > 60:
                return "LONG", long_pct
            elif long_pct < 40:
                return "SHORT", 100 - long_pct
            else:
                return "NEUTRAL", 50
    return None, 0


# ─── Candidate Discovery ─────────────────────────────────────

def get_candidates():
    """Get top assets by USD OI for scanning."""
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
        mark_px = float(inst.get("markPx", inst.get("midPx", 0)))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if coin and oi_usd > 5_000_000:
            candidates.append({"coin": coin, "oi_usd": oi_usd, "price": mark_px})
    candidates.sort(key=lambda x: x["oi_usd"], reverse=True)
    return candidates[:25]


# ─── Core Signal: Triple Convergence ──────────────────────────

def analyze_asset(coin, entry_cfg):
    """Check for volume-momentum-OI convergence on a single asset."""

    # Fetch candle data across three timeframes
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["5m", "15m", "1h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None

    candles_5m = data.get("data", {}).get("candles", {}).get("5m", [])
    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 4:
        return None

    # ── SIGNAL 1: Multi-timeframe momentum convergence ────────
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 1)

    min_mom_5m = entry_cfg.get("minMomentum5mPct", 0.3)
    min_mom_15m = entry_cfg.get("minMomentum15mPct", 0.15)

    # All three timeframes must agree on direction
    if mom_5m > min_mom_5m and mom_15m > min_mom_15m and mom_1h > 0:
        direction = "LONG"
    elif mom_5m < -min_mom_5m and mom_15m < -min_mom_15m and mom_1h < 0:
        direction = "SHORT"
    else:
        return None  # No convergence — timeframes disagree

    # ── SIGNAL 2: Volume confirmation ─────────────────────────
    vol_5m = volume_ratio(candles_5m)
    vol_15m = volume_ratio(candles_15m)
    min_vol = entry_cfg.get("minVolRatio", 1.3)

    if vol_5m < min_vol:
        return None  # Move without volume = fake

    # ── SIGNAL 3: OI confirmation ─────────────────────────────
    oi, oi_trend, funding = get_oi_change(coin)
    min_oi_trend = entry_cfg.get("minOiTrendPct", 2.0)

    # OI should be increasing (new money entering)
    oi_confirmed = oi_trend is not None and oi_trend > min_oi_trend

    # If OI is decreasing, the move is driven by closing — weaker signal
    if oi_trend is not None and oi_trend < -5.0:
        return None  # OI collapsing — move is driven by liquidations, not new conviction

    # ── SCORING ───────────────────────────────────────────────
    score = 0
    reasons = []

    # Momentum strength
    score += 3
    reasons.append(f"3TF_converge_{direction.lower()}")
    reasons.append(f"5m:{mom_5m:+.2f}%_15m:{mom_15m:+.2f}%_1h:{mom_1h:+.2f}%")

    if abs(mom_5m) > min_mom_5m * 2:
        score += 1
        reasons.append("strong_5m_momentum")

    # Volume strength
    score += 2
    reasons.append(f"vol_{vol_5m:.1f}x")
    if vol_5m > 2.0:
        score += 1
        reasons.append("vol_spike")

    # OI confirmation
    if oi_confirmed:
        score += 2
        reasons.append(f"oi_growing_{oi_trend:+.1f}%")
    else:
        # OI flat — still valid but weaker
        score += 1
        reasons.append("oi_flat")

    # ── BOOSTERS (optional, not required) ─────────────────────

    # SM alignment booster
    sm_dir, sm_pct = get_sm_direction(coin)
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        # SM opposes — hard block, not just a penalty
        if entry_cfg.get("smHardBlock", True):
            return None
        score -= 1
        reasons.append(f"sm_opposes_{sm_dir}")

    # Funding alignment booster
    if funding is not None:
        if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0):
            score += 1
            reasons.append("funding_aligned")
        elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.005):
            score -= 1
            reasons.append("funding_opposes")

    # RSI filter — don't enter overbought/oversold
    closes_15m = extract_closes(candles_15m)
    rsi = calc_rsi(closes_15m)
    if direction == "LONG" and rsi > entry_cfg.get("rsiMaxLong", 75):
        return None  # Already overbought
    if direction == "SHORT" and rsi < entry_cfg.get("rsiMinShort", 25):
        return None  # Already oversold
    if (direction == "LONG" and rsi < 45) or (direction == "SHORT" and rsi > 55):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    price = float(candles_5m[-1].get("close", candles_5m[-1].get("c", 0)))

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "momentum": {"5m": mom_5m, "15m": mom_15m, "1h": mom_1h},
        "volume": {"5m": vol_5m, "15m": vol_15m},
        "oi_trend": oi_trend,
        "rsi": rsi,
        "funding": funding,
    }


# ─── Main ─────────────────────────────────────────────────────

# ─── Thesis Re-Evaluation (for held positions) ───────────────

def evaluate_held_position(coin, direction, entry_cfg):
    """Re-evaluate the triple convergence thesis. Returns (still_valid, reasons)."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["5m", "15m", "1h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return True, ["data_unavailable_hold"]

    candles_5m = data.get("data", {}).get("candles", {}).get("5m", [])
    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])

    if len(candles_5m) < 6 or len(candles_1h) < 6:
        return True, ["insufficient_data_hold"]

    invalidations = []

    # CHECK 1: Multi-timeframe momentum broken?
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1) if len(candles_15m) >= 2 else 0
    mom_1h = price_momentum(candles_1h, 1)

    # If 2 of 3 timeframes disagree with our direction, convergence is broken
    agree_count = 0
    if direction == "LONG":
        if mom_5m > 0: agree_count += 1
        if mom_15m > 0: agree_count += 1
        if mom_1h > 0: agree_count += 1
    else:
        if mom_5m < 0: agree_count += 1
        if mom_15m < 0: agree_count += 1
        if mom_1h < 0: agree_count += 1

    if agree_count < 2:
        invalidations.append(f"convergence_broken_{agree_count}of3_agree")

    # CHECK 2: Volume dried up?
    vol = volume_ratio(candles_5m)
    if vol < 0.5:
        invalidations.append(f"volume_died_{vol:.1f}x")

    # CHECK 3: SM flipped against us?
    sm_dir, _ = get_sm_direction(coin)
    if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        invalidations.append(f"sm_flipped_{sm_dir}")

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
    active_coins = {p["coin"] for p in positions}
    entry_cfg = config.get("entry", {})

    # CHECK 1: Re-evaluate thesis for held positions
    for pos in positions:
        still_valid, reasons = evaluate_held_position(pos["coin"], pos["direction"], entry_cfg)
        if not still_valid:
            cfg.output({
                "success": True,
                "action": "thesis_exit",
                "exits": [{
                    "coin": pos["coin"],
                    "direction": pos["direction"],
                    "reasons": reasons,
                    "upnl": pos.get("upnl", 0),
                }],
                "note": "triple convergence broken — exit position",
            })
            return

    # CHECK 2: Dynamic slots
    dynamic = entry_cfg.get("dynamicSlots", {})
    if dynamic.get("enabled", True):
        base_max = dynamic.get("baseMax", 3)
        day_pnl = tc.get("realizedPnl", 0)
        effective_max = base_max
        for threshold in dynamic.get("unlockThresholds", []):
            if day_pnl >= threshold.get("pnl", 999999):
                effective_max = threshold.get("maxEntries", effective_max)
        max_entries = min(effective_max, dynamic.get("absoluteMax", 6))
    else:
        max_entries = entry_cfg.get("maxEntriesPerDay", 4)

    if tc.get("entries", 0) >= max_entries:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": f"max entries ({max_entries})"})
        return

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": f"max positions ({max_positions})"})
        return

    # CHECK 3: Scan candidates
    candidates = get_candidates()
    signals = []
    min_score = entry_cfg.get("minScore", 10)

    for cand in candidates:
        if cand["coin"] in active_coins:
            continue
        result = analyze_asset(cand["coin"], entry_cfg)
        if result and result["score"] >= min_score:
            signals.append(result)

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"scanned {len(candidates)}, no convergence"})
        return

    # Pick best signal
    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    # Margin: conviction-scaled
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    if best["score"] >= 14:
        margin_pct = base_margin_pct * 1.5
    elif best["score"] >= 12:
        margin_pct = base_margin_pct * 1.25
    else:
        margin_pct = base_margin_pct
    margin = round(account_value * margin_pct, 2)

    leverage = config.get("leverage", {}).get("default", 10)

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
        "dynamicSlotsMax": max_entries,
    })


if __name__ == "__main__":
    run()
