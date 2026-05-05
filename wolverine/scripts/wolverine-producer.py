#!/usr/bin/env python3
# Senpi WOLVERINE Producer v4.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""WOLVERINE v4.0.0 Producer — HYPE alpha signal emitter for v2 runtime.

v3.x was a full-agency scanner (load_tc / save_tc / has_resting_orders
/ Python-side cooldowns / drawdown gate). 292 audit entries on M193170
since 2026-04-15, mostly schema-validation failures (silent-None bug
pattern). v4.0 flips to producer + v2 runtime (Polar v4.0 / Vulture
v3.0 template).

ARCHITECTURE CHANGE:
  - Producer (wolverine-producer.py) emits signals via
    `openclaw senpi external-scanner ingest`. NO execution code.
  - Runtime LLM gate is pass-through — producer has applied every
    filter; LLM only catches malformed signals.
  - risk.guard_rails ENFORCES daily caps, drawdown halt, consecutive-
    loss halt, per-asset cooldown. No Python state to drift / crash.
  - DSL uses FEE_OPTIMIZED_LIMIT on entries AND exits.
  - Trade chain DB emits per-trade telemetry — chain-DB visibility
    on Wolverine for the first time.

WHAT'S PRESERVED FROM v3.0.3/v3.0.4:
  - HYPE single-asset thesis
  - Six-gate entry validation:
    * GATE 1: 4h trend != NEUTRAL
    * GATE 2: 4h structural strength ≥ 0.75
    * GATE 3: 1h matches 4h direction
    * GATE 4: 15m momentum aligned ≥ MIN_MOM_15M (0.15)
    * GATE 5: base-tech floor (strong_15m OR aligned_5m)
    * GATE 6 (v3.0.3): 4h MAGNITUDE >= 1.5% — rejects dead-flat chop
      that was killing all 6 Week 5 trades on -0.26% 24h HYPE
  - SM HARD BLOCK if direction opposes
  - RSI hard gates (74 LONG / 26 SHORT)
  - Multi-factor scoring (~17 max points)
  - MIN_SCORE = 9 (config-overridable)
  - Conviction-tiered leverage: 5x apex (score ≥11), 3x standard (≥9)
  - DSL preset preserved exactly: time-cuts disabled (v3.0.1/2/4 fixes),
    Phase 1 max_loss 20% / retrace 8 / 3 breaches, Phase 2 tiers
    10/15, 20/35, 35/55, 55/70, 80/85

FLEET PATCHES:
  - FP-001 quiet hours (00-04 UTC unless apex score 11+)
  - FP-002 hard rule in SKILL.md (user-conversation Claude sessions
    are read-only — only producer cron + DSL engine are write paths)

Environment / config resolution:
  Strategy wallet read from config/wolverine-config.json (canonical).
  WOLVERINE_WALLET_ADDRESS env var supported as optional override.
