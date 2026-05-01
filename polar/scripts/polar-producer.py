#!/usr/bin/env python3
# Senpi POLAR Producer v4.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""POLAR v4.0.0 Producer — ETH alpha signal emitter for v2 runtime.

v3.x was a full-agency scanner that called create_position directly,
maintained Python-side trade counters, cooldowns, and resting-order
guards. v4.0 flips to producer + v2 runtime (Vulture v3.0 / Scorpion
v4.x pattern).

ARCHITECTURE CHANGE:
  - Producer (polar-producer.py) emits signals via
    `openclaw senpi external-scanner ingest`. NO execution code.
  - Runtime LLM gate (decision_mode: llm) is pass-through (Roach
    pattern) — producer has applied every filter; LLM only catches
    malformed signals.
  - risk.guard_rails ENFORCES daily caps, drawdown halt, consecutive-
    loss halt, per-asset cooldown (no Python state to drift / crash).
  - DSL uses FEE_OPTIMIZED_LIMIT on entries AND exits — saves
    ~0.020-0.030% per maker-filled close vs v3.x MARKET orders.
  - Trade chain DB emits LIFECYCLE / DECISION_EXECUTED / ACTION_RESULT
    / DSL_CREATED / DSL_CLOSED for every trade — telemetry restored.

WHAT'S PRESERVED FROM v3.0.6:
  - ETH single-asset thesis
  - Hyperfeed SM gates (pct≥5%, traders≥30, cc_15m≥0.3 acceleration)
  - Structural gates (4h trend != NEUTRAL, 4h trend matches SM
    direction, 1h matches 4h, 15m momentum aligned, RSI not extreme)
  - Multi-factor scoring (~17 max points across base-tech, SM
    concentration, SM velocity, SM accelerating, trader depth,
    funding, OI velocity, BTC correlation, RSI room, 4h momentum,
    move-exhaustion penalty)
  - MIN_SCORE = 14 (default; config-overridable)
  - Conviction-tiered leverage (5x standard / 7x conviction / 10x apex)
  - DSL preset: time-cuts disabled, Phase 1 max_loss 25% / retrace 8 /
    3 breaches, Phase 2 leverage-aware ladder

FLEET PATCHES:
  - FP-001 quiet hours (00-04 UTC unless apex score 17+)
  - FP-002 hard rule in SKILL.md (user-conversation Claude sessions
    are read-only — only producer cron + DSL engine are write paths)

Environment / config resolution:
  Strategy wallet is read from config/polar-config.json (canonical
  source). POLAR_WALLET_ADDRESS env var supported as optional
  override but NOT required for routine cron runs.

  SENPI_API_KEY              — MCP access (required)
  POLAR_WALLET_ADDRESS       — optional override; defaults to config.wallet
  SENPI_MCP_URL              — optional, default https://mcp.prod.senpi.ai/mcp
  OPENCLAW_BIN               — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME      — optional override (default "polar_signals")
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
import polar_config as cfg


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD (fleet-standard fcntl pattern)
# ═══════════════════════════════════════════════════════════════

_LOCK_DIR = Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "polar-strategy" / "state"
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


VERSION = "4.0.0"
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "polar_signals")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")


