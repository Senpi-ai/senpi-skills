#!/usr/bin/env python3
# Senpi GRIZZLY Producer v7.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""GRIZZLY v7.0.0 Producer — senpi_runtime_helpers migration.

v7.0.0 (2026-05-08) — plumbing migration. NO thesis change. BTC
single-asset alpha hunter, six-gate entry validation, v5.5 macro
V-recovery gate, RSI hard gates, multi-factor scoring (~17 max),
conviction-scaled leverage (7x default / 10x conviction / 10x apex),
MIN_SCORE 12, FP-001 quiet hours, FP-003 requireAllConfirmations
gate — all preserved verbatim from v6.0.0.

Six-layer plumbing flip per migration-cookbook (matches Cheetah
v7.0.0 / Kodiak v7.0.0 / Polar v5.0.0 / Wolverine v5.0.0 patterns):
  1. MCP calls — cfg.mcporter_call() shim now routes through
     SenpiClient.mcp_call() (direct HTTPS).
  2. Signal emit — subprocess.run(["openclaw","senpi","external-scanner",
     "ingest"...]) replaced by cfg._wrapper_client.push_signal(...).
     Per Rachin's PR #209 review: signal_type passed as explicit
     kwarg ("GRIZZLY_BTC_TREND"), no dead fields in payload.
  3. Reentrancy — hand-rolled fcntl flock dropped. producer_daemon
     owns per-tick scanner_lock.
  4. Tick scheduling — openclaw cron + agentTurn replaced by
     producer_daemon (long-lived process; zero per-tick LLM cost).
  5. Per-tick cache + parallel fan-out NOT adopted.
  6. /state alive_check — wallet=/scanner= passed; daemon
     self-terminates when the runtime is deleted or the scanner
     is renamed.

v6.0.0 thesis preserved unchanged.

Original v6.0.0 docstring (preserved for context):

v5.x was a full-agency scanner: 1073 lines with a 3-mode state machine
(HUNTING/RIDING/STALKING), Python-side cooldowns + dynamic daily caps,
reload/scale-in evaluation, and direct create_position calls. The
state machine drifted across runtime restarts and silent-state-file
corruption was diagnosable only after the fact.

v6.0 flips to producer + v2 runtime (Wolverine v4 / Cheetah v6 /
Vulture v3 template):
  - Producer (grizzly-producer.py) emits BTC signals via
    `SenpiClient.push_signal()` (direct HTTP POST). NO execution code.
  - Runtime LLM gate is pass-through — producer has applied every
    filter; LLM only catches malformed signals.
  - risk.guard_rails ENFORCES daily caps, drawdown halt, consecutive-
    loss halt, per-asset cooldown. No Python state to drift / crash.
  - DSL uses FEE_OPTIMIZED_LIMIT on entries AND exits.
  - Trade chain DB emits per-trade telemetry — chain DB visibility
    on Grizzly for the first time.

WHAT'S PRESERVED FROM v5.8:
  - Single-asset BTC thesis (Kodiak/Wolverine/Polar family pattern)
  - Six-gate entry validation:
    * GATE 1: 4h trend != NEUTRAL
    * GATE 2: 4h structural strength ≥ 0.75 (Kodiak v5.1 fix)
    * GATE 3: 1h matches 4h direction
    * GATE 4: 15m momentum aligned ≥ MIN_MOM_15M (0.05)
    * GATE 5: base-tech floor (strong_15m OR aligned_5m)
    * GATE 6: v5.5 MACRO V-RECOVERY GATE — block fades within 1.25%
      of 24h extreme. Diagnosed on 2026-04-23 BTC V-bottom.
  - SM HARD BLOCK if direction opposes
  - RSI hard gates (70 LONG / 30 SHORT) — BTC-tuned
  - Multi-factor scoring (~17 max points)
  - MIN_SCORE = 12 (config-overridable; v5.8 "hit fewer, win bigger")
  - BTC-specific thresholds: 15m momentum, RSI, move-exhaustion,
    vol ratio — all preserved verbatim
  - Conviction-scaled leverage: 10x apex / 10x conviction / 7x default