"""

import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wolverine_config as cfg


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD
# ═══════════════════════════════════════════════════════════════

_LOCK_DIR = Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "wolverine-strategy" / "state"
_LOCK_DIR.mkdir(parents=True, exist_ok=True)
_LOCK_PATH = _LOCK_DIR / "producer.lock"


def acquire_lock():
    try:
        f = open(_LOCK_PATH, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(f"{os.getpid()} {int(time.time())}\n")
        f.flush()
        return f
    except (IOError, OSError, BlockingIOError):
        return None


def release_lock(lock_file):
    if lock_file is None:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_file.close()
    except Exception:
        pass


VERSION = "4.2.0"
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "wolverine_signals")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")


def _resolve_wallet():
    """Resolve strategy wallet — config.json is the canonical source.
    Env var WOLVERINE_WALLET_ADDRESS is supported as an optional override."""
    env_val = (os.environ.get("WOLVERINE_WALLET_ADDRESS") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved from v3.0.3 (HYPE-tuned)
# ═══════════════════════════════════════════════════════════════

ASSET = "HYPE"
MIN_SCORE_DEFAULT = 9             # config-overridable via "minScore"

# Hard gates
MIN_MOM_15M = 0.15
MIN_4H_STRUCTURE = 0.65           # v4.2: 0.75 → 0.65. v3.0.3's 0.75
                                  # required 4 of 5 4h candles aligned —
                                  # missed multi-day grinds where 1-2
                                  # pullback candles are normal. 0.65 =
                                  # 3 of 5 candles, captures clean
                                  # trends with normal pullback structure.
MIN_4H_MAGNITUDE_PCT = 1.0        # v4.2: 1.5 → 1.0. Combined with
                                  # trailing-window calculation below,
                                  # 1.0% over 4 hours captures HYPE's
                                  # typical multi-day grind cadence
                                  # (0.8-1.2% per 4h on cumulative move).
RSI_MAX_LONG = 72
RSI_MIN_SHORT = 28
FUNDING_CROWDED = 0.005

# Move-exhaustion
STRONG_4H_PCT = 2.5
MOVE_EXHAUSTION_PCT = 3.5
MOVE_TIRING_PCT = 2.0

# Conviction-scaled leverage tiers (preserved from v3.x)
LEVERAGE_TIERS = [
    {"min_score": 11, "leverage": 5, "label": "apex"},
    {"min_score": 9,  "leverage": 3, "label": "standard"},
]
DEFAULT_LEVERAGE = 3
MAX_LEVERAGE = 5


# ═══════════════════════════════════════════════════════════════
# HELPERS
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


def volume_trend(candles, lookback=6):
    """Simple trend over last N candles — % change avg(latest 3) vs avg(prior 3)."""
    if len(candles) < lookback:
        return 0
    recent = [candle_volume(c) for c in candles[-3:]]
    prior = [candle_volume(c) for c in candles[-6:-3]]
    avg_r = sum(recent) / max(1, len(recent))
    avg_p = sum(prior) / max(1, len(prior))
    if avg_p == 0:
        return 0
    return ((avg_r - avg_p) / avg_p) * 100


def get_leverage_tier(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"], tier["label"]
    return DEFAULT_LEVERAGE, "default"


# ═══════════════════════════════════════════════════════════════
# v4.0 fleet patches
# ═══════════════════════════════════════════════════════════════

def get_min_score():
    try:
        return int(cfg.load_config().get("minScore", MIN_SCORE_DEFAULT))
    except (TypeError, ValueError):
        return MIN_SCORE_DEFAULT


def in_quiet_hours():
    qh = (cfg.load_config() or {}).get("quietHours") or {}
    start = int(qh.get("startUtc", 0))
    end = int(qh.get("endUtc", 4))
    apex = int(qh.get("apexBypassScore", 11))
    if start == end:
        return False, -1, apex
    h = datetime.now(timezone.utc).hour
    if start < end:
        return (start <= h < end), h, apex
    return (h >= start or h < end), h, apex


# ═══════════════════════════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════════════════════════

def get_hype_full_picture():
    raw = cfg.mcporter_call(
        "market_get_asset_data", asset=ASSET,
        candle_intervals=["5m", "15m", "1h", "4h"],
        include_funding=True, include_order_book=False,
    )
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else None
    return data if isinstance(data, dict) else None


def get_hype_sm_direction():
    """Return (direction, pct, traders, cc_15m) for HYPE SM signal."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return None, 0, 0, 0
    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0, 0, 0

    long_pct = 0.0
    short_pct = 0.0
    traders_sum = 0
    cc_15m = 0.0
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", ""))).upper()
        if token != ASSET:
            continue
        direction = str(m.get("direction", "")).upper()
        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0))
        cc = safe_float(m.get("contribution_pct_change_15m", 0))
        if direction == "LONG":
            long_pct = pct
            traders_sum += traders
            cc_15m = cc
        elif direction == "SHORT":
            short_pct = pct
            traders_sum += traders
            cc_15m = cc

    total = long_pct + short_pct
    if total == 0:
        return "NEUTRAL", 0, traders_sum, cc_15m
    long_ratio = (long_pct / total) * 100
    if long_ratio > 58:
        return "LONG", long_pct, traders_sum, cc_15m
    if long_ratio < 42:
        return "SHORT", short_pct, traders_sum, cc_15m
    return "NEUTRAL", max(long_pct, short_pct), traders_sum, cc_15m


