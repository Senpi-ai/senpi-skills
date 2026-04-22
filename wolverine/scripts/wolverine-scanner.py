#!/usr/bin/env python3
# Senpi WOLVERINE Scanner v3.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""WOLVERINE v3.0 — HYPE Alpha Hunter (Kodiak-family port).

Complete rewrite. v2.x was a rotation-scanner that accumulated -26%
drawdown over 162 trades — textbook Cobra-pattern fee-churn. v3.0 is a
single-asset HYPE specialist built from the Kodiak v5.1 template, with
three modules added that are not in Kodiak:

  1. Drawdown circuit breaker — pause HUNTING on 7-day drawdown > 15%
  2. Funding regime check — from Vulture v2.2
  3. Funding persistence bonus — from Vulture v2.2

## Gate stack (all must pass before entry)

  1. 4h trend BULLISH or BEARISH (not NEUTRAL)
  2. trend_strength_4h >= 0.75 (Kodiak v5.1 pattern)
  3. 1h trend matches 4h
  4. 15m momentum confirms direction with minimum magnitude
  5. Base-tech floor: strong_15m OR 5m_aligned (Kodiak v5.1 pattern)
  6. SM HARD BLOCK if against direction
  7. RSI filter: 72 for LONG, 28 for SHORT (HYPE-tuned)
  8. Drawdown circuit breaker: pause if account < 85% of 7d peak

## Scoring (max ~18)

  4h trend (+3), 1h_confirms (+2), 15m strong (+1), 5m aligned (+1),
  SM aligned (+2), SM strongly tilted (+1), SM 15m fresh (+1 or -3),
  funding aligned (+2), funding regime aligned (+1), funding persistent (+1),
  volume 1h (+1), volume rising (+1), OI accelerating (+2),
  BTC correlation (+1), RSI room (+1), 4h momentum (+1),
  MOVE_EXHAUSTION (-2), MOVE_TIRING (-1).

MIN_SCORE = 9. Conviction-scaled leverage: score >= 11 → 5x, else 3x.