WHAT'S DROPPED (now runtime-side):
  - 3-mode state machine (HUNTING/RIDING/STALKING) — runtime tracks
  - evaluate_reload() — DSL owns position lifecycle, no Python reload
  - get_dynamic_daily_cap() — risk.guard_rails enforces
  - has_resting_orders() — runtime tracks open orders
  - create_position() execution — runtime LLM gate executes
  - get_safe_leverage() — producer asserts leverage; LLM honors

FLEET PATCHES:
  - FP-001 quiet hours (00-04 UTC unless apex score 14+)
  - FP-002 hard rule in SKILL.md (user-conversation Claude sessions
    are read-only — only producer cron + DSL engine are write paths)
  - FP-003 requireAllConfirmations gate (default true) — every soft
    confirmation (4TF + SM + Funding + Volume + OI) must fire, not
    just summed score

Environment / config resolution:
  Strategy wallet read from config/grizzly-config.json (canonical).
  GRIZZLY_WALLET_ADDRESS env var supported as optional override.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grizzly_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "7.0.0"

# Hardcoded — must match runtime.yaml external_scanner.name.
SCANNER_NAME = "grizzly_signals"

# Signal type passed explicitly to push_signal() per Rachin's review
# of Cheetah PR #209.
SIGNAL_TYPE = "GRIZZLY_BTC_TREND"


# Reentrancy guard removed in v7.0.0. producer_daemon owns the
# per-tick scanner_lock with stale-PID auto-recovery via os.kill(pid, 0).
# Do NOT add an inner scanner_lock(...) inside main() — fcntl flock
# is not reentrant; nested call raises BlockingIOError every tick.