def get_funding_regime():
    try:
        fr = cfg.mcporter_call("market_get_funding_regime")
        if fr:
            data = fr.get("data", fr)
            if isinstance(data, dict):
                return data.get("regime")
    except Exception:
        pass
    return None


def get_funding_history_hype():
    try:
        fh = cfg.mcporter_call("market_get_funding_history", asset=ASSET)
        if fh:
            data = fh.get("data", fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def get_btc_correlation():
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


def fetch_held_assets():
    if not STRATEGY_ADDRESS:
        return []
    try:
        ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=STRATEGY_ADDRESS)
        if not ch:
            return []
        data = ch.get("data", ch)
        held = []
        for section in ("main", "xyz"):
            s = data.get(section, {})
            if not isinstance(s, dict):
                continue
            for ap in s.get("assetPositions", []):
                pos = ap.get("position", ap)
                szi = float(pos.get("szi", 0) or 0)
                if szi == 0:
                    continue
                coin = pos.get("coin", "")
                if coin:
                    held.append(coin)
        return held
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# THESIS BUILDER — preserved from v3.0.3 build_hype_thesis
# ═══════════════════════════════════════════════════════════════

def build_hype_thesis():
    """Build HYPE entry thesis. Returns thesis dict or {"blocked":True,"reason":...}."""
    hype_data = get_hype_full_picture()
    if not hype_data:
        return {"blocked": True, "reason": "no_asset_data"}

    candles_5m = hype_data.get("candles", {}).get("5m", [])
    candles_15m = hype_data.get("candles", {}).get("15m", [])
    candles_1h = hype_data.get("candles", {}).get("1h", [])
    candles_4h = hype_data.get("candles", {}).get("4h", [])
    asset_ctx = hype_data.get("asset_context", hype_data.get("assetContext", {})) or {}
    funding = safe_float(asset_ctx.get("funding", 0))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return {"blocked": True, "reason": "insufficient_candles"}

    price = candle_close(candles_5m[-1])

    # GATE 1: 4h trend structure
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return {"blocked": True, "reason": "4h_NEUTRAL"}

    # GATE 2: strong 4h structural alignment
    if trend_strength_4h < MIN_4H_STRUCTURE:
        return {"blocked": True, "reason": f"4h_weak_{trend_strength_4h:.0%}"}

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # GATE 3: 1h matches 4h
    trend_1h, _ = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return {"blocked": True, "reason": f"1h_{trend_1h}_vs_4h_{trend_4h}"}

    # GATE 4: 15m momentum confirms
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)

    # v4.2: mom_4h via TRAILING WINDOW using 1H candles, not grid-based
    # 4h candle. The grid-based approach (price_momentum(candles_4h, 1))
    # only sees ONE 4h candle's change — typically 0.5-1.5% on HYPE
    # multi-day grinds. The trailing window captures cumulative momentum
    # across the past 4 hours, matching how price actually moved. Same
    # fix Kestrel got in v1.1 (kestrel-scanner v1.1 trailing-4H comment).
    if len(candles_1h) >= 5:
        close_1h_now = candle_close(candles_1h[-1])
        close_1h_4h_ago = candle_close(candles_1h[-5])
        if close_1h_4h_ago > 0:
            mom_4h = ((close_1h_now - close_1h_4h_ago) / close_1h_4h_ago) * 100
        else:
            mom_4h = price_momentum(candles_4h, 1)   # fallback to grid-based
    else:
        mom_4h = price_momentum(candles_4h, 1)       # not enough 1h candles

    if direction == "LONG" and mom_15m < MIN_MOM_15M:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}
    if direction == "SHORT" and mom_15m > -MIN_MOM_15M:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}

    # GATE 5: Base-tech floor
    strong_15m = abs(mom_15m) > MIN_MOM_15M * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return {"blocked": True,
                "reason": f"base_tech_weak_15m({mom_15m:+.2f})_5m({mom_5m:+.2f})"}

    # GATE 6 (v3.0.3): 4h MAGNITUDE floor — reject dead-flat chop.
    # v4.2: now uses trailing-window mom_4h above, threshold lowered to 1.0%.
    if abs(mom_4h) < MIN_4H_MAGNITUDE_PCT:
        return {"blocked": True, "reason": f"4h_magnitude_too_flat_{mom_4h:+.2f}_min_{MIN_4H_MAGNITUDE_PCT}"}

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
        return {"blocked": True, "reason": f"sm_opposes_{sm_dir}_vs_setup_{direction}"}

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

    # Funding regime
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

    # Funding persistence
    fh = get_funding_history_hype()
    persistence_h = None
    if fh:
        ph = fh.get("persistence_hours")
        try:
            persistence_h = float(ph) if ph is not None else None
        except (TypeError, ValueError):
            persistence_h = None
        if persistence_h is not None and persistence_h >= 6:
            score += 1
            reasons.append(f"FUNDING_PERSISTENT_{persistence_h:.0f}h")

    # Volume
    vol_1h = volume_ratio(candles_1h)
    if vol_1h >= 1.2:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.7:
        score -= 1
        reasons.append("vol_weak")

    vt = volume_trend(candles_1h)
    if vt > 15:
        score += 1
        reasons.append(f"vol_rising_{vt:+.0f}%")

    # OI velocity
    oi_vel = hype_data.get("oi_velocity") if isinstance(hype_data.get("oi_velocity"), dict) else {}
    oi_change = None
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
                oi_change = None

    # BTC correlation
    btc_mom_15m, btc_mom_1h = get_btc_correlation()
    if btc_mom_15m is not None and btc_mom_1h is not None:
        btc_agrees = (direction == "LONG" and btc_mom_15m > 0 and btc_mom_1h > 0) or \
                     (direction == "SHORT" and btc_mom_15m < 0 and btc_mom_1h < 0)
        if btc_agrees:
            score += 1
            reasons.append(f"btc_confirms_{btc_mom_1h:+.2f}%")

    # RSI hard gates + bonus
    closes_1h = [candle_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > RSI_MAX_LONG:
        return {"blocked": True, "reason": f"rsi_overbought_{rsi:.0f}"}
    if direction == "SHORT" and rsi < RSI_MIN_SHORT:
        return {"blocked": True, "reason": f"rsi_oversold_{rsi:.0f}"}
    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4h momentum bonus
    if abs(mom_4h) > STRONG_4H_PCT:
        score += 1
        reasons.append(f"4h_strong_{mom_4h:+.1f}%")

    # Move-exhaustion penalty
    if abs(mom_4h) >= MOVE_EXHAUSTION_PCT:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif abs(mom_4h) >= MOVE_TIRING_PCT:
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
        "sm_pct": sm_pct,
        "sm_traders": sm_count,
        "sm_cc_15m": sm_cc_15m,
        "funding": funding,
        "regime": regime,
        "persistence_h": persistence_h,
        "oi_change_1h": oi_change,
        "btc_mom_15m": btc_mom_15m,
        "btc_mom_1h": btc_mom_1h,
        "vol_1h": vol_1h,
        "mom": {"5m": mom_5m, "15m": mom_15m, "1h": mom_1h, "4h": mom_4h},
        "trend_4h": trend_4h,
        "trend_strength_4h": trend_strength_4h,
    }


