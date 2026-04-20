#!/usr/bin/env python3
# Senpi GRIZZLY Scanner v5.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""GRIZZLY v5.0 — BTC Alpha Hunter with Position Lifecycle.

v5.0 is a COMPLETE REWRITE. Grizzly is now the BTC variant of the
Kodiak/Wolverine/Polar single-asset-specialist family. All four agents
share the same architecture, tuned per asset.

v4.x was a contrarian SM-fader — opposite direction to the family intent.
April 10 inversion test assumed SM was broken on BTC. That was the wrong
read. Grizzly was bleeding because it was fighting the prevailing trend,
not because SM was inverted. v5.0 returns to trend-following with the
full Kodiak architecture.

## Architecture (shared with Kodiak/Wolverine/Polar)

MODE 1 — HUNTING: all signals must align, score >= MIN_SCORE to enter
MODE 2 — RIDING: position open, DSL manages exit, scanner does NOT re-evaluate
MODE 3 — STALKING: DSL closed, watch for reload on dip, or reset if thesis dies

## BTC-specific tuning vs Kodiak (SOL)

BTC moves ~0.6% in 4h vs SOL's 2-4%. All momentum thresholds tightened:
  - min_mom_15m: 0.05 (vs 0.10)
  - move_exhaustion: 2.5% (vs 4.0%)
  - move_tiring: 1.5% (vs 2.5%)
  - rsi_max_long: 70 (vs 74) — BTC rarely pushes that extreme
  - rsi_min_short: 30 (vs 26)
  - vol_ratio_min: 1.1 (vs 1.2) — BTC volume is always deep
  - 4h strong move: 2.0% (vs 4.0%)

BTC SM consensus still works — reuses Kodiak's leaderboard_get_markets
pattern. SM-opposes is a HARD BLOCK (same as Kodiak).

## v5.0 changes from v4.x

- REMOVED: CONTRARIAN_FLIP — direction flips inverted the family thesis
- REMOVED: CONTRARIAN_EXHAUSTION_GATE — was gating contrarian behavior
- ADDED: 3-mode state machine (HUNTING/RIDING/STALKING)
- ADDED: 4-timeframe alignment (5m + 15m + 1h + 4h)
- ADDED: 4TF_aligned bonus scoring
- ADDED: SM strongly tilted bonus (>65% consensus)
- ADDED: SM opposes HARD BLOCK
- ADDED: Move-exhaustion + tiring penalties
- ADDED: RSI filter (70/30 BTC-tuned)
- ADDED: Volume trend + OI-proxy growth signal
- ADDED: STALKING mode — reload on dip after DSL exit
- ADDED: Stalk evaluation kill conditions
- KEPT:   Dynamic daily cap (P&L-aware circuit breaker)
- KEPT:   has_resting_orders with auto-cancel stale
- KEPT:   10x MAX_LEVERAGE cap (fleet-wide learning)
- KEPT:   Same-direction cooldown (60 min)

