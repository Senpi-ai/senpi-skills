#!/usr/bin/env python3
# Senpi KODIAK Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""KODIAK v2.0 — SOL Alpha Hunter with Position Lifecycle.

Single-asset focus. SOL only. Every signal source available (SM, funding, OI,
4-timeframe trend, volume, BTC correlation). 15-20x leverage, maximum conviction.

v2.0 adds three-mode position lifecycle:
  MODE 1 — HUNTING: normal scanning, all signals must align, score 10+ to enter
  MODE 2 — RIDING: position open, DSL trails, monitor thesis
  MODE 3 — STALKING: DSL closed, watch for reload on dip, or reset if thesis dies

The loop: HUNTING → enter → RIDING → DSL closes → STALKING → reload or reset.

Runs every 3 minutes.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kodiak_config as cfg


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


# ─── Asset-Specific Data ────────────────────────────────────────

def get_sol_full_picture():
    """Fetch comprehensive SOL data across all timeframes."""
    data = cfg.mcporter_call("market_get_asset_data", asset="SOL",
                              candle_intervals=["5m", "15m", "1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None
    return data.get("data", data)


def get_btc_correlation():
    """Fetch BTC data to check if it confirms SOL's move (BTC as correlation)."""
    data = cfg.mcporter_call("market_get_asset_data", asset="BTC",
                              candle_intervals=["15m", "1h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return None, None
    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    mom_15m = price_momentum(candles_15m, 1) if len(candles_15m) >= 2 else 0
    mom_1h = price_momentum(candles_1h, 1) if len(candles_1h) >= 2 else 0
    return mom_15m, mom_1h


def get_sol_sm_direction():
    """Get smart money positioning specifically for SOL."""
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0, 0

    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    # Aggregate long vs short entries for SOL
    asset_long_pct = 0
    asset_short_pct = 0
    asset_traders = 0
    found = False

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if token != "SOL":
            continue
        found = True
        direction = m.get("direction", "").lower()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders = int(m.get("trader_count", m.get("traderCount", 0)))
        if direction == "long":
            asset_long_pct = pct
            asset_traders += traders
        elif direction == "short":
            asset_short_pct = pct
            asset_traders += traders

    if not found:
        return None, 0, 0

    total = asset_long_pct + asset_short_pct
    if total == 0:
        return "NEUTRAL", 50, asset_traders
    long_ratio = (asset_long_pct / total) * 100 if total > 0 else 50
    if long_ratio > 58:
        return "LONG", long_ratio, asset_traders
    elif long_ratio < 42:
        return "SHORT", 100 - long_ratio, asset_traders
    return "NEUTRAL", 50, asset_traders


# ─── Thesis Builder (BTC Only) ───────────────────────────────

def build_sol_thesis(entry_cfg):
    """Build a conviction thesis from every SOL signal source."""

    sol_data = get_sol_full_picture()
    if not sol_data:
        return None

    candles_5m = sol_data.get("candles", {}).get("5m", [])
    candles_15m = sol_data.get("candles", {}).get("15m", [])
    candles_1h = sol_data.get("candles", {}).get("1h", [])
    candles_4h = sol_data.get("candles", {}).get("4h", [])
    asset_ctx = sol_data.get("asset_context", {})
    funding = float(asset_ctx.get("funding", sol_data.get("funding", 0)))
    oi = float(asset_ctx.get("openInterest", sol_data.get("openInterest", 0)))

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
    sm_dir, sm_pct, sm_count = get_sol_sm_direction()
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

    # ── BTC correlation confirmation ──────────────────────────
    corr_mom_15m, corr_mom_1h = get_btc_correlation()
    if corr_mom_15m is not None and corr_mom_1h is not None:
        corr_agrees = (direction == "LONG" and corr_mom_15m > 0 and corr_mom_1h > 0) or \
                     (direction == "SHORT" and corr_mom_15m < 0 and corr_mom_1h < 0)
        if corr_agrees:
            score += 1
            reasons.append(f"btc_confirms_{corr_mom_1h:+.2f}%")
        # BTC disagreement is not a block — SOL often follows BTC

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
        "coin": "SOL",
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

def evaluate_sol_position(direction, entry_cfg):
    """Re-evaluate SOL thesis. Returns (still_valid, invalidation_reasons)."""
    sol_data = get_sol_full_picture()
    if not sol_data:
        return True, ["data_unavailable_hold"]

    candles_1h = sol_data.get("candles", {}).get("1h", [])
    candles_4h = sol_data.get("candles", {}).get("4h", [])
    asset_ctx = sol_data.get("asset_context", {})
    funding = float(asset_ctx.get("funding", sol_data.get("funding", 0)))

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
    sm_dir, sm_pct, _ = get_sol_sm_direction()
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

    # ETH diverging strongly? (SOL up, BTC down for 2+ hours = warning)
    corr_15m, corr_1h = get_btc_correlation()
    if corr_1h is not None:
        if direction == "LONG" and corr_1h < -1.0:
            invalidations.append(f"btc_diverging_{corr_1h:+.1f}%")
        elif direction == "SHORT" and corr_1h > 1.0:
            invalidations.append(f"btc_diverging_{corr_1h:+.1f}%")

    return (len(invalidations) == 0), invalidations


# ─── Stalk Evaluation (after DSL exit) ────────────────────────

def evaluate_reload(exit_state, entry_cfg):
    """Check if conditions are met to reload after a DSL exit.
    Returns (should_reload, reasons) or (False, kill_reasons)."""

    stalk_cfg = entry_cfg.get("stalk", {})
    direction = exit_state.get("exitDirection")
    exit_ts = exit_state.get("exitTimestamp", 0)
    exit_vol = exit_state.get("exitEntryVolRatio", 1.0)
    now = cfg.now_ts()
    hours_stalking = (now - exit_ts) / 3600

    # KILL: stalking too long — trend is over
    max_stalk_hours = stalk_cfg.get("maxStalkHours", 6)
    if hours_stalking > max_stalk_hours:
        return False, ["stalk_timeout_{:.1f}h".format(hours_stalking)]

    sol_data = get_sol_full_picture()
    if not sol_data:
        return False, ["data_unavailable"]

    candles_5m = sol_data.get("candles", {}).get("5m", [])
    candles_1h = sol_data.get("candles", {}).get("1h", [])
    candles_4h = sol_data.get("candles", {}).get("4h", [])
    asset_ctx = sol_data.get("asset_context", {})
    funding = float(asset_ctx.get("funding", sol_data.get("funding", 0)))

    kill_reasons = []
    reload_checks = []

    # KILL CHECK: 4h trend reversed
    trend_4h, _ = trend_structure(candles_4h)
    expected_trend = "BULLISH" if direction == "LONG" else "BEARISH"
    if trend_4h != expected_trend and trend_4h != "NEUTRAL":
        kill_reasons.append(f"4h_trend_reversed_{trend_4h}")

    # KILL CHECK: SM flipped
    sm_dir, sm_pct, _ = get_sol_sm_direction()
    if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        kill_reasons.append(f"sm_flipped_{sm_dir}")

    # KILL CHECK: Funding spiked into extreme crowding
    funding_ann = abs(funding) * 8760
    max_funding = stalk_cfg.get("maxFundingAnnPct", 100)
    if (direction == "LONG" and funding > 0 and funding_ann > max_funding) or \
       (direction == "SHORT" and funding < 0 and funding_ann > max_funding):
        kill_reasons.append(f"funding_extreme_{funding_ann:.0f}%ann")

    # KILL CHECK: OI collapsed
    if len(candles_1h) >= 6:
        recent_vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:]]
        earlier_vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-3]]
        avg_recent = sum(recent_vols) / len(recent_vols) if recent_vols else 0
        avg_earlier = sum(earlier_vols) / len(earlier_vols) if earlier_vols else 1
        if avg_earlier > 0:
            oi_change = ((avg_recent - avg_earlier) / avg_earlier) * 100
            if oi_change < -20:
                kill_reasons.append(f"oi_collapsed_{oi_change:+.0f}%")

    if kill_reasons:
        return False, kill_reasons

    # RELOAD CHECK 1: At least one completed 1h candle since exit
    if len(candles_1h) >= 2:
        last_completed_close_ts = candles_1h[-2].get("t", candles_1h[-2].get("T", 0))
        if isinstance(last_completed_close_ts, str):
            last_completed_close_ts = 0
        # Simple check: must have been stalking for at least 30 min (roughly one 1h candle)
        if hours_stalking < 0.5:
            reload_checks.append("waiting_for_1h_candle")

    # RELOAD CHECK 2: Fresh 5m momentum impulse
    if len(candles_5m) >= 3:
        mom_5m_1 = price_momentum(candles_5m, 1)
        mom_5m_2 = price_momentum(candles_5m[:-1], 1)
        if direction == "LONG":
            if mom_5m_1 > 0.15 and mom_5m_1 > mom_5m_2:
                reload_checks.append(f"fresh_5m_impulse_{mom_5m_1:+.2f}%")
            else:
                reload_checks.append("no_5m_impulse")
        else:
            if mom_5m_1 < -0.15 and mom_5m_1 < mom_5m_2:
                reload_checks.append(f"fresh_5m_impulse_{mom_5m_1:+.2f}%")
            else:
                reload_checks.append("no_5m_impulse")

    # RELOAD CHECK 3: OI stable or growing
    if len(candles_1h) >= 4:
        recent_v = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-2:]) / 2
        earlier_v = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-4:-2]) / 2
        if earlier_v > 0 and recent_v >= earlier_v * 0.8:
            reload_checks.append("oi_stable")
        else:
            reload_checks.append("oi_declining")

    # RELOAD CHECK 4: Volume at least 50% of original entry
    min_vol_pct = stalk_cfg.get("minReloadVolPct", 50)
    vol = volume_ratio(candles_5m)
    if vol >= exit_vol * min_vol_pct / 100:
        reload_checks.append(f"vol_sufficient_{vol:.1f}x")
    else:
        reload_checks.append(f"vol_weak_{vol:.1f}x")

    # RELOAD CHECK 5: Funding not crowded
    crowd_threshold = stalk_cfg.get("crowdedFundingAnnPct", 50)
    if (direction == "LONG" and (funding <= 0 or funding_ann < crowd_threshold)) or \
       (direction == "SHORT" and (funding >= 0 or funding_ann < crowd_threshold)):
        reload_checks.append("funding_ok")
    else:
        reload_checks.append(f"funding_crowded_{funding_ann:.0f}%ann")

    # RELOAD CHECK 6: SM still aligned
    if sm_dir == direction:
        reload_checks.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir == "NEUTRAL":
        reload_checks.append("sm_neutral_ok")
    else:
        reload_checks.append(f"sm_not_aligned_{sm_dir}")

    # RELOAD CHECK 7: 4h trend intact
    if trend_4h == expected_trend:
        reload_checks.append("4h_intact")
    else:
        reload_checks.append(f"4h_{trend_4h}")

    # Count passes vs fails
    fails = [r for r in reload_checks if any(bad in r for bad in
              ["no_5m", "oi_declining", "vol_weak", "funding_crowded",
               "sm_not_aligned", "waiting_for"])]

    if not fails:
        return True, reload_checks
    else:
        return False, reload_checks