# ═══════════════════════════════════════════════════════════════
# INGEST
# ═══════════════════════════════════════════════════════════════

def push_signal(thesis, held_assets):
    if not STRATEGY_ADDRESS:
        print(
            "ERROR: strategy wallet not resolved — set 'wallet' in "
            "wolverine-config.json (preferred) or WOLVERINE_WALLET_ADDRESS env var",
            file=sys.stderr,
        )
        return False

    if "HYPE" in {h.upper() for h in held_assets}:
        return False

    leverage, tier_label = get_leverage_tier(thesis["score"])
    mom = thesis["mom"]

    payload = {
        "asset": ASSET,
        "direction": thesis["direction"],
        "score": thesis["score"] / 17.0,
        "signal_type": "WOLVERINE_HYPE_HYBRID",
        "data": {
            "score": thesis["score"],
            "tier": tier_label,
            "leverage": leverage,
            "reasons": thesis["reasons"],
            "smPct": thesis["sm_pct"],
            "smTraders": thesis["sm_traders"],
            "smCc15m": thesis["sm_cc_15m"],
            "trend4h": thesis["trend_4h"],
            "trendStrength4h": thesis["trend_strength_4h"],
            "rsi": thesis["rsi"],
            "funding": thesis["funding"],
            "fundingRegime": thesis.get("regime"),
            "fundingPersistenceHours": thesis.get("persistence_h"),
            "oiChange1h": thesis.get("oi_change_1h"),
            "vol1h": thesis.get("vol_1h"),
            "priceChange5m": mom["5m"],
            "priceChange15m": mom["15m"],
            "priceChange1h": mom["1h"],
            "priceChange4h": mom["4h"],
            "btcMom15m": thesis.get("btc_mom_15m"),
            "btcMom1h": thesis.get("btc_mom_1h"),
            "heldAssets": held_assets,
        },
    }

    cmd = [
        OPENCLAW_BIN, "senpi", "external-scanner", "ingest",
        "--address", STRATEGY_ADDRESS,
        "--scanner", SCANNER_NAME,
        "--payload", json.dumps(payload),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            print(f"INGEST_FAILED HYPE: {result.stderr}", file=sys.stderr)
            return False
        response = json.loads(result.stdout) if result.stdout.strip() else {}
        if not response.get("ok", False):
            print(f"INGEST_REJECTED HYPE: {response.get('error', {})}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"INGEST_EXCEPTION HYPE: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    lock = acquire_lock()
    if lock is None:
        print(json.dumps({
            "status": "skip",
            "reason": "previous run still active — cron reentrancy guard",
            "_wolverine_producer_version": VERSION,
        }))
        return

    try:
        thesis = build_hype_thesis()

        if thesis.get("blocked"):
            elapsed = time.time() - run_start
            print(json.dumps({
                "status": "ok",
                "heartbeat": "NO_REPLY",
                "note": f"BLOCKED: {thesis['reason']}",
                "elapsed_sec": round(elapsed, 2),
                "_wolverine_producer_version": VERSION,
            }))
            return

        min_score = get_min_score()
        if thesis["score"] < min_score:
            print(json.dumps({
                "status": "ok",
                "heartbeat": "NO_REPLY",
                "note": f"score_low {thesis['score']}/{min_score}",
                "direction": thesis["direction"],
                "reasons": thesis["reasons"],
                "_wolverine_producer_version": VERSION,
            }))
            return

        # FP-001 quiet hours
        quiet, current_hour, apex_bypass = in_quiet_hours()
        if quiet and thesis["score"] < apex_bypass:
            print(json.dumps({
                "status": "ok",
                "heartbeat": "NO_REPLY",
                "note": f"QUIET_HOURS hour={current_hour}_UTC score={thesis['score']}_below_apex_{apex_bypass}",
                "direction": thesis["direction"],
                "_wolverine_producer_version": VERSION,
            }))
            return

        held_assets = fetch_held_assets()
        pushed = push_signal(thesis, held_assets)

        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "action": "PUSHED" if pushed else "PUSH_FAILED",
            "direction": thesis["direction"],
            "score": thesis["score"],
            "tier": get_leverage_tier(thesis["score"])[1],
            "reasons": thesis["reasons"][:5],
            "held_assets": held_assets,
            "elapsed_sec": round(elapsed, 2),
            "_wolverine_producer_version": VERSION,
        }))
    finally:
        release_lock(lock)


if __name__ == "__main__":
    main()