Runs every 3 minutes.
"""

import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grizzly_config as cfg


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — BTC-tuned (vs Kodiak/SOL)
# ═══════════════════════════════════════════════════════════════

ASSET = "BTC"
MAX_POSITIONS = 1
MAX_LEVERAGE = 10          # Fleet-wide: 10x is the empirical ceiling
MIN_LEVERAGE = 5
MIN_SCORE = 10             # Family standard — was 8 in v4.x
SAME_DIR_COOLDOWN_MINUTES = 60
ASSET_COOLDOWN_MINUTES = 60  # Post-exit cooldown on BTC entry

# BTC-specific momentum thresholds (BTC ~3x slower than SOL)
MIN_MOM_15M = 0.05         # SOL uses 0.10
RSI_MAX_LONG = 70          # SOL uses 74
RSI_MIN_SHORT = 30         # SOL uses 26
STRONG_4H_PCT = 2.0        # BTC "strong" 4h move (SOL: 4.0)
MOVE_EXHAUSTION_PCT = 2.5  # SOL: 4.0
MOVE_TIRING_PCT = 1.5      # SOL: 2.5
MIN_VOL_RATIO = 1.1        # SOL: 1.2
FUNDING_EXTREME = 0.0008   # BTC funding is lower magnitude than alts
FUNDING_CROWDED = 0.003

# Dynamic daily cap
STARTING_BUDGET = 1000.0


# ═══════════════════════════════════════════════════════════════
# Technical Helpers (identical to Kodiak — shared family math)
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
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
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ═══════════════════════════════════════════════════════════════
# BTC Data
# ═══════════════════════════════════════════════════════════════

def get_btc_full_picture():
    """Fetch comprehensive BTC data across all timeframes."""
    data = cfg.mcporter_call("market_get_asset_data", asset=ASSET,
                              candle_intervals=["5m", "15m", "1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None
    return data.get("data", data)


def get_btc_sm_direction():
    """Get smart money positioning specifically for BTC.
    Returns (direction, pct, trader_count, cc_15m)."""
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data:
        return None, 0, 0, 0

    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0, 0, 0

    asset_long_pct = 0
    asset_short_pct = 0
    asset_traders = 0
    btc_cc_15m = 0
    found = False

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if str(token).upper() != ASSET:
            continue
        found = True
        direction = m.get("direction", "").lower()
        pct = safe_float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders = int(m.get("trader_count", m.get("traderCount", 0)) or 0)
        cc_15m_val = safe_float(m.get("contribution_pct_change_15m", 0))
        if direction == "long":
            asset_long_pct = pct
            asset_traders += traders
            btc_cc_15m = cc_15m_val
        elif direction == "short":
            asset_short_pct = pct
            asset_traders += traders
            btc_cc_15m = cc_15m_val

    if not found:
        return None, 0, 0, 0

    total = asset_long_pct + asset_short_pct
    if total == 0:
        return "NEUTRAL", 50, asset_traders, btc_cc_15m
    long_ratio = (asset_long_pct / total) * 100
    if long_ratio > 58:
        return "LONG", long_ratio, asset_traders, btc_cc_15m
    elif long_ratio < 42:
        return "SHORT", 100 - long_ratio, asset_traders, btc_cc_15m
    return "NEUTRAL", 50, asset_traders, btc_cc_15m


# ═══════════════════════════════════════════════════════════════
# Thesis Builder (BTC)
# ═══════════════════════════════════════════════════════════════

def build_btc_thesis():
    """Build a conviction thesis for BTC from every signal source.
    Mirrors Kodiak's build_sol_thesis. BTC-specific thresholds."""

    btc_data = get_btc_full_picture()
    if not btc_data:
        return None

    candles_5m = btc_data.get("candles", {}).get("5m", [])
    candles_15m = btc_data.get("candles", {}).get("15m", [])
    candles_1h = btc_data.get("candles", {}).get("1h", [])
    candles_4h = btc_data.get("candles", {}).get("4h", [])
    asset_ctx = btc_data.get("asset_context", btc_data.get("assetContext", {}))
    funding = safe_float(asset_ctx.get("funding", btc_data.get("funding", 0)))
    oi = safe_float(asset_ctx.get("openInterest", btc_data.get("openInterest", 0)))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return None

    price = safe_float(candles_5m[-1].get("close", candles_5m[-1].get("c", 0)))

    # ── REQUIRED: 4h trend structure (the foundation) ────────
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None  # No conviction without macro structure

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ── REQUIRED: 1h trend agrees ─────────────────────────────
    trend_1h, trend_strength_1h = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    # ── REQUIRED: 15m momentum confirms direction ────────────
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)

    if direction == "LONG" and mom_15m < MIN_MOM_15M:
        return None
    if direction == "SHORT" and mom_15m > -MIN_MOM_15M:
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

    # 15m momentum strength (1 pt if strong)
    if abs(mom_15m) > MIN_MOM_15M * 2:
        score += 1
        reasons.append(f"15m_strong_{mom_15m:+.2f}%")
    else:
        reasons.append(f"15m_{mom_15m:+.2f}%")

    # 5m alignment (1 pt — all 4 timeframes agree)
    if (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0):
        score += 1
        reasons.append("4TF_aligned")

    # ── SM positioning (HARD BLOCK if opposed — BTC has strongest SM signal) ─
    sm_dir, sm_pct, sm_count, sm_cc_15m = get_btc_sm_direction()
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}traders")
        if sm_pct > 65:
            score += 1
            reasons.append("sm_strongly_tilted")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        # SM opposes — hard block. SM has the best read on BTC.
        return None

    # ── 15m velocity freshness ────────────────────────────────
    if sm_cc_15m <= 0:
        score -= 3
        reasons.append(f"15M_STALE_PENALTY ({sm_cc_15m:.2f})")
    elif sm_cc_15m > 0.5:
        score += 1
        reasons.append(f"15M_FRESH +{sm_cc_15m:.2f}")

    # ── Funding alignment ─────────────────────────────────────
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

    # ── Volume confirmation ───────────────────────────────────
    vol_1h = volume_ratio(candles_1h)
    if vol_1h >= MIN_VOL_RATIO:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.7:
        score -= 1
        reasons.append("vol_weak")

    vol_trend_1h = volume_trend(candles_1h)
    if vol_trend_1h > 15:
        score += 1
        reasons.append(f"vol_rising_{vol_trend_1h:+.0f}%")

    # ── OI growth proxy (legacy, kept as fallback) ────────────
    vol_recent = sum(safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:])
    vol_earlier = sum(safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1
        reasons.append(f"oi_growing_{oi_proxy:+.0f}%")

    # ── OI velocity (v5.1 — real OI data from new MCP tool) ──
    # market_get_asset_data now includes oi_velocity (5m/15m/1h/4h).
    # Accelerating OI in direction = real capital committing → conviction boost.
    # Null handling: 1h/4h can be null on fresh deploys, treat as insufficient.
    oi_vel = btc_data.get("oi_velocity", {}) if isinstance(btc_data.get("oi_velocity"), dict) else {}
    oi_vel_1h = oi_vel.get("1h", {}) if isinstance(oi_vel.get("1h"), dict) else {}
    oi_vel_change = oi_vel_1h.get("change_pct")
    if oi_vel_change is not None:
        try:
            oi_vel_change = float(oi_vel_change)
            if oi_vel_change > 5:
                score += 2
                reasons.append(f"OI_ACCELERATING_{oi_vel_change:+.1f}%")
            elif oi_vel_change > 2:
                score += 1
                reasons.append(f"OI_rising_{oi_vel_change:+.1f}%")
            elif oi_vel_change < -3:
                score -= 1
                reasons.append(f"OI_draining_{oi_vel_change:+.1f}%")
        except (TypeError, ValueError):
            pass

    # ── RSI filter (BTC rarely pushes >70 or <30) ─────────────
    closes_1h = [safe_float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > RSI_MAX_LONG:
        return None
    if direction == "SHORT" and rsi < RSI_MIN_SHORT:
        return None
    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # ── 4h momentum strength bonus ────────────────────────────
    if abs(mom_4h) > STRONG_4H_PCT:
        score += 1
        reasons.append(f"4h_strong_{mom_4h:+.1f}%")

    # ── Move-exhaustion penalty ───────────────────────────────
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
        "trend_1h": trend_1h,
        "momentum": {"5m": mom_5m, "15m": mom_15m, "1h": mom_1h, "4h": mom_4h},
        "sm_direction": sm_dir,
        "sm_pct": sm_pct,
        "funding": funding,
        "rsi": rsi,
        "vol_ratio": vol_1h,
    }


# ═══════════════════════════════════════════════════════════════
# Stalk / Reload Evaluation
# ═══════════════════════════════════════════════════════════════

def evaluate_reload(exit_state):
    """After DSL exit, check if conditions are met to reload same direction.
    Returns (should_reload, reasons). False with kill signals → reset to HUNTING."""

    direction = exit_state.get("exitDirection")
    exit_ts = exit_state.get("exitTimestamp", 0)
    exit_vol = exit_state.get("exitEntryVolRatio", 1.0)
    now = cfg.now_ts()
    hours_stalking = (now - exit_ts) / 3600

    MAX_STALK_HOURS = 6
    if hours_stalking > MAX_STALK_HOURS:
        return False, [f"stalk_timeout_{hours_stalking:.1f}h"]

    btc_data = get_btc_full_picture()
    if not btc_data:
        return False, ["data_unavailable"]

    candles_5m = btc_data.get("candles", {}).get("5m", [])
    candles_1h = btc_data.get("candles", {}).get("1h", [])
    candles_4h = btc_data.get("candles", {}).get("4h", [])
    asset_ctx = btc_data.get("asset_context", btc_data.get("assetContext", {}))
    funding = safe_float(asset_ctx.get("funding", btc_data.get("funding", 0)))

    kill_reasons = []
    reload_checks = []

    # KILL: 4h trend reversed
    trend_4h, _ = trend_structure(candles_4h)
    expected_trend = "BULLISH" if direction == "LONG" else "BEARISH"
    if trend_4h != expected_trend and trend_4h != "NEUTRAL":
        kill_reasons.append(f"4h_trend_reversed_{trend_4h}")

    # KILL: SM flipped against
    sm_dir, sm_pct, _, _ = get_btc_sm_direction()
    if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        kill_reasons.append(f"sm_flipped_{sm_dir}")

    # KILL: Funding spiked into extreme crowding
    funding_ann = abs(funding) * 8760
    MAX_FUNDING_ANN_PCT = 80
    if (direction == "LONG" and funding > 0 and funding_ann > MAX_FUNDING_ANN_PCT) or \
       (direction == "SHORT" and funding < 0 and funding_ann > MAX_FUNDING_ANN_PCT):
        kill_reasons.append(f"funding_extreme_{funding_ann:.0f}%ann")

    # KILL: OI collapsed
    if len(candles_1h) >= 6:
        recent_vols = [safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:]]
        earlier_vols = [safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-3]]
        avg_recent = sum(recent_vols) / len(recent_vols) if recent_vols else 0
        avg_earlier = sum(earlier_vols) / len(earlier_vols) if earlier_vols else 1
        if avg_earlier > 0:
            oi_change = ((avg_recent - avg_earlier) / avg_earlier) * 100
            if oi_change < -20:
                kill_reasons.append(f"oi_collapsed_{oi_change:+.0f}%")

    if kill_reasons:
        return False, kill_reasons

    # Must have been stalking for at least 30 min
    if hours_stalking < 0.5:
        reload_checks.append("waiting_for_setup")

    # RELOAD: fresh 5m momentum impulse
    if len(candles_5m) >= 3:
        mom_5m_1 = price_momentum(candles_5m, 1)
        mom_5m_2 = price_momentum(candles_5m[:-1], 1)
        if direction == "LONG":
            if mom_5m_1 > 0.10 and mom_5m_1 > mom_5m_2:
                reload_checks.append(f"fresh_5m_impulse_{mom_5m_1:+.2f}%")
            else:
                reload_checks.append("no_5m_impulse")
        else:
            if mom_5m_1 < -0.10 and mom_5m_1 < mom_5m_2:
                reload_checks.append(f"fresh_5m_impulse_{mom_5m_1:+.2f}%")
            else:
                reload_checks.append("no_5m_impulse")

    # RELOAD: Volume at least 50% of exit
    vol = volume_ratio(candles_5m)
    if vol >= exit_vol * 0.5:
        reload_checks.append(f"vol_sufficient_{vol:.1f}x")
    else:
        reload_checks.append(f"vol_weak_{vol:.1f}x")

    # RELOAD: Funding not crowded
    if (direction == "LONG" and (funding <= 0 or funding_ann < 50)) or \
       (direction == "SHORT" and (funding >= 0 or funding_ann < 50)):
        reload_checks.append("funding_ok")
    else:
        reload_checks.append(f"funding_crowded_{funding_ann:.0f}%ann")

    # RELOAD: SM still aligned
    if sm_dir == direction:
        reload_checks.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir == "NEUTRAL":
        reload_checks.append("sm_neutral_ok")
    else:
        reload_checks.append(f"sm_not_aligned_{sm_dir}")

    # RELOAD: 4h trend intact
    if trend_4h == expected_trend:
        reload_checks.append("4h_intact")
    else:
        reload_checks.append(f"4h_{trend_4h}")

    fails = [r for r in reload_checks if any(bad in r for bad in
              ["no_5m", "vol_weak", "funding_crowded",
               "sm_not_aligned", "waiting_for"])]

    if not fails:
        return True, reload_checks
    else:
        return False, reload_checks