def _resolve_wallet():
    """Resolve strategy wallet — config.json is the canonical source.
    Env var GRIZZLY_WALLET_ADDRESS is supported as an optional override."""
    env_val = (os.environ.get("GRIZZLY_WALLET_ADDRESS") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — BTC-tuned (preserved verbatim from v5.8)
# ═══════════════════════════════════════════════════════════════

ASSET = "BTC"
MIN_SCORE_DEFAULT = 12          # v5.8: was 10 hardcoded; now config-overridable
MAX_LEVERAGE = 10               # Fleet-wide ceiling

# BTC-specific momentum thresholds (BTC ~3x slower than SOL)
MIN_MOM_15M = 0.05              # SOL uses 0.10
RSI_MAX_LONG = 70               # SOL uses 74 — BTC rarely pushes that extreme
RSI_MIN_SHORT = 30              # SOL uses 26
STRONG_4H_PCT = 2.0             # BTC "strong" 4h move (SOL: 4.0)
MOVE_EXHAUSTION_PCT = 2.5       # SOL: 4.0
MOVE_TIRING_PCT = 1.5           # SOL: 2.5
MIN_VOL_RATIO = 1.1             # SOL: 1.2
FUNDING_EXTREME = 0.0008        # BTC funding lower magnitude than alts
FUNDING_CROWDED = 0.003

# v5.3 4h structural strength floor — Kodiak v5.1 ported.
# Marginal patterns (60% threshold) flip on the next candle close.
# 75% (4 of 5 candles) rejects bull-rally pullbacks that briefly
# scored as BEARISH. Live failure 2026-04-23: BTC $70k→$78k run
# produced 9 of 15 SHORT losses at 60% threshold.
MIN_4H_STRUCTURE = 0.75

# v5.5 MACRO V-RECOVERY GATE.
# 4h structural metric is lagging — takes 8-12h for 4h candle structure
# to break after a reversal. During V-recoveries the 4h gate still says
# "BEARISH 100%" while price has rallied 1-2% off the 24h low.
# Live failure 2026-04-23: BTC V-bottomed at $76.4k at ~04:00 UTC,
# rallied to $77,495 by 10:38 UTC (+1.43% off low). trend_strength_4h
# was 0.80, fired SHORT. BTC continued to $78,063. Second SHORT at
# 14:12 UTC had trend_strength_4h = 1.0 and fired again. Both losers.
# This gate checks distance from 24h extreme using 1h-candle highs/lows.
# Block SHORTs within 1.25% of 24h low; mirror for LONGs off 24h high.
MACRO_VRECOVERY_DISTANCE_PCT = 1.25

# Conviction-scaled leverage tiers (v5.8 — apex pushes 10x; BTC has
# less wick risk than alts so safe to apex-lever)
LEVERAGE_TIERS = [
    {"min_score": 14, "leverage": 10, "label": "apex"},
    {"min_score": 12, "leverage": 10, "label": "conviction"},
]
DEFAULT_LEVERAGE = 7


# ═══════════════════════════════════════════════════════════════
# HELPERS (identical to Kodiak/Wolverine — shared family math)
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
    if len(candles) < lookback + 2:
        return 0
    vols = [candle_volume(c) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


def get_leverage_tier(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"], tier["label"]
    return DEFAULT_LEVERAGE, "default"


# ═══════════════════════════════════════════════════════════════
# v5.8 fleet patches
# ═══════════════════════════════════════════════════════════════

def _config():
    try:
        return cfg.load_config() or {}
    except Exception:
        return {}


def get_min_score():
    try:
        return int(_config().get("minScore", MIN_SCORE_DEFAULT))
    except (TypeError, ValueError):
        return MIN_SCORE_DEFAULT


def in_quiet_hours():
    """FP-001: skip emission during low-liquidity UTC window.
    Default 00:00-04:00 UTC. Apex setups (score >= apex_bypass) bypass.
    Returns (in_quiet: bool, hour: int, apex_bypass: int)."""
    c = _config()
    qh = c.get("quietHours") or {}
    # Support both flat (v5.8) and nested (Wolverine v4) forms
    start = int(qh.get("startUtc", c.get("quietHoursStartUtc", 0)))
    end = int(qh.get("endUtc", c.get("quietHoursEndUtc", 4)))
    apex = int(qh.get("apexBypassScore", c.get("quietHoursApexBypassScore", 14)))
    if start == end:
        return False, -1, apex
    h = datetime.now(timezone.utc).hour
    if start < end:
        return (start <= h < end), h, apex
    return (h >= start or h < end), h, apex


def all_confirmations_present(thesis):
    """FP-003: require each soft confirmation to contribute, not just
    score-summed. Five Kodiak-family confirmations (4TF + SM + Funding
    + Volume + OI) must all fire. Returns (ok: bool, missing: list)."""
    reasons = thesis.get("reasons") or []
    needed = {
        "4TF_ALIGNED": ("4TF_aligned",),
        "SM_ALIGNED": ("sm_aligned_",),
        "FUNDING_OK": ("funding_pays_",),
        "VOLUME": ("vol_",),
        "OI_ACCELERATING": ("OI_ACCELERATING_", "OI_rising_", "oi_growing_"),
    }
    missing = []
    for label, prefixes in needed.items():
        if not any(any(r.startswith(p) for p in prefixes) for r in reasons):
            missing.append(label)
    return (not missing), missing


def require_all_confirmations_enabled():
    return bool(_config().get("requireAllConfirmations", True))


# ═══════════════════════════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════════════════════════

def get_btc_full_picture():
    raw = cfg.mcporter_call(
        "market_get_asset_data", asset=ASSET,
        candle_intervals=["5m", "15m", "1h", "4h"],
        include_funding=True, include_order_book=False,
    )
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else None
    return data if isinstance(data, dict) else None


def get_btc_sm_direction():
    """Return (direction, pct, traders, cc_15m) for BTC SM signal."""
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


def get_funding_history_btc():
    try:
        fh = cfg.mcporter_call("market_get_funding_history", asset=ASSET)
        if fh:
            data = fh.get("data", fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


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
# THESIS BUILDER — preserved from v5.8 build_btc_thesis
# ═══════════════════════════════════════════════════════════════

def build_btc_thesis():
    """Build BTC entry thesis. Returns thesis dict or {"blocked":True,"reason":...}."""
    btc_data = get_btc_full_picture()
    if not btc_data:
        return {"blocked": True, "reason": "no_asset_data"}

    candles_5m = btc_data.get("candles", {}).get("5m", [])
    candles_15m = btc_data.get("candles", {}).get("15m", [])
    candles_1h = btc_data.get("candles", {}).get("1h", [])
    candles_4h = btc_data.get("candles", {}).get("4h", [])
    asset_ctx = btc_data.get("asset_context", btc_data.get("assetContext", {})) or {}
    funding = safe_float(asset_ctx.get("funding", 0))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return {"blocked": True, "reason": "insufficient_candles"}

    price = candle_close(candles_5m[-1])

    # GATE 1: 4h trend structure
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return {"blocked": True, "reason": "4h_NEUTRAL"}

    # GATE 2: strong 4h structural alignment (v5.3 Kodiak v5.1 fix)
    if trend_strength_4h < MIN_4H_STRUCTURE:
        return {"blocked": True, "reason": f"4h_weak_{trend_strength_4h:.0%}"}

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # GATE 3: 1h matches 4h
    trend_1h, trend_strength_1h = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return {"blocked": True, "reason": f"1h_{trend_1h}_vs_4h_{trend_4h}"}

    # GATE 4: 15m momentum confirms direction
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)

    if direction == "LONG" and mom_15m < MIN_MOM_15M:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}
    if direction == "SHORT" and mom_15m > -MIN_MOM_15M:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}

    # GATE 5: Base-tech-score floor (v5.3 Kodiak v5.1 pattern)
    strong_15m = abs(mom_15m) > MIN_MOM_15M * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return {"blocked": True,
                "reason": f"base_tech_weak_15m({mom_15m:+.2f})_5m({mom_5m:+.2f})"}

    # GATE 6: v5.5 MACRO V-RECOVERY GATE — preserved verbatim
    # See module docstring for live failure case 2026-04-23.
    if len(candles_1h) >= 24:
        highs_1h_24 = [candle_high(c) for c in candles_1h[-24:]]
        lows_1h_24 = [candle_low(c) for c in candles_1h[-24:]]
        high_24h = max([h for h in highs_1h_24 if h > 0], default=0)
        low_24h = min([l for l in lows_1h_24 if l > 0], default=0)
        if direction == "SHORT" and low_24h > 0:
            distance_from_low_pct = (price - low_24h) / low_24h * 100
            if distance_from_low_pct > MACRO_VRECOVERY_DISTANCE_PCT:
                return {"blocked": True,
                        "reason": f"V_RECOVERY_BLOCK_SHORT_{distance_from_low_pct:.2f}%_off_low"}
        if direction == "LONG" and high_24h > 0:
            distance_from_high_pct = (high_24h - price) / high_24h * 100
            if distance_from_high_pct > MACRO_VRECOVERY_DISTANCE_PCT:
                return {"blocked": True,
                        "reason": f"V_RECOVERY_BLOCK_LONG_{distance_from_high_pct:.2f}%_off_high"}

    # SCORING
    score = 0
    reasons = []

    # 4h trend (3 pts — the foundation)
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{trend_strength_4h:.0%}")

    # 1h trend agreement (2 pts)
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")

    # 15m momentum strength (1 pt if strong)
    if strong_15m:
        score += 1
        reasons.append(f"15m_strong_{mom_15m:+.2f}%")
    else:
        reasons.append(f"15m_{mom_15m:+.2f}%")

    # 5m alignment (1 pt — all 4 timeframes agree)
    if aligned_5m:
        score += 1
        reasons.append("4TF_aligned")

    # SM positioning — HARD BLOCK if opposes (BTC has strongest SM signal)
    sm_dir, sm_pct, sm_count, sm_cc_15m = get_btc_sm_direction()
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}traders")
        if sm_pct > 65:
            score += 1
            reasons.append("sm_strongly_tilted")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        return {"blocked": True, "reason": f"sm_opposes_{sm_dir}_vs_setup_{direction}"}

    # 15m velocity freshness
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
    fh = get_funding_history_btc()
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

    # Volume confirmation
    vol_1h = volume_ratio(candles_1h)
    if vol_1h >= MIN_VOL_RATIO:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.7:
        score -= 1
        reasons.append("vol_weak")

    vt = volume_trend(candles_1h)
    if vt > 15:
        score += 1
        reasons.append(f"vol_rising_{vt:+.0f}%")

    # OI growth proxy (legacy fallback if OI velocity missing)
    vol_recent = sum(candle_volume(c) for c in candles_1h[-3:])
    vol_earlier = sum(candle_volume(c) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1
        reasons.append(f"oi_growing_{oi_proxy:+.0f}%")

    # OI velocity (real OI data when available)
    oi_vel = btc_data.get("oi_velocity", {}) if isinstance(btc_data.get("oi_velocity"), dict) else {}
    oi_change = None
    oi_vel_1h = oi_vel.get("1h", {}) if isinstance(oi_vel.get("1h"), dict) else {}
    oi_vel_change = oi_vel_1h.get("change_pct")
    if oi_vel_change is not None:
        try:
            oi_change = float(oi_vel_change)
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
        "vol_1h": vol_1h,
        "mom": {"5m": mom_5m, "15m": mom_15m, "1h": mom_1h, "4h": mom_4h},
        "trend_4h": trend_4h,
        "trend_strength_4h": trend_strength_4h,
    }