def _resolve_wallet():
    """Resolve strategy wallet — config.json is the canonical source.
    Env var POLAR_WALLET_ADDRESS is supported as an optional override."""
    env_val = (os.environ.get("POLAR_WALLET_ADDRESS") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved from v3.0.6
# ═══════════════════════════════════════════════════════════════

ASSET = "ETH"
MIN_SCORE_DEFAULT = 14            # config-overridable via "minScore"

# Hard gates (v3.x — proven calibrated)
MIN_SM_ACCEL_PCT = 0.3
MIN_SM_CONCENTRATION = 5.0
MIN_SM_TRADER_COUNT = 30
MIN_TREND_STRENGTH_4H = 0.75
MIN_MOM_15M_PCT = 0.1

# Conviction-scaled leverage tiers (preserved from v3.x)
LEVERAGE_TIERS = [
    {"min_score": 17, "leverage": 10, "label": "apex"},
    {"min_score": 15, "leverage": 7,  "label": "conviction"},
    {"min_score": 14, "leverage": 5,  "label": "standard"},
]
DEFAULT_LEVERAGE = 5
MAX_LEVERAGE = 10


# ═══════════════════════════════════════════════════════════════
# HELPERS (preserved from v3.x)
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
    """FP-001: skip emission during low-liquidity UTC window.
    Default 00:00-04:00 UTC; apex (score >= apexBypassScore, default 17)
    bypasses. Returns (in_quiet, hour, apex_bypass)."""
    qh = (cfg.load_config() or {}).get("quietHours") or {}
    start = int(qh.get("startUtc", 0))
    end = int(qh.get("endUtc", 4))
    apex = int(qh.get("apexBypassScore", 17))
    if start == end:
        return False, -1, apex
    h = datetime.now(timezone.utc).hour
    if start < end:
        return (start <= h < end), h, apex
    return (h >= start or h < end), h, apex


# ═══════════════════════════════════════════════════════════════
# DATA FETCH (preserved from v3.x)
# ═══════════════════════════════════════════════════════════════

def get_eth_asset_data():
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
            cc_15m, cc_1h, cc_4h = cc_15m_val, cc_1h_val, cc_4h_val
        elif direction == "SHORT":
            short_pct = pct
            traders_sum += traders
            cc_15m, cc_1h, cc_4h = cc_15m_val, cc_1h_val, cc_4h_val

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
# THESIS BUILDER — preserved from v3.0.6 build_eth_thesis
# ═══════════════════════════════════════════════════════════════

def build_eth_thesis():
    """Build an ETH entry thesis gated by hyperfeed SM + candle structure.
    Returns a thesis dict on success, or {"blocked": True, "reason": "..."}
    on any gate failure."""
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

    # Structural gates
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return {"blocked": True, "reason": f"4h_NEUTRAL_(sm_said_{direction})"}
    if trend_strength_4h < MIN_TREND_STRENGTH_4H:
        return {"blocked": True, "reason": f"4h_weak_{trend_strength_4h:.0%}"}

    structural_dir = "LONG" if trend_4h == "BULLISH" else "SHORT"
    if structural_dir != direction:
        return {"blocked": True,
                "reason": f"direction_conflict_sm_{direction}_vs_4h_{structural_dir}"}

    trend_1h, _ = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return {"blocked": True, "reason": f"1h_{trend_1h}_vs_4h_{trend_4h}"}

    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)

    if direction == "LONG" and mom_15m < MIN_MOM_15M_PCT:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}
    if direction == "SHORT" and mom_15m > -MIN_MOM_15M_PCT:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}

    closes_1h = [candle_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > 74:
        return {"blocked": True, "reason": f"rsi_overbought_{rsi:.0f}"}
    if direction == "SHORT" and rsi < 26:
        return {"blocked": True, "reason": f"rsi_oversold_{rsi:.0f}"}

    strong_15m = abs(mom_15m) > MIN_MOM_15M_PCT * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return {"blocked": True,
                "reason": f"base_tech_weak_15m({mom_15m:+.2f})_5m({mom_5m:+.2f})"}

    # ─── SCORING (preserved from v3.0.6) ────────────────────────
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

    if sm["pct"] >= 15:
        score += 3
        reasons.append(f"SM_DOMINANT_{sm['pct']:.1f}%_{sm['traders']}t")
    elif sm["pct"] >= 10:
        score += 2
        reasons.append(f"SM_STRONG_{sm['pct']:.1f}%_{sm['traders']}t")
    elif sm["pct"] >= 5:
        score += 1
        reasons.append(f"SM_aligned_{sm['pct']:.1f}%_{sm['traders']}t")

    if sm["cc_15m"] > 2.0:
        score += 2
        reasons.append(f"SM_15m_strong_+{sm['cc_15m']:.2f}")
    elif sm["cc_15m"] > 0.5:
        score += 1
        reasons.append(f"SM_15m_+{sm['cc_15m']:.2f}")

    if sm["cc_15m"] > 0 and sm["cc_1h"] > 0 and sm["cc_15m"] > sm["cc_1h"]:
        score += 1
        reasons.append(f"SM_ACCELERATING_15m({sm['cc_15m']:.2f})>1h({sm['cc_1h']:.2f})")

    if sm["traders"] >= 100:
        score += 1
        reasons.append(f"SM_DEEP_{sm['traders']}t")

    if direction == "LONG" and funding < 0:
        score += 2
        reasons.append(f"funding_pays_longs_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0:
        score += 2
        reasons.append(f"funding_pays_shorts_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.005) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append(f"funding_crowded_{funding:+.4f}")

    oi_vel = asset_data.get("oi_velocity")
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

    btc_mom_15m, btc_mom_1h = get_btc_correlation()
    if btc_mom_15m is not None and btc_mom_1h is not None:
        btc_agrees = (direction == "LONG" and btc_mom_15m > 0 and btc_mom_1h > 0) or \
                     (direction == "SHORT" and btc_mom_15m < 0 and btc_mom_1h < 0)
        if btc_agrees:
            score += 1
            reasons.append(f"btc_confirms_{btc_mom_1h:+.2f}%")

    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    if abs(mom_4h) > 1.0:
        score += 1
        reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

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
        "oi_change_1h": oi_change,
        "btc_mom_15m": btc_mom_15m,
        "btc_mom_1h": btc_mom_1h,
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
            "polar-config.json (preferred) or POLAR_WALLET_ADDRESS env var",
            file=sys.stderr,
        )
        return False

    if "ETH" in {h.upper() for h in held_assets}:
        # Already holding ETH — runtime would reject anyway
        return False

    leverage, tier_label = get_leverage_tier(thesis["score"])
    sm = thesis["sm"]
    mom = thesis["mom"]

    payload = {
        "asset": ASSET,
        "direction": thesis["direction"],
        "score": thesis["score"] / 20.0,  # normalize 0-1 (theoretical max ~20)
        "signal_type": "POLAR_ETH_HYBRID",
        "data": {
            "score": thesis["score"],
            "tier": tier_label,
            "leverage": leverage,
            "reasons": thesis["reasons"],
            "smPct": sm["pct"],
            "smTraders": sm["traders"],
            "smCc15m": sm["cc_15m"],
            "smCc1h": sm["cc_1h"],
            "smCc4h": sm["cc_4h"],
            "trend4h": thesis["trend_4h"],
            "trendStrength4h": thesis["trend_strength_4h"],
            "rsi": thesis["rsi"],
            "funding": thesis["funding"],
            "oiChange1h": thesis.get("oi_change_1h"),
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
            print(f"INGEST_FAILED ETH: {result.stderr}", file=sys.stderr)
            return False
        response = json.loads(result.stdout) if result.stdout.strip() else {}
        if not response.get("ok", False):
            print(f"INGEST_REJECTED ETH: {response.get('error', {})}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"INGEST_EXCEPTION ETH: {e}", file=sys.stderr)
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
            "_polar_producer_version": VERSION,
        }))
        return

    try:
        # Build thesis
        thesis = build_eth_thesis()

        if thesis.get("blocked"):
            elapsed = time.time() - run_start
            print(json.dumps({
                "status": "ok",
                "heartbeat": "NO_REPLY",
                "note": f"BLOCKED: {thesis['reason']}",
                "elapsed_sec": round(elapsed, 2),
                "_polar_producer_version": VERSION,
            }))
            return

        # MIN_SCORE check
        min_score = get_min_score()
        if thesis["score"] < min_score:
            print(json.dumps({
                "status": "ok",
                "heartbeat": "NO_REPLY",
                "note": f"score_low {thesis['score']}/{min_score}",
                "direction": thesis["direction"],
                "reasons": thesis["reasons"],
                "_polar_producer_version": VERSION,
            }))
            return

        # FP-001 quiet hours (apex-score override)
        quiet, current_hour, apex_bypass = in_quiet_hours()
        if quiet and thesis["score"] < apex_bypass:
            print(json.dumps({
                "status": "ok",
                "heartbeat": "NO_REPLY",
                "note": f"QUIET_HOURS hour={current_hour}_UTC score={thesis['score']}_below_apex_{apex_bypass}",
                "direction": thesis["direction"],
                "_polar_producer_version": VERSION,
            }))
            return

        # Check held positions — runtime would reject duplicate ETH anyway
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
            "_polar_producer_version": VERSION,
        }))
    finally:
        release_lock(lock)


if __name__ == "__main__":
    main()