# ═══════════════════════════════════════════════════════════════
# Trading Infrastructure
# ═══════════════════════════════════════════════════════════════

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap — hot hand gets more, losers preserve capital."""
    if starting_budget <= 0:
        return 4
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12   # Hot hand
    elif pnl_pct >= 0:     return 8
    elif pnl_pct >= -5:    return 5
    elif pnl_pct >= -15:   return 3
    elif pnl_pct >= -25:   return 1
    else:                  return 0    # HARD STOP circuit breaker


def get_leverage_for_score(score):
    """Conviction-scaled leverage. Kodiak empirical 10x cap.
    BTC can safely push to 10x at apex — less wick risk than alts."""
    if score >= 10:
        return 10
    return 7


def get_margin_pct(score, base=0.30):
    """Conviction-scaled margin. Apex setups get bigger allocation."""
    if score >= 14:
        return base * 1.5   # 45%
    elif score >= 12:
        return base * 1.25  # 37.5%
    else:
        return base         # 30%


def get_safe_leverage(wallet, coin, desired):
    """Clamp leverage to per-asset HL max via strategy_get_asset_trading_limits."""
    try:
        r = cfg.mcporter_call("strategy_get_asset_trading_limits",
                              strategy_wallet=wallet, coin=coin)
        if r:
            d = r.get("data", r)
            max_lev = int(d.get("maxLeverage", d.get("max_leverage", MAX_LEVERAGE)))
            return min(desired, max_lev, MAX_LEVERAGE)
    except Exception:
        pass
    return min(desired, MAX_LEVERAGE)


def has_resting_orders(wallet):
    """Non-reduceOnly orders block entry. Auto-cancel stale (>10min) maker orders."""
    STALE_ORDER_MAX_AGE_SEC = 600
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = time.time() * 1000
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
                    cfg.mcporter_call("cancel_order",
                                      strategyWalletAddress=wallet,
                                      orderId=int(oid))
                except Exception:
                    pass
            continue
        has_fresh = True
    return has_fresh


def execute_entry(wallet, direction, margin, leverage):
    """Place BTC entry via canonical MCP schema with inner-order validation."""
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
                "ensureExecutionAsTaker": False,
                "executionTimeoutSeconds": 30,
            },
        }],
    )
    if not result:
        return False, {"error": "mcporter_call returned None"}

    # Outer envelope
    if not result.get("success"):
        return False, {"error": result.get("error", "outer_envelope_failed"), "raw": result}

    # Inner-order validation — guard against phantom success
    data = result.get("data", result)
    orders = data.get("orders", []) if isinstance(data, dict) else []
    if orders and isinstance(orders, list):
        first = orders[0] if isinstance(orders[0], dict) else {}
        if first and not first.get("success", True):
            return False, {"error": first.get("error", "inner_order_failed"),
                           "raw": result}

    return True, result


# ═══════════════════════════════════════════════════════════════
# Main — 3-Mode State Machine
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") == "HARD_STOP":
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"gate=HARD_STOP: {tc.get('gateReason', 'circuit breaker')}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    state = cfg.load_state("grizzly-state.json")
    current_mode = state.get("currentMode", "HUNTING")
    btc_position = next((p for p in positions if p["coin"].upper() == ASSET), None)

    # ── MODE 2: RIDING ────────────────────────────────────────
    if btc_position and current_mode in ("RIDING", "HUNTING"):
        if current_mode != "RIDING":
            state["currentMode"] = "RIDING"
            state["lastDirection"] = btc_position["direction"]
            cfg.save_state(state, "grizzly-state.json")
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"RIDING: BTC {btc_position['direction']} — DSL manages exit. Scanner does NOT re-evaluate.",
                    "_v2_no_thesis_exit": True})
        return

    # ── DSL exit detected: was RIDING, now no position ────────
    if not btc_position and current_mode == "RIDING":
        btc_data = get_btc_full_picture()
        exit_vol = 1.0
        if btc_data:
            candles_5m = btc_data.get("candles", {}).get("5m", [])
            exit_vol = volume_ratio(candles_5m) if candles_5m else 1.0

        state["currentMode"] = "STALKING"
        state["exitState"] = {
            "exitDirection": state.get("lastDirection", "LONG"),
            "exitTimestamp": cfg.now_ts(),
            "exitEntryVolRatio": exit_vol,
        }
        cfg.save_state(state, "grizzly-state.json")
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": "DSL closed position — transitioning to STALKING mode"})
        return

    # ── MODE 3: STALKING ──────────────────────────────────────
    if current_mode == "STALKING":
        exit_state = state.get("exitState", {})
        if not exit_state:
            state["currentMode"] = "HUNTING"
            cfg.save_state(state, "grizzly-state.json")
        else:
            should_reload, reasons = evaluate_reload(exit_state)

            if should_reload:
                direction = exit_state["exitDirection"]
                leverage = get_safe_leverage(wallet, ASSET, 10)
                margin = round(account_value * 0.30, 2)  # Reload uses base margin

                state["currentMode"] = "RIDING"
                state["lastDirection"] = direction
                state.pop("exitState", None)
                cfg.save_state(state, "grizzly-state.json")

                success, result = execute_entry(wallet, direction, margin, leverage)
                if success:
                    tc["entries"] = tc.get("entries", 0) + 1
                    tc["last_entry_ts"] = time.time()
                    cfg.save_trade_counter(tc)
                    cfg.output({
                        "success": True,
                        "action": "RELOAD",
                        "signal": {"asset": ASSET, "direction": direction,
                                   "mode": "STALKING_RELOAD", "reasons": reasons},
                        "execution": {"asset": ASSET, "direction": direction,
                                      "leverage": leverage, "margin": margin,
                                      "orderType": "FEE_OPTIMIZED_LIMIT"},
                        "result": result,
                        "_grizzly_version": "5.0",
                    })
                else:
                    # Reload failed — back to HUNTING
                    state["currentMode"] = "HUNTING"
                    cfg.save_state(state, "grizzly-state.json")
                    cfg.output({
                        "success": True, "action": "RELOAD_FAILED",
                        "signal": {"asset": ASSET, "direction": direction,
                                   "reasons": reasons},
                        "error": result, "_grizzly_version": "5.0",
                    })
                return

            # Kill conditions → reset to HUNTING
            kill_signals = [r for r in reasons if any(k in r for k in
                           ["stalk_timeout", "4h_trend_reversed", "sm_flipped",
                            "funding_extreme", "oi_collapsed"])]

            if kill_signals:
                state["currentMode"] = "HUNTING"
                state.pop("exitState", None)
                cfg.save_state(state, "grizzly-state.json")
                cfg.output({"success": True, "heartbeat": "NO_REPLY",
                            "note": f"STALKING → RESET: {kill_signals[0]}"})
                return

            # Still stalking
            hours = (cfg.now_ts() - exit_state.get("exitTimestamp", cfg.now_ts())) / 3600
            cfg.output({"success": True, "heartbeat": "NO_REPLY",
                        "note": f"STALKING {hours:.1f}h — waiting for reload conditions"})
            return

    # ── MODE 1: HUNTING ───────────────────────────────────────
    # Dynamic daily cap
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    if cfg.is_asset_cooled_down(ASSET, ASSET_COOLDOWN_MINUTES):
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "BTC on cooldown"})
        return

    if has_resting_orders(wallet):
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": "RESTING ORDER: BTC limit order pending."})
        return

    # Build thesis
    thesis = build_btc_thesis()
    if not thesis:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "HUNTING: no BTC thesis"})
        return

    if thesis["score"] < MIN_SCORE:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"HUNTING: BTC {thesis['direction']} score {thesis['score']}<{MIN_SCORE}. {', '.join(thesis['reasons'][:3])}"})
        return

    # Same-direction cooldown after a win
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)
    if last_win_dir and last_win_ts and thesis["direction"] == last_win_dir:
        if (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
            remaining = int((SAME_DIR_COOLDOWN_MINUTES * 60 - (time.time() - last_win_ts)) / 60)
            cfg.output({"success": True, "heartbeat": "NO_REPLY",
                        "note": f"SAME_DIR_COOLDOWN: won {last_win_dir} {remaining}min ago"})
            return

    # Conviction-scaled sizing
    leverage = get_safe_leverage(wallet, ASSET, get_leverage_for_score(thesis["score"]))
    margin_pct = get_margin_pct(thesis["score"])
    margin = round(account_value * margin_pct, 2)

    # Execute
    success, result = execute_entry(wallet, thesis["direction"], margin, leverage)
    if success:
        state["currentMode"] = "RIDING"
        state["lastDirection"] = thesis["direction"]
        cfg.save_state(state, "grizzly-state.json")

        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        cfg.save_trade_counter(tc)

        cfg.output({
            "success": True,
            "action": "ENTRY",
            "signal": thesis,
            "execution": {
                "asset": ASSET, "direction": thesis["direction"],
                "leverage": leverage, "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "ensureExecutionAsTaker": False,
            },
            "constraints": {"minLeverage": MIN_LEVERAGE, "maxLeverage": MAX_LEVERAGE},
            "result": result,
            "_grizzly_version": "5.0",
        })
    else:
        cfg.output({
            "success": True,
            "action": "ENTRY_FAILED",
            "signal": {"asset": ASSET, "direction": thesis["direction"],
                       "score": thesis["score"], "reasons": thesis["reasons"]},
            "error": result,
            "_grizzly_version": "5.0",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"success": False, "error": str(e)})