# ─── Hardcoded Constants & DSL State Builder ─────────────────

MAX_LEVERAGE = 10         # Capped from 15-20x
MIN_LEVERAGE = 7

KODIAK_DSL_TIERS = [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
]

KODIAK_CONVICTION_TIERS = [
    {"minScore": 8,  "absoluteFloorRoe": -25, "hardTimeoutMin": 45, "weakPeakCutMin": 20, "deadWeightCutMin": 15},
    {"minScore": 10, "absoluteFloorRoe": -30, "hardTimeoutMin": 60, "weakPeakCutMin": 30, "deadWeightCutMin": 20},
    {"minScore": 12, "absoluteFloorRoe": -35, "hardTimeoutMin": 90, "weakPeakCutMin": 45, "deadWeightCutMin": 30},
]

KODIAK_STAGNATION_TP = {"enabled": True, "roeMin": 10, "hwStaleMin": 45}


def build_dsl_state_template(direction, score):
    """Build exact DSL state file for a Kodiak SOL position."""
    tier = KODIAK_CONVICTION_TIERS[0]
    for ct in KODIAK_CONVICTION_TIERS:
        if score >= ct["minScore"]:
            tier = ct
    return {
        "active": True, "asset": "SOL", "direction": direction, "score": score,
        "phase": 1, "highWaterPrice": 0, "highWaterRoe": 0,
        "currentTierIndex": -1, "consecutiveBreaches": 0,
        "lockMode": "pct_of_high_water", "phase2TriggerRoe": 7,
        "phase1": {
            "enabled": True, "retraceThreshold": 0.03, "consecutiveBreachesRequired": 3,
            "phase1MaxMinutes": tier["hardTimeoutMin"],
            "weakPeakCutMinutes": tier["weakPeakCutMin"],
            "deadWeightCutMin": tier["deadWeightCutMin"],
            "weakPeakCut": {"enabled": True, "intervalInMinutes": tier["weakPeakCutMin"], "minValue": 3.0},
        },
        "phase2": {"enabled": True, "retraceThreshold": 0.015, "consecutiveBreachesRequired": 2},
        "tiers": KODIAK_DSL_TIERS,
        "stagnationTp": KODIAK_STAGNATION_TP,
        "execution": {"phase1SlOrderType": "MARKET", "phase2SlOrderType": "MARKET", "breachCloseOrderType": "MARKET"},
        "_kodiak_version": "1.1",
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
    entry_cfg = config.get("entry", {})
    state = cfg.load_state("kodiak-state.json")
    current_mode = state.get("currentMode", "HUNTING")
    sol_position = next((p for p in positions if p["coin"] == "SOL"), None)

    # ── MODE 2: RIDING ────────────────────────────────────────
    if sol_position and current_mode in ("RIDING", "HUNTING"):
        # We have a position — ensure we're in RIDING mode
        if current_mode != "RIDING":
            state["currentMode"] = "RIDING"
            cfg.save_state(state, "kodiak-state.json")

        still_valid, reasons = evaluate_sol_position(sol_position["direction"], entry_cfg)
        if not still_valid:
            cfg.output({
                "success": True,
                "action": "thesis_exit",
                "exits": [{
                    "coin": "SOL",
                    "direction": sol_position["direction"],
                    "reasons": reasons,
                    "upnl": sol_position.get("upnl", 0),
                }],
                "note": "SOL thesis invalidated — conviction broken",
            })
            # On thesis exit, go to HUNTING (thesis is dead, don't stalk)
            state["currentMode"] = "HUNTING"
            state.pop("exitState", None)
            cfg.save_state(state, "kodiak-state.json")
            return

        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"RIDING: SOL {sol_position['direction']} thesis intact"})
        cfg.save_state(state, "kodiak-state.json")
        return

    # ── Detect DSL exit: was RIDING, now no position ──────────
    if not sol_position and current_mode == "RIDING":
        # DSL closed our position. Transition to STALKING.
        # Record exit state for reload evaluation
        sol_data = get_sol_full_picture()
        exit_vol = 1.0
        if sol_data:
            candles_5m = sol_data.get("candles", {}).get("5m", [])
            exit_vol = volume_ratio(candles_5m) if candles_5m else 1.0

        state["currentMode"] = "STALKING"
        state["exitState"] = {
            "exitDirection": state.get("lastDirection", "LONG"),
            "exitTimestamp": cfg.now_ts(),
            "exitEntryVolRatio": exit_vol,
        }
        cfg.save_state(state, "kodiak-state.json")
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": "DSL closed position — transitioning to STALKING mode"})
        return

    # ── MODE 3: STALKING ──────────────────────────────────────
    if current_mode == "STALKING":
        exit_state = state.get("exitState", {})
        if not exit_state:
            # Corrupted state — reset
            state["currentMode"] = "HUNTING"
            cfg.save_state(state, "kodiak-state.json")
        else:
            should_reload, reasons = evaluate_reload(exit_state, entry_cfg)

            if should_reload:
                # RELOAD: re-enter same direction
                direction = exit_state["exitDirection"]
                lev_cfg = config.get("leverage", {})
                leverage = min(lev_cfg.get("default", 15), MAX_LEVERAGE)
                base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
                margin = round(account_value * base_margin_pct, 2)

                state["currentMode"] = "RIDING"
                state["lastDirection"] = direction
                state.pop("exitState", None)
                cfg.save_state(state, "kodiak-state.json")

                cfg.output({
                    "success": True,
                    "action": "reload",
                    "entry": {
                        "coin": "SOL",
                        "direction": direction,
                        "leverage": leverage,
                        "margin": margin,
                        "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
                    },
                    "dslState": build_dsl_state_template(direction, 10),
                    "reasons": reasons,
                    "note": f"STALKING → RELOAD: fresh impulse confirmed, re-entering SOL {direction}",
                })
                return

            # Check for kill conditions (returned as reasons when should_reload=False)
            kill_signals = [r for r in reasons if any(k in r for k in
                           ["stalk_timeout", "4h_trend_reversed", "sm_flipped",
                            "funding_extreme", "oi_collapsed"])]

            if kill_signals:
                state["currentMode"] = "HUNTING"
                state.pop("exitState", None)
                cfg.save_state(state, "kodiak-state.json")
                cfg.output({"success": True, "heartbeat": "NO_REPLY",
                             "note": f"STALKING → RESET: {kill_signals[0]}"})
                return

            # Still stalking, conditions not met yet
            hours = (cfg.now_ts() - exit_state.get("exitTimestamp", cfg.now_ts())) / 3600
            cfg.output({"success": True, "heartbeat": "NO_REPLY",
                         "note": f"STALKING {hours:.1f}h — waiting for reload conditions"})
            cfg.save_state(state, "kodiak-state.json")
            return

    # ── MODE 1: HUNTING ───────────────────────────────────────
    # Entry cap
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

    # Build BTC thesis
    thesis = build_sol_thesis(entry_cfg)

    if not thesis or thesis["score"] < entry_cfg.get("minScore", 10):
        note = "no SOL thesis" if not thesis else f"SOL score {thesis['score']} below {entry_cfg.get('minScore', 10)}"
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": note})
        return

    # Conviction-scaled leverage
    lev_cfg = config.get("leverage", {})
    if thesis["score"] >= 14:
        leverage = min(lev_cfg.get("max", 20), MAX_LEVERAGE)
    elif thesis["score"] >= 12:
        leverage = min(lev_cfg.get("high", 18), MAX_LEVERAGE)
    elif thesis["score"] >= 10:
        leverage = min(lev_cfg.get("default", 15), MAX_LEVERAGE)
    else:
        leverage = min(lev_cfg.get("min", 12), MAX_LEVERAGE)

    # Conviction-scaled margin
    base_margin_pct = entry_cfg.get("marginPctBase", 0.30)
    if thesis["score"] >= 14:
        margin_pct = base_margin_pct * 1.5
    elif thesis["score"] >= 12:
        margin_pct = base_margin_pct * 1.25
    else:
        margin_pct = base_margin_pct
    margin = round(account_value * margin_pct, 2)

    # Enter and switch to RIDING
    state["currentMode"] = "RIDING"
    state["lastDirection"] = thesis["direction"]
    cfg.save_state(state, "kodiak-state.json")

    cfg.output({
        "success": True,
        "signal": thesis,
        "entry": {
            "coin": "SOL",
            "direction": thesis["direction"],
            "leverage": leverage,
            "margin": margin,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "dslState": build_dsl_state_template(thesis["direction"], thesis["score"]),
        "constraints": {
            "minLeverage": MIN_LEVERAGE,
            "maxLeverage": MAX_LEVERAGE,
            "stagnationTp": KODIAK_STAGNATION_TP,
            "dslTiers": KODIAK_DSL_TIERS,
            "_dslNote": "Use dslState as the DSL state file. Do NOT merge with dsl-profile.json.",
        },
    })


if __name__ == "__main__":
    run()