# ═══════════════════════════════════════════════════════════════
# INGEST
# ═══════════════════════════════════════════════════════════════

def push_signal(thesis, held_assets):
    """Push a signal payload to the runtime via senpi_runtime_helpers.

    Direct HTTP POST to runtime API on 127.0.0.1; no subprocess.
    asset/direction/signal_type at top-level kwargs (Rachin's PR #209
    review). Score normalized 0..1 for SignalItem.score (Grizzly's
    composite 0-17 scaled), with raw int score in `data` for telemetry.
    """
    if not STRATEGY_ADDRESS:
        print(
            "ERROR: strategy wallet not resolved — set 'wallet' in "
            "grizzly-config.json (preferred) or GRIZZLY_WALLET_ADDRESS env var",
            file=sys.stderr,
        )
        return False

    if "BTC" in {h.upper() for h in held_assets}:
        return False

    leverage, tier_label = get_leverage_tier(thesis["score"])
    mom = thesis["mom"]

    data_block = {
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
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=ASSET,
            direction=thesis["direction"],
            score=thesis["score"] / 17.0,   # 0..1 confidence (Grizzly composite normalized)
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED BTC {thesis['direction']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        print(f"INGEST_EXCEPTION BTC {thesis['direction']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    """Single tick. NO inner scanner_lock — daemon owns it."""
    run_start = time.time()

    thesis = build_btc_thesis()

    if thesis.get("blocked"):
        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "heartbeat": "NO_REPLY",
            "note": f"BLOCKED: {thesis['reason']}",
            "elapsed_sec": round(elapsed, 2),
            "_grizzly_producer_version": VERSION,
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
            "_grizzly_producer_version": VERSION,
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
            "_grizzly_producer_version": VERSION,
        }))
        return

    # FP-003 require-all-confirmations
    if require_all_confirmations_enabled():
        ok, missing = all_confirmations_present(thesis)
        if not ok:
            print(json.dumps({
                "status": "ok",
                "heartbeat": "NO_REPLY",
                "note": f"MISSING_CONFIRMATIONS {missing} score={thesis['score']}",
                "direction": thesis["direction"],
                "reasons": thesis["reasons"],
                "_grizzly_producer_version": VERSION,
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
        "_grizzly_producer_version": VERSION,
    }))


if __name__ == "__main__":
    # v7.0.0 — long-lived daemon. Replaces openclaw cron + agentTurn.
    # producer_daemon owns the per-tick scanner_lock with stale-PID
    # auto-recovery.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=180,           # Grizzly's v6.x cron was every 3 min
        name=f"grizzly-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=240,
    )