## Sizing constraints

  MARGIN_PCT = 25% of account per position
  MAX_NOTIONAL = 1.5x account value (hard cap)
  MAX_LEVERAGE = 5 (HYPE vol is ~1.5-2x BTC; tight cap vs Kodiak's 10x)

## DSL tiers (tighter than Kodiak SOL — HYPE reverses faster)

  T0: trigger 10%, lock 15% of high-water
  T1: trigger 20%, lock 35%
  T2: trigger 35%, lock 55%
  T3: trigger 55%, lock 70%
  T4: trigger 80%, lock 85%

DSL attach is MANDATORY after every successful entry. If attach fails,
immediate close_position (Dire/Polar v3.0.2 pattern).

## Fleet-standard learnings applied from day 1

  - Config-driven startingBudget (Polar v3.0.1 pattern)
  - Config-driven dslTiers
  - FLAT oi_velocity path (not nested — Cobra-antipattern silent-None fix)
  - FEE_OPTIMIZED_LIMIT with ensureExecutionAsTaker=True, 15s timeout
  - Inner-order success validation in execute_entry

Runs every 3 minutes.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wolverine_config as cfg


VERSION = "3.0"
ASSET = "HYPE"
MAX_POSITIONS = 1


# ═══════════════════════════════════════════════════════════════
# CONFIG-DRIVEN STARTING BUDGET (daily-cap rebase support)
# ═══════════════════════════════════════════════════════════════

def _resolve_starting_budget():
    """Read startingBudget from config.json; default 1000.0.
    Wallet-specific rebases go in user's LOCAL config."""
    try:
        c = cfg.load_config()
        v = c.get("startingBudget")
        if v is not None:
            return float(v)
    except Exception:
        pass
    return 1000.0


STARTING_BUDGET = _resolve_starting_budget()


def get_dynamic_daily_cap(account_value, starting_budget=None):
    """P&L-aware daily entry cap."""
    if starting_budget is None:
        starting_budget = STARTING_BUDGET
    if starting_budget <= 0:
        return 4
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12
    elif pnl_pct >= 0:     return 8
    elif pnl_pct >= -5:    return 5
    elif pnl_pct >= -15:   return 3
    elif pnl_pct >= -25:   return 1
    else:                  return 0


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — HYPE-tuned per v3.0 spec
# ═══════════════════════════════════════════════════════════════

COOLDOWN_MINUTES = 120
SAME_DIR_COOLDOWN_MINUTES = 60
MARGIN_PCT = 0.25
MAX_NOTIONAL_MULT = 1.5
MIN_SCORE = 9

MIN_MOM_15M = 0.15
STRONG_4H_PCT = 2.5
MOVE_EXHAUSTION_PCT = 3.5
MOVE_TIRING_PCT = 2.0
RSI_MAX_LONG = 72
RSI_MIN_SHORT = 28
FUNDING_EXTREME = 0.0008
FUNDING_CROWDED = 0.005

DRAWDOWN_PAUSE_PCT = 0.85
DRAWDOWN_RESUME_PCT = 0.90

LEVERAGE_TIERS = [
    {"min_score": 11, "leverage": 5, "label": "apex"},
    {"min_score": 9,  "leverage": 3, "label": "standard"},
]
DEFAULT_LEVERAGE = 3
MAX_LEVERAGE = 5
MIN_LEVERAGE = 2

DSL_TIERS_DEFAULT = [
    {"triggerRoe": 10, "lockRoe": 15},
    {"triggerRoe": 20, "lockRoe": 35},
    {"triggerRoe": 35, "lockRoe": 55},
    {"triggerRoe": 55, "lockRoe": 70},
    {"triggerRoe": 80, "lockRoe": 85},
]


def _resolve_dsl_tiers():
    """Read dslTiers from config; default matches runtime.yaml."""
    try:
        c = cfg.load_config()
        t = c.get("dslTiers")
        if isinstance(t, list) and t:
            return t
    except Exception:
        pass
    return DSL_TIERS_DEFAULT


# ═══════════════════════════════════════════════════════════════
# TECHNICAL HELPERS (ported from Kodiak v5.1)
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = safe_float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = safe_float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [safe_float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
    highs = [safe_float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
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
    vols = [safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = safe_float(candles[-1].get("volume", candles[-1].get("v", candles[-1].get("vlm", 0))))
    return latest / avg if avg > 0 else 1.0


def volume_trend(candles, lookback=6):
    if len(candles) < lookback + 2:
        return 0
    vols = [safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 2):]]
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
    g = gains[-period:]
    l = losses[-period:]
    avg_g = sum(g) / period
    avg_l = sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ═══════════════════════════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════════════════════════

def get_hype_full_picture():
    data = cfg.mcporter_call(
        "market_get_asset_data", asset=ASSET,
        candle_intervals=["5m", "15m", "1h", "4h"],
        include_funding=True, include_order_book=False,
    )
    if not data or not data.get("success"):
        return None
    return data.get("data", data)


def get_btc_correlation():
    """Pull BTC 15m/1h momentum for correlation scoring."""
    data = cfg.mcporter_call(
        "market_get_asset_data", asset="BTC",
        candle_intervals=["15m", "1h"],
        include_funding=False, include_order_book=False,
    )
    if not data or not data.get("success"):
        return None, None
    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    mom_15m = price_momentum(candles_15m, 1) if len(candles_15m) >= 2 else None
    mom_1h = price_momentum(candles_1h, 1) if len(candles_1h) >= 2 else None
    return mom_15m, mom_1h


def get_hype_sm_direction():
    """Get HYPE SM direction from leaderboard_get_markets.
    Returns (direction, pct, trader_count, cc_15m)."""
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0, 0, 0

    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    long_pct = 0.0
    short_pct = 0.0
    traders = 0
    cc_15m = 0.0
    found = False

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", ""))).upper()
        if token != ASSET:
            continue
        found = True
        direction = str(m.get("direction", "")).lower()
        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        t_count = int(m.get("trader_count", 0))
        cc_15m_val = safe_float(m.get("contribution_pct_change_15m", 0))
        if direction == "long":
            long_pct = pct
            traders += t_count
            cc_15m = cc_15m_val
        elif direction == "short":
            short_pct = pct
            traders += t_count
            cc_15m = cc_15m_val

    if not found:
        return None, 0, 0, 0

    total = long_pct + short_pct
    if total == 0:
        return "NEUTRAL", 50, traders, cc_15m
    long_ratio = (long_pct / total) * 100
    if long_ratio > 58:
        return "LONG", long_ratio, traders, cc_15m
    elif long_ratio < 42:
        return "SHORT", 100 - long_ratio, traders, cc_15m
    return "NEUTRAL", 50, traders, cc_15m


def get_funding_regime():
    """Vulture v2.2 pattern: market-wide funding regime."""
    try:
        r = cfg.mcporter_call("market_get_funding_regime")
        if r:
            return r.get("data", r).get("regime")
    except Exception:
        pass
    return None


def get_funding_history_hype():
    """Vulture v2.2 pattern: per-asset funding persistence."""
    try:
        r = cfg.mcporter_call("market_get_funding_history", asset=ASSET)
        if r:
            data = r.get("data", r)
            return {
                "persistence_hours": data.get("persistence_hours"),
                "trend": data.get("trend"),
            }
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
# DRAWDOWN CIRCUIT BREAKER (v3.0 new module, not in Kodiak)
# ═══════════════════════════════════════════════════════════════

def check_drawdown_gate(account_value, tc):
    """Pause HUNTING if 7-day drawdown > 15%. Resume at 90% recovery.

    Tracks rolling 7-day peak in trade counter state. State fields:
      tc["peak_7d"]: max account_value observed in last 7 days
      tc["peak_ts"]: timestamp of peak
      tc["dd_paused"]: bool, whether currently paused

    Returns (gate_open, reason).
    """
    peak_7d = tc.get("peak_7d", 0)
    peak_ts = tc.get("peak_ts", 0)
    now = time.time()

    if account_value > peak_7d:
        tc["peak_7d"] = account_value
        tc["peak_ts"] = now
        tc["dd_paused"] = False
        return True, f"new_peak_{account_value:.2f}"

    if peak_ts > 0 and (now - peak_ts) > 7 * 86400:
        tc["peak_7d"] = account_value
        tc["peak_ts"] = now
        tc["dd_paused"] = False
        return True, "peak_aged_out_7d_reset"

    if peak_7d <= 0:
        tc["peak_7d"] = account_value
        tc["peak_ts"] = now
        tc["dd_paused"] = False
        return True, "no_peak_init"

    dd_ratio = account_value / peak_7d
    was_paused = tc.get("dd_paused", False)

    if not was_paused and dd_ratio < DRAWDOWN_PAUSE_PCT:
        tc["dd_paused"] = True
        return False, f"dd_pause_trigger_{dd_ratio:.2%}_of_peak_{peak_7d:.2f}"

    if was_paused and dd_ratio >= DRAWDOWN_RESUME_PCT:
        tc["dd_paused"] = False
        return True, f"dd_resumed_{dd_ratio:.2%}_of_peak"

    if was_paused:
        return False, f"dd_paused_{dd_ratio:.2%}_of_peak_{peak_7d:.2f}"

    return True, f"dd_ok_{dd_ratio:.2%}_of_peak"


# ═══════════════════════════════════════════════════════════════
# THESIS BUILDER — Kodiak v5.1 + HYPE tuning + Vulture funding
# ═══════════════════════════════════════════════════════════════

def build_hype_thesis():
    """Build HYPE entry thesis. Returns thesis dict or None on any gate fail."""

    hype_data = get_hype_full_picture()
    if not hype_data:
        return None

    candles_5m = hype_data.get("candles", {}).get("5m", [])
    candles_15m = hype_data.get("candles", {}).get("15m", [])
    candles_1h = hype_data.get("candles", {}).get("1h", [])
    candles_4h = hype_data.get("candles", {}).get("4h", [])
    asset_ctx = hype_data.get("asset_context", hype_data.get("assetContext", {})) or {}
    funding = safe_float(asset_ctx.get("funding", 0))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return None

    price = safe_float(candles_5m[-1].get("close", candles_5m[-1].get("c", 0)))

    # GATE 1: 4h trend structure
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None

    # GATE 2: strong 4h structural alignment (Kodiak v5.1)
    if trend_strength_4h < 0.75:
        return None

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # GATE 3: 1h matches 4h
    trend_1h, trend_strength_1h = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    # GATE 4: 15m momentum confirms
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)

    if direction == "LONG" and mom_15m < MIN_MOM_15M:
        return None
    if direction == "SHORT" and mom_15m > -MIN_MOM_15M:
        return None

    # GATE 5: Base-tech floor (Kodiak v5.1)
    strong_15m = abs(mom_15m) > MIN_MOM_15M * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return None

    # SCORING
    score = 0
    reasons = []

    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{trend_strength_4h:.0%}")
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")
    if strong_15m:
        score += 1
        reasons.append(f"15m_strong_{mom_15m:+.2f}%")
    else:
        reasons.append(f"15m_{mom_15m:+.2f}%")
    if aligned_5m:
        score += 1
        reasons.append("4TF_aligned")

    # SM positioning — HARD BLOCK if opposes
    sm_dir, sm_pct, sm_count, sm_cc_15m = get_hype_sm_direction()
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}traders")
        if sm_pct > 65:
            score += 1
            reasons.append("sm_strongly_tilted")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        return None  # SM HARD BLOCK

    # SM 15m freshness
    if sm_cc_15m <= 0:
        score -= 3
        reasons.append(f"15M_STALE_PENALTY ({sm_cc_15m:.2f})")
    elif sm_cc_15m > 0.5:
        score += 1
        reasons.append(f"15M_FRESH +{sm_cc_15m:.2f}")

    # Funding alignment
    if direction == "LONG" and funding < 0:
        score += 2
        reasons.append(f"funding_pays_longs_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0:
        score += 2
        reasons.append(f"funding_pays_shorts_{funding:+.4f}")
    elif (direction == "LONG" and funding > FUNDING_CROWDED) or \
         (direction == "SHORT" and funding < -FUNDING_CROWDED):
        score -= 1
        reasons.append(f"funding_crowded_{funding:+.4f}")

    # Funding regime (Vulture v2.2)
    regime = get_funding_regime()
    if regime == "LONG_CROWDED" and direction == "LONG":
        score += 1
        reasons.append("REGIME_LONG_CROWDED_aligned")
    elif regime == "SHORT_CROWDED" and direction == "SHORT":
        score += 1
        reasons.append("REGIME_SHORT_CROWDED_aligned")
    elif regime == "LONG_CROWDED" and direction == "SHORT":
        score -= 1
        reasons.append("REGIME_LONG_CROWDED_fighting")
    elif regime == "SHORT_CROWDED" and direction == "LONG":
        score -= 1
        reasons.append("REGIME_SHORT_CROWDED_fighting")
    elif regime is not None:
        reasons.append(f"REGIME_{regime}")

    # Funding persistence (Vulture v2.2)
    fh = get_funding_history_hype()
    if fh:
        ph = fh.get("persistence_hours")
        try:
            ph_val = float(ph) if ph is not None else None
        except (TypeError, ValueError):
            ph_val = None
        if ph_val is not None and ph_val >= 6:
            score += 1
            reasons.append(f"FUNDING_PERSISTENT_{ph_val:.0f}h")

    # Volume
    vol_1h = volume_ratio(candles_1h)
    if vol_1h >= 1.2:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.7:
        score -= 1
        reasons.append("vol_weak")

    vol_trend_1h = volume_trend(candles_1h)
    if vol_trend_1h > 15:
        score += 1
        reasons.append(f"vol_rising_{vol_trend_1h:+.0f}%")

    # OI velocity — FLAT PATH (no Cobra-antipattern silent-None bug)
    oi_vel = hype_data.get("oi_velocity") if isinstance(hype_data.get("oi_velocity"), dict) else {}
    if isinstance(oi_vel, dict):
        oi_change = oi_vel.get("oi_change_pct_1h")
        if oi_change is not None:
            try:
                oi_change = float(oi_change)
                if oi_change > 5:
                    score += 2
                    reasons.append(f"OI_ACCELERATING_{oi_change:+.1f}%")
                elif oi_change > 2:
                    score += 1
                    reasons.append(f"OI_rising_{oi_change:+.1f}%")
                elif oi_change < -3:
                    score -= 1
                    reasons.append(f"OI_draining_{oi_change:+.1f}%")
            except (TypeError, ValueError):
                pass

    # BTC correlation
    corr_mom_15m, corr_mom_1h = get_btc_correlation()
    if corr_mom_15m is not None and corr_mom_1h is not None:
        corr_agrees = (direction == "LONG" and corr_mom_15m > 0 and corr_mom_1h > 0) or \
                     (direction == "SHORT" and corr_mom_15m < 0 and corr_mom_1h < 0)
        if corr_agrees:
            score += 1
            reasons.append(f"btc_confirms_{corr_mom_1h:+.2f}%")

    # RSI filter (HARD GATE for extremes)
    closes_1h = [safe_float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > RSI_MAX_LONG:
        return None
    if direction == "SHORT" and rsi < RSI_MIN_SHORT:
        return None
    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4h momentum bonus
    if abs(mom_4h) > STRONG_4H_PCT:
        score += 1
        reasons.append(f"4h_strong_{mom_4h:+.1f}%")

    # MOVE_EXHAUSTION / MOVE_TIRING penalties
    if abs(mom_4h) >= MOVE_EXHAUSTION_PCT:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION {mom_4h:+.1f}%")
    elif abs(mom_4h) >= MOVE_TIRING_PCT:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1
            reasons.append(f"MOVE_TIRING {mom_4h:+.1f}%")

    return {
        "coin": ASSET,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "trend_4h": trend_4h,
        "trend_strength_4h": trend_strength_4h,
        "momentum": {"5m": mom_5m, "15m": mom_15m, "1h": mom_1h, "4h": mom_4h},
        "sm_direction": sm_dir,
        "sm_pct": sm_pct,
        "funding": funding,
        "regime": regime,
        "rsi": rsi,
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def get_leverage_tier(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"], tier["label"]
    return DEFAULT_LEVERAGE, "default"


def execute_entry(wallet, direction, margin, leverage):
    """Fire create_position with taker fallback."""
    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[{
            "coin": ASSET,
            "direction": direction,
            "leverage": leverage,
            "marginAmount": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "feeOptimizedLimitOptions": {
                "ensureExecutionAsTaker": True,
                "executionTimeoutSeconds": 15,
            },
        }],
    )
    if result and result.get("success"):
        data = result.get("data", {})
        if isinstance(data, dict):
            orders_result = data.get("orders", data.get("results", []))
            if isinstance(orders_result, list) and orders_result:
                inner = orders_result[0]
                if isinstance(inner, dict) and inner.get("success") is False:
                    err = inner.get("error", "inner order failed")
                    return False, {"error": f"INNER_FAILURE: {err}"}
        return True, result
    error = result.get("error", "unknown") if result else "mcporter_call returned None"
    return False, {"error": error}


def has_resting_orders(wallet):
    """Auto-cancel stale resting orders > 600s old."""
    import time as _time
    STALE_ORDER_MAX_AGE_SEC = 600
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = _time.time() * 1000
    max_age_ms = STALE_ORDER_MAX_AGE_SEC * 1000
    has_fresh = False
    for o in orders:
        if o.get("reduceOnly", False):
            continue
        ts_raw = o.get("timestamp", 0) or 0
        try:
            ts = float(ts_raw)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > 0 and (now_ms - ts) > max_age_ms:
            oid = o.get("oid") or o.get("orderId") or o.get("id")
            if oid:
                try:
                    cfg.mcporter_call(
                        "cancel_order",
                        strategyWalletAddress=wallet,
                        orderId=int(oid),
                    )
                except Exception:
                    pass
            continue
        has_fresh = True
    return has_fresh


# ═══════════════════════════════════════════════════════════════
# MAIN — HUNTING or RIDING (DSL manages exits)
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "wallet_not_configured", "version": VERSION})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "error", "heartbeat": "NO_REPLY",
                    "note": f"account_value_zero_or_unreadable account={account_value}",
                    "version": VERSION})
        return

    # RIDING mode — position open, DSL manages exit
    if positions:
        p = positions[0]
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": (f"RIDING: {p['coin']} {p['direction']} "
                     f"upnl=${p['upnl']:.2f} margin=${p['margin']:.2f}"),
            "version": VERSION,
        })
        return

    # HUNTING mode
    tc = cfg.load_trade_counter()

    # Drawdown circuit breaker
    dd_open, dd_reason = check_drawdown_gate(account_value, tc)
    cfg.save_trade_counter(tc)
    if not dd_open:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HUNTING_PAUSED: {dd_reason}",
            "account_value": account_value,
            "peak_7d": tc.get("peak_7d"),
            "version": VERSION,
        })
        return

    # Dynamic daily cap
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": (f"DAILY_CAP_REACHED {tc.get('entries', 0)}/{dynamic_cap} "
                     f"pnl={pnl_pct:+.1f}%"),
            "version": VERSION,
        })
        return

    # Resting order guard
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "RESTING_ORDER_pending", "version": VERSION})
        return

    # Per-asset cooldown
    if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"COOLDOWN_{ASSET}_active_{COOLDOWN_MINUTES}min",
                    "version": VERSION})
        return

    # Build thesis
    thesis = build_hype_thesis()
    if not thesis:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "HUNTING: no thesis (gate blocked)",
                    "version": VERSION})
        return

    if thesis["score"] < MIN_SCORE:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HUNTING_score_low {thesis['score']}/{MIN_SCORE}",
            "direction": thesis["direction"],
            "reasons": thesis["reasons"],
            "version": VERSION,
        })
        return

    direction = thesis["direction"]

    # Same-direction cooldown after a win
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)
    if last_win_dir and last_win_ts and direction == last_win_dir:
        if (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
            remaining = int((SAME_DIR_COOLDOWN_MINUTES * 60 - (time.time() - last_win_ts)) / 60)
            cfg.output({
                "status": "ok", "heartbeat": "NO_REPLY",
                "note": f"SAME_DIR_COOLDOWN won {last_win_dir} {remaining}min ago",
                "thesis_score": thesis["score"],
                "version": VERSION,
            })
            return

    # Sizing
    leverage, tier_label = get_leverage_tier(thesis["score"])
    leverage = max(MIN_LEVERAGE, min(leverage, MAX_LEVERAGE))
    margin = round(account_value * MARGIN_PCT, 2)

    # Enforce max notional hard cap
    notional = margin * leverage
    max_notional = account_value * MAX_NOTIONAL_MULT
    if notional > max_notional:
        margin = round(max_notional / leverage, 2)
        thesis["reasons"].append(f"notional_capped_at_{MAX_NOTIONAL_MULT}x_account")

    if margin < 10 or leverage <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"SIZING_TOO_SMALL lev={leverage} margin={margin}",
                    "version": VERSION})
        return

    # Fire entry
    ok, result = execute_entry(wallet, direction, margin, leverage)
    if not ok:
        cfg.output({
            "status": "error",
            "action": "ENTRY_FAILED",
            "direction": direction,
            "score": thesis["score"],
            "reasons": thesis["reasons"],
            "error": result,
            "version": VERSION,
        })
        return

    # Parse fill details
    fill_price = thesis["price"]
    fill_size_abs = margin * leverage / fill_price if fill_price > 0 else 0
    try:
        data = result.get("data", {}) if isinstance(result, dict) else {}
        orders = data.get("orders", data.get("results", []))
        if orders and isinstance(orders[0], dict):
            main = orders[0].get("mainOrder", orders[0])
            if isinstance(main, dict):
                fp = main.get("fillPrice") or main.get("avgFillPrice") or main.get("price")
                fs = main.get("filledSize") or main.get("filledSz") or main.get("size")
                if fp:
                    try:
                        fill_price = float(fp)
                    except (TypeError, ValueError):
                        pass
                if fs:
                    try:
                        fill_size_abs = abs(float(fs))
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass

    # MANDATORY DSL attach (close-on-fail)
    dsl_tiers = _resolve_dsl_tiers()
    dsl_response = cfg.mcporter_call(
        "ratchet_stop_add",
        strategyId=strategy_id,
        strategy_wallet_address=wallet,
        asset=ASSET,
        direction=direction,
        entryPrice=fill_price,
        size=fill_size_abs,
        leverage=leverage,
        ratchetStopConfig={"tiered": {"tiers": dsl_tiers}},
    )
    dsl_ok = bool(dsl_response) and (
        dsl_response.get("success", False) if isinstance(dsl_response, dict) else False
    )

    if not dsl_ok:
        close_response = cfg.mcporter_call(
            "close_position",
            strategyWalletAddress=wallet,
            coin=ASSET,
        )
        cfg.output({
            "status": "critical",
            "action": "DSL_ATTACH_FAILED_EMERGENCY_CLOSE",
            "direction": direction,
            "score": thesis["score"],
            "reasons": thesis["reasons"],
            "dsl_response": str(dsl_response)[:300] if dsl_response else "null",
            "close_triggered": close_response is not None,
            "version": VERSION,
        })
        return

    # Success — increment counter + set cooldown
    tc["entries"] = tc.get("entries", 0) + 1
    tc["last_entry_ts"] = time.time()
    tc["lastDirection"] = direction
    cfg.save_trade_counter(tc)
    cfg.set_asset_cooldown(ASSET, COOLDOWN_MINUTES)

    cfg.output({
        "status": "ok",
        "action": "ENTRY",
        "direction": direction,
        "coin": ASSET,
        "score": thesis["score"],
        "reasons": thesis["reasons"],
        "execution": {
            "asset": ASSET,
            "direction": direction,
            "leverage": leverage,
            "margin": margin,
            "sizing_tier": tier_label,
            "notional_vs_account": round((leverage * margin) / account_value, 2),
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "ensureExecutionAsTaker": True,
            "fill_price": fill_price,
            "fill_size": fill_size_abs,
        },
        "dsl": {
            "attached": True,
            "tiers": dsl_tiers,
            "entry_price": fill_price,
        },
        "thesis": {
            "rsi": thesis["rsi"],
            "sm_direction": thesis["sm_direction"],
            "sm_pct": thesis["sm_pct"],
            "funding": thesis["funding"],
            "regime": thesis["regime"],
            "trend_4h": thesis["trend_4h"],
            "trend_strength_4h": thesis["trend_strength_4h"],
            "momentum": thesis["momentum"],
        },
        "account": {
            "value": account_value,
            "peak_7d": tc.get("peak_7d"),
            "entries_today": tc.get("entries"),
            "daily_cap": dynamic_cap,
        },
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        import traceback
        cfg.output({
            "status": "error",
            "heartbeat": "NO_REPLY",
            "note": f"scanner_exception: {type(e).__name__}: {str(e)[:200]}",
            "traceback": traceback.format_exc()[-500:],
            "version": VERSION,
        })
