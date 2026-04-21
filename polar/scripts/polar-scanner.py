#!/usr/bin/env python3
# Senpi POLAR Scanner v3.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""POLAR v3.0 — ETH Alpha Hunter (hyperfeed primary + structural veto).

Complete architectural rewrite. v2.4 used leaderboard_get_markets as primary
signal with no structural candle confirmation. In choppy ETH regimes this
produced 32% gross win rate — worse than coin flip. 81 lifetime positions,
-$364 realized, fee drag dominated.

v3.0 keeps hyperfeed (Senpi's differentiated SM data) as the direction
oracle but adds structural-veto gates from raw candle data. The scanner
only fires when BOTH hyperfeed AND price structure agree on direction.

## Design

Data flow:
  1. PRIMARY direction from leaderboard_get_markets ETH row (SM positioning)
  2. STRUCTURAL validation from market_get_asset_data(asset="ETH") candles
  3. CONFLUENCE scoring from funding, OI velocity, BTC correlation, RSI, etc.

Hard gates (all must pass, any failure → NO_REPLY):

  Hyperfeed gates:
    1. SM direction ∈ {LONG, SHORT}
    2. pct_of_top_traders_gain ≥ 5.0
    3. trader_count ≥ 30
    4. contribution_pct_change_15m ≥ 0.3 (SM actively building)

  Structural veto gates (price must second the motion):
    5. trend_4h ∈ {BULLISH, BEARISH} (not NEUTRAL)
    6. trend_strength_4h ≥ 0.75 (Kodiak v5.1 pattern — 4 of 5 candles)
    7. structural_direction == SM_direction
       ← THE KEY NEW GATE. Rejects signals where hyperfeed says LONG but 4h
         shows BEARISH structure, or vice versa. This is what prevents the
         v2.4 chop failure mode where hyperfeed oscillated with noise.
    8. trend_1h matches trend_4h
    9. mom_15m direction + magnitude ≥ 0.1%
    10. RSI filter: no LONG > 74, no SHORT < 26
    11. Base-tech floor: strong_15m OR aligned_5m

Scoring after gates (max ~20):
  Base tech: 4h(+3), 1h_confirms(+2), 15m_strong(+1), 5m_aligned(+1)
  SM concentration: 5%/10%/15% tiers (+1/+2/+3)
  SM velocity: 15m (+1-2), 1h accel (+1)
  Trader depth ≥ 100 (+1)
  Funding aligned (+2) or crowded against (-1)
  OI velocity: FLAT path oi_change_pct_1h (fixes v2.4 silent-None bug)
  BTC correlation (+1)
  RSI room (+1)
  4h momentum bonus (+1)
  Move-exhaustion penalty (-1 to -2)

MIN_SCORE = 14. Higher than Kodiak's 10 because more scoring dimensions;
higher threshold forces apex-quality setups only. Expected entry rate:
2-4/week (down from ~2/day in v2.4). Reduces fee drag proportionally.

Leverage: conviction-scaled tiers. Apex (10x) only at score ≥ 17.

## What v3.0 does NOT change

- Runtime DSL tiers (8/25, 15/50, 25/65, 35/80, 50/85) — same as Kodiak
- Phase 1: max_loss 25%, retrace 8, 3 breaches
- weak_peak_cut 60m, dead_weight_cut 30m, hard_timeout 480m
- MAX_POSITIONS=1, MARGIN_PCT=0.50, COOLDOWN_MINUTES=240
- SAME_DIR_COOLDOWN_MINUTES=120
- P&L-aware dynamic daily cap

Uses: leaderboard_get_markets + market_get_asset_data + strategy_get_open_orders
Runs every 3 minutes.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polar_config as cfg


VERSION = "3.0.1"
ASSET = "ETH"
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 4


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

def _resolve_starting_budget():
    """Read startingBudget from config.json; default 1000 for fresh deploys.

    Wallet-specific values (e.g. post-drawdown rebases, post-topup values)
    belong in the user's LOCAL config, not in the public repo. Scanner reads
    config on each cron invocation (fresh Python subprocess), so config
    edits take effect on the next scan without any code change.
    """
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
    """P&L-aware daily entry cap. Winners get more trades, losers fewer.
    Catastrophic drawdown triggers HARD STOP."""
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
    else:                  return 0  # HARD STOP


# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

COOLDOWN_MINUTES = 240
SAME_DIR_COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.50
MIN_SCORE = 14
MIN_SM_ACCEL_PCT = 0.3
MIN_SM_CONCENTRATION = 5.0
MIN_SM_TRADER_COUNT = 30
MIN_TREND_STRENGTH_4H = 0.75
MIN_MOM_15M_PCT = 0.1

# Conviction-scaled leverage tiers. Higher score = higher leverage.
LEVERAGE_TIERS = [
    {"min_score": 17, "leverage": 10, "label": "apex"},
    {"min_score": 15, "leverage": 7,  "label": "conviction"},
    {"min_score": 14, "leverage": 5,  "label": "standard"},
]
DEFAULT_LEVERAGE = 5
MAX_LEVERAGE = 10


# ═══════════════════════════════════════════════════════════════
# TECHNICAL HELPERS (ported from Kodiak v5.1 — battle-tested)
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def candle_close(c):
    return safe_float(c.get("close", c.get("c", 0)))


def candle_high(c):
    return safe_float(c.get("high", c.get("h", 0)))


def candle_low(c):
    return safe_float(c.get("low", c.get("l", 0)))


def candle_volume(c):
    return safe_float(c.get("volume", c.get("v", c.get("vlm", 0))))


def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = candle_close(candles[-(n_bars + 1)])
    new = candle_close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Return (direction, strength) from higher-lows / lower-highs pattern.
    strength is fraction of candles supporting the pattern (0.0-1.0)."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [candle_low(c) for c in candles[-lookback:]]
    highs = [candle_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g = sum(g) / period
    avg_l = sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def volume_ratio(candles, lookback=10):
    if len(candles) < lookback + 1:
        return 1.0
    vols = [candle_volume(c) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = candle_volume(candles[-1])
    return latest / avg if avg > 0 else 1.0


def get_leverage_tier(score):
    """Return (leverage, label) for a score."""
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"], tier["label"]
    return DEFAULT_LEVERAGE, "default"


# ═══════════════════════════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════════════════════════

def get_eth_asset_data():
    """Pull ETH candles + asset_context + OI velocity from market_get_asset_data."""
    raw = cfg.mcporter_call(
        "market_get_asset_data", asset=ASSET,
        candle_intervals=["5m", "15m", "1h", "4h"],
        include_funding=True, include_order_book=False,
    )
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return None
    return data


def get_eth_sm_signal():
    """Get ETH smart money positioning from leaderboard_get_markets.

    Returns dict with direction, pct, traders, cc_15m, cc_1h, cc_4h, or None.
    Aggregates across long/short rows if present (matches Kodiak pattern).
    """
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return None

    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None

    long_pct = 0.0
    short_pct = 0.0
    traders_sum = 0
    cc_15m = 0.0
    cc_1h = 0.0
    cc_4h = 0.0
    found = False

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", ""))).upper()
        if token != ASSET:
            continue
        found = True
        direction = str(m.get("direction", "")).upper()
        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0))
        cc_15m_val = safe_float(m.get("contribution_pct_change_15m", 0))
        cc_1h_val = safe_float(m.get("contribution_pct_change_1h", 0))
        cc_4h_val = safe_float(m.get("contribution_pct_change_4h", 0))

        if direction == "LONG":
            long_pct = pct
            traders_sum += traders
            cc_15m = cc_15m_val
            cc_1h = cc_1h_val
            cc_4h = cc_4h_val
        elif direction == "SHORT":
            short_pct = pct
            traders_sum += traders
            # For single-direction rows, these overwrite. Acceptable — we only
            # score on the aggregate anyway.
            cc_15m = cc_15m_val
            cc_1h = cc_1h_val
            cc_4h = cc_4h_val

    if not found:
        return None

    total = long_pct + short_pct
    if total == 0:
        return {"direction": "NEUTRAL", "pct": 0, "traders": traders_sum,
                "cc_15m": cc_15m, "cc_1h": cc_1h, "cc_4h": cc_4h}

    long_ratio = (long_pct / total) * 100
    if long_ratio > 58:
        direction = "LONG"
        pct_winner = long_pct
    elif long_ratio < 42:
        direction = "SHORT"
        pct_winner = short_pct
    else:
        return {"direction": "NEUTRAL", "pct": max(long_pct, short_pct),
                "traders": traders_sum, "cc_15m": cc_15m, "cc_1h": cc_1h, "cc_4h": cc_4h}

    return {
        "direction": direction,
        "pct": pct_winner,
        "traders": traders_sum,
        "cc_15m": cc_15m,
        "cc_1h": cc_1h,
        "cc_4h": cc_4h,
    }


def get_btc_correlation():
    """Pull BTC 15m/1h momentum for correlation scoring."""
    raw = cfg.mcporter_call(
        "market_get_asset_data", asset="BTC",
        candle_intervals=["15m", "1h"],
        include_funding=False, include_order_book=False,
    )
    if not raw:
        return None, None
    data = raw.get("data", raw) if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return None, None
    candles_15m = data.get("candles", {}).get("15m", [])
    candles_1h = data.get("candles", {}).get("1h", [])
    mom_15m = price_momentum(candles_15m, 1) if len(candles_15m) >= 2 else None
    mom_1h = price_momentum(candles_1h, 1) if len(candles_1h) >= 2 else None
    return mom_15m, mom_1h


# ═══════════════════════════════════════════════════════════════
# THESIS BUILDER — v3.0 hybrid hyperfeed + structural veto
# ═══════════════════════════════════════════════════════════════

def build_eth_thesis():
    """Build an ETH entry thesis gated by hyperfeed SM + candle structure.

    Returns a thesis dict on success, or {"blocked": True, "reason": "..."}
    on any gate failure. Never returns None — always returns a diagnostic
    so the heartbeat can log what blocked the signal.
    """
    # ─── HYPERFEED GATES ────────────────────────────────────────
    sm = get_eth_sm_signal()
    if not sm:
        return {"blocked": True, "reason": "no_sm_data"}
    if sm["direction"] not in ("LONG", "SHORT"):
        return {"blocked": True, "reason": f"sm_neutral_{sm['direction']}"}
    if sm["pct"] < MIN_SM_CONCENTRATION:
        return {"blocked": True, "reason": f"sm_weak_{sm['pct']:.1f}%"}
    if sm["traders"] < MIN_SM_TRADER_COUNT:
        return {"blocked": True, "reason": f"sm_shallow_{sm['traders']}t"}
    if sm["cc_15m"] < MIN_SM_ACCEL_PCT:
        return {"blocked": True, "reason": f"sm_stale_{sm['cc_15m']:+.2f}"}

    direction = sm["direction"]

    # ─── STRUCTURAL DATA ────────────────────────────────────────
    asset_data = get_eth_asset_data()
    if not asset_data:
        return {"blocked": True, "reason": "no_asset_data"}

    candles_5m = asset_data.get("candles", {}).get("5m", [])
    candles_15m = asset_data.get("candles", {}).get("15m", [])
    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])
    asset_ctx = asset_data.get("asset_context", {}) or {}
    funding = safe_float(asset_ctx.get("funding", 0))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return {"blocked": True, "reason": "insufficient_candles"}

    price = candle_close(candles_5m[-1])

    # ─── STRUCTURAL GATES ───────────────────────────────────────
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return {"blocked": True, "reason": f"4h_NEUTRAL_(sm_said_{direction})"}
    if trend_strength_4h < MIN_TREND_STRENGTH_4H:
        return {"blocked": True, "reason": f"4h_weak_{trend_strength_4h:.0%}"}

    # THE KEY GATE — structural direction must match SM direction
    structural_dir = "LONG" if trend_4h == "BULLISH" else "SHORT"
    if structural_dir != direction:
        return {"blocked": True,
                "reason": f"direction_conflict_sm_{direction}_vs_4h_{structural_dir}"}

    trend_1h, trend_strength_1h = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return {"blocked": True, "reason": f"1h_{trend_1h}_vs_4h_{trend_4h}"}

    # ─── MOMENTUM GATES ─────────────────────────────────────────
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)

    if direction == "LONG" and mom_15m < MIN_MOM_15M_PCT:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}
    if direction == "SHORT" and mom_15m > -MIN_MOM_15M_PCT:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}

    # ─── RSI GATE ───────────────────────────────────────────────
    closes_1h = [candle_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > 74:
        return {"blocked": True, "reason": f"rsi_overbought_{rsi:.0f}"}
    if direction == "SHORT" and rsi < 26:
        return {"blocked": True, "reason": f"rsi_oversold_{rsi:.0f}"}

    # ─── BASE-TECH FLOOR (Kodiak v5.1 pattern) ──────────────────
    strong_15m = abs(mom_15m) > MIN_MOM_15M_PCT * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return {"blocked": True,
                "reason": f"base_tech_weak_15m({mom_15m:+.2f})_5m({mom_5m:+.2f})"}

    # ─── ALL GATES PASSED — SCORE THE SIGNAL ────────────────────
    score = 0
    reasons = []

    # Base tech
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{trend_strength_4h:.0%}")
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")
    if strong_15m:
        score += 1
        reasons.append(f"15m_strong_{mom_15m:+.2f}%")
    if aligned_5m:
        score += 1
        reasons.append("4TF_aligned")

    # SM concentration
    if sm["pct"] >= 15:
        score += 3
        reasons.append(f"SM_DOMINANT_{sm['pct']:.1f}%_{sm['traders']}t")
    elif sm["pct"] >= 10:
        score += 2
        reasons.append(f"SM_STRONG_{sm['pct']:.1f}%_{sm['traders']}t")
    elif sm["pct"] >= 5:
        score += 1
        reasons.append(f"SM_aligned_{sm['pct']:.1f}%_{sm['traders']}t")

    # SM 15m velocity
    if sm["cc_15m"] > 2.0:
        score += 2
        reasons.append(f"SM_15m_strong_+{sm['cc_15m']:.2f}")
    elif sm["cc_15m"] > 0.5:
        score += 1
        reasons.append(f"SM_15m_+{sm['cc_15m']:.2f}")

    # SM 1h accel pattern (15m > 1h > 0 = SM piling in RIGHT NOW)
    if sm["cc_15m"] > 0 and sm["cc_1h"] > 0 and sm["cc_15m"] > sm["cc_1h"]:
        score += 1
        reasons.append(f"SM_ACCELERATING_15m({sm['cc_15m']:.2f})>1h({sm['cc_1h']:.2f})")

    # Trader depth
    if sm["traders"] >= 100:
        score += 1
        reasons.append(f"SM_DEEP_{sm['traders']}t")

    # Funding alignment
    if direction == "LONG" and funding < 0:
        score += 2
        reasons.append(f"funding_pays_longs_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0:
        score += 2
        reasons.append(f"funding_pays_shorts_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.005) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append(f"funding_crowded_{funding:+.4f}")

    # OI velocity — FLAT path (fixes v2.4 silent-None bug)
    # MCP returns oi_velocity = { oi_change_pct_5m, oi_change_pct_15m,
    # oi_change_pct_1h, oi_change_pct_4h, ... } at flat top level.
    oi_vel = asset_data.get("oi_velocity")
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
    btc_mom_15m, btc_mom_1h = get_btc_correlation()
    if btc_mom_15m is not None and btc_mom_1h is not None:
        btc_agrees = (direction == "LONG" and btc_mom_15m > 0 and btc_mom_1h > 0) or \
                     (direction == "SHORT" and btc_mom_15m < 0 and btc_mom_1h < 0)
        if btc_agrees:
            score += 1
            reasons.append(f"btc_confirms_{btc_mom_1h:+.2f}%")

    # RSI room
    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4h momentum bonus
    if abs(mom_4h) > 1.0:
        score += 1
        reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

    # Move-exhaustion penalty
    if abs(mom_4h) >= 4.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif abs(mom_4h) >= 2.5:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1
            reasons.append(f"MOVE_TIRING_{mom_4h:+.1f}%")

    return {
        "blocked": False,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "sm": sm,
        "funding": funding,
        "mom": {"5m": mom_5m, "15m": mom_15m, "1h": mom_1h, "4h": mom_4h},
        "trend_4h": trend_4h,
        "trend_strength_4h": trend_strength_4h,
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(wallet, direction, margin, leverage):
    """Fire create_position via mcporter. Uses taker fallback for fast ETH moves."""
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
        # Fleet-wide inner-order validation
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


# ═══════════════════════════════════════════════════════════════
# TRADE COUNTER
# ═══════════════════════════════════════════════════════════════

def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_tc():
    """Load trade counter. Timestamps persist across midnight; only entries counter resets."""
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    default = {"date": _today_str(), "entries": 0,
               "last_entry_ts": 0, "last_win_direction": None, "last_win_ts": 0}
    if os.path.exists(p):
        try:
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") != _today_str():
                tc["date"] = _today_str()
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_tc(tc):
    tc["date"] = _today_str()
    cfg.atomic_write(os.path.join(str(cfg.STATE_DIR), "trade-counter.json"), tc)


def has_resting_orders(wallet):
    """Auto-cancel stale FEE_OPTIMIZED_LIMIT orders > 600s old."""
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
# MAIN
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

    # RIDING: position open → scanner does not exit (DSL owns exits)
    if len(positions) >= MAX_POSITIONS:
        p = positions[0]
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": (f"RIDING: {p['coin']} {p['direction']} "
                     f"upnl=${p['upnl']:.2f} margin=${p['margin']:.2f}"),
            "version": VERSION,
        })
        return

    # Resting order guard
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "RESTING_ORDER_pending", "version": VERSION})
        return

    # Trade counter + dynamic cap
    tc = load_tc()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": (f"DAILY_CAP_REACHED {tc.get('entries', 0)}/{dynamic_cap} "
                             f"pnl={pnl_pct:+.1f}%"),
                    "version": VERSION})
        return

    # Asset cooldown (global ETH cooldown)
    if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"COOLDOWN_{ASSET}_active_{COOLDOWN_MINUTES}min",
                    "version": VERSION})
        return

    # Same-direction cooldown (v2.3 pattern — prevent re-entering same side after win)
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)
    if last_win_dir and (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
        # Block entry in the SAME direction as last win, for the cooldown window
        # (checked against thesis below)
        same_dir_block_until = last_win_ts + SAME_DIR_COOLDOWN_MINUTES * 60
    else:
        same_dir_block_until = 0

    # Evaluate thesis
    thesis = build_eth_thesis()
    if thesis.get("blocked"):
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HUNTING_blocked:{thesis['reason']}",
            "version": VERSION,
        })
        return

    direction = thesis["direction"]
    score = thesis["score"]

    # Same-direction cooldown check
    if same_dir_block_until > 0 and direction == last_win_dir:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": (f"SAME_DIR_COOLDOWN {direction} until "
                     f"{int((same_dir_block_until - time.time())/60)}min"),
            "thesis_score": score,
            "version": VERSION,
        })
        return

    # Score check
    if score < MIN_SCORE:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HUNTING_score_low {score}/{MIN_SCORE}",
            "direction": direction,
            "reasons": thesis["reasons"],
            "version": VERSION,
        })
        return

    # Sizing
    leverage, tier_label = get_leverage_tier(score)
    margin = round(account_value * MARGIN_PCT, 2)
    if margin < 10 or leverage <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"SIZING_TOO_SMALL lev={leverage} margin={margin}",
                    "version": VERSION})
        return

    # Execute
    ok, result = execute_entry(wallet, direction, margin, leverage)
    if not ok:
        cfg.output({
            "status": "error",
            "action": "ENTRY_FAILED",
            "direction": direction,
            "score": score,
            "reasons": thesis["reasons"],
            "error": result,
            "version": VERSION,
        })
        return

    # Success — increment counter + set cooldown
    tc["entries"] = tc.get("entries", 0) + 1
    tc["last_entry_ts"] = time.time()
    save_tc(tc)
    cfg.set_asset_cooldown(ASSET, reason="entry")

    cfg.output({
        "status": "ok",
        "action": "ENTRY",
        "direction": direction,
        "coin": ASSET,
        "score": score,
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
        },
        "thesis": {
            "rsi": thesis["rsi"],
            "sm": thesis["sm"],
            "funding": thesis["funding"],
            "trend_4h": thesis["trend_4h"],
            "trend_strength_4h": thesis["trend_strength_4h"],
            "mom": thesis["mom"],
        },
        "account": {
            "value": account_value,
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
