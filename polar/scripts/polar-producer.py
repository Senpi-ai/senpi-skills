#!/usr/bin/env python3
# Senpi POLAR Producer v2.5.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""POLAR v2.5.0 Producer — ETH Alpha Hunter (v2-runtime-native).

Migrates Polar from v3.x scanner architecture (script calls
create_position directly) to v2-runtime-native producer pattern.

Naming: v2.5 (not v3.x). v3.0's diagnostic was correct — v2.4's 32%
WR + 'fee drag dominated' was the problem — but v3.0's MIN_SCORE 14
+ over-strict structural veto produced too few entries (16 trades /
10 days / -$72). v2.5 keeps v3.0's structural veto (4h NEUTRAL block,
trend_strength_4h ≥ 0.75, 1h matches 4h, 15m direction confirms,
base-tech floor) but lowers MIN_SCORE to 12 (between v2.4's 10 and
v3.0's 14), and adds the v6 Kodiak asymmetry-optimized DSL.

Architecture:
  1. This producer (cron, 3min): emits scored thesis signals only.
  2. Runtime (senpi-trading-runtime v2): receives signals via
     external_scanner ingest, LLM-gates them (pass-through), executes
     with FEE_OPTIMIZED_LIMIT (entries: maker-only, no taker; exits:
     maker-first + taker fallback), and manages DSL exits autonomously.

Asymmetry math: at 5x leverage with max_loss 15% (= 3% price stop),
per-loser cost is ~$15 + $0.40 maker fee. Per Phase 2 tier 3+ winner
is $30-100+. 32% WR profitable when asymmetry holds.

NO execution code. NO DSL code. NO position-tracking. Daily-loss /
drawdown / max-positions / consec-loss / per-asset cooldown all
enforced by the runtime's declarative guard_rails.

v3.0 entry logic preserved (with MIN_SCORE 14 → 12 relaxation):
  - ETH only
  - 4h trend structure required (not NEUTRAL)
  - trend_strength_4h >= 0.75 (4 of 5 candles, not 3 of 5)
  - structural direction MUST match SM direction (the v3.0 anti-chop gate)
  - 1h trend matches 4h
  - 15m momentum confirms direction
  - Base-tech-score floor: strong_15m OR aligned_5m
  - RSI gate: 74 cap on long, 26 floor on short
  - MIN_SCORE 12 (composite: trend + 1h confirm + SM + funding + OI + BTC + RSI)
  - 240min per-asset cooldown

Environment variables:
  SENPI_API_KEY     — for MCP access
  POLAR_WALLET      — Polar wallet (must match runtime YAML).
                      AGENT-SPECIFIC env var by design — NO fallback
                      to STRATEGY_ADDRESS (Turbine v2.0.9 fix).
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  OPENCLAW_BIN      — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME — optional override (default "polar_signals")
  POLAR_MARGIN_PCT  — optional, default 0.25 (25% of account value)
"""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polar_config as cfg


POLAR_WALLET = os.environ.get("POLAR_WALLET", "")
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "polar_signals")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
MARGIN_PCT = float(os.environ.get("POLAR_MARGIN_PCT", "0.25"))

ASSET = "ETH"


def _wallet_state_dir():
    if POLAR_WALLET:
        h = hashlib.sha256(POLAR_WALLET.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_LOCK_PATH = _STATE_DIR / "producer.lock"
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS (v3.0 preserved with MIN_SCORE relaxation)
# ═══════════════════════════════════════════════════════════════
MIN_SCORE = 12                     # v2.5: was 14 (v3.0) — between v2.4's 10 and v3.0's 14
MIN_MOM_15M_PCT = 0.1
MIN_TREND_STRENGTH_4H = 0.75       # v3.0 / v5.1 Kodiak pattern — 4 of 5 candles
MIN_SM_CONCENTRATION = 5.0         # v3.0 — pct_of_top_traders_gain >= 5%
MIN_SM_TRADER_COUNT = 30
MIN_SM_ACCEL_PCT = 0.3
RSI_LONG_MAX = 74
RSI_SHORT_MIN = 26
ASSET_COOLDOWN_MINUTES = 240

# v2.5 conviction-tiered leverage (preserves v3.0's tiers).
LEVERAGE_TIERS = [
    {"min_score": 17, "leverage": 10, "label": "apex"},
    {"min_score": 15, "leverage": 7,  "label": "conviction"},
    {"min_score": 12, "leverage": 5,  "label": "standard"},
]
DEFAULT_LEVERAGE = 5


# ═══════════════════════════════════════════════════════════════
# UTILITIES (preserved from v3.0)
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
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
    cur = candle_close(candles[-1])
    prev = candle_close(candles[-1 - n_bars])
    if prev <= 0:
        return 0
    return (cur - prev) / prev * 100


def trend_structure(candles, lookback=6):
    """Return (label, strength). v3.0/v5.1 require >= 0.75 for entry."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    recent = candles[-lookback:]
    highs = [candle_high(c) for c in recent]
    lows = [candle_low(c) for c in recent]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] >= lows[i-1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] <= highs[i-1])
    bullish = higher_lows / (lookback - 1)
    bearish = lower_highs / (lookback - 1)
    if bullish >= 0.6:
        return "BULLISH", bullish
    if bearish >= 0.6:
        return "BEARISH", bearish
    return "NEUTRAL", max(bullish, bearish)


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = 0, 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d > 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def time_of_day_modifier():
    hour = datetime.now(timezone.utc).hour
    if 4 <= hour < 14:
        return 1, "tod_active_window"
    elif hour >= 18 or hour < 2:
        return -2, "tod_chop_zone"
    return 0, None


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"], tier["label"]
    return DEFAULT_LEVERAGE, "standard"


# ═══════════════════════════════════════════════════════════════
# STATE I/O
# ═══════════════════════════════════════════════════════════════

def load_cooldowns():
    try:
        with open(_COOLDOWN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cooldowns(cooldowns):
    cfg.atomic_write(str(_COOLDOWN_FILE), cooldowns)


def is_asset_cooled_down(asset, cooldown_minutes=ASSET_COOLDOWN_MINUTES):
    cooldowns = load_cooldowns()
    if asset not in cooldowns:
        return False
    last_emit = cooldowns[asset].get("emittedTimestamp", 0)
    return ((time.time() - last_emit) / 60) < cooldown_minutes


def mark_asset_emitted(asset):
    cooldowns = load_cooldowns()
    cooldowns[asset] = {
        "emittedTimestamp": time.time(),
        "setAt": cfg.now_iso(),
    }
    save_cooldowns(cooldowns)


# ═══════════════════════════════════════════════════════════════
# ACCOUNT VALUE
# ═══════════════════════════════════════════════════════════════

def get_account_value():
    if not POLAR_WALLET:
        return None, None
    ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                            strategy_wallet=POLAR_WALLET)
    if not ch:
        return None, None
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    total_value = 0.0
    pos_count = 0
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        total_value += safe_float(ms.get("accountValue", 0))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if safe_float(pos.get("szi", 0)) != 0:
                pos_count += 1
    return total_value, pos_count


# ═══════════════════════════════════════════════════════════════
# MCP DATA FETCHERS (preserved from v3.0)
# ═══════════════════════════════════════════════════════════════

def get_eth_full_picture():
    """Fetch ETH data across timeframes + funding + OI."""
    raw = cfg.mcporter_call(
        "market_get_asset_data",
        asset=ASSET,
        candle_intervals=["5m", "15m", "1h", "4h"],
        include_funding=True,
        include_order_book=False,
    )
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    return data if isinstance(data, dict) else None


def get_btc_correlation():
    raw = cfg.mcporter_call(
        "market_get_asset_data",
        asset="BTC",
        candle_intervals=["1h"],
        include_funding=False,
        include_order_book=False,
    )
    if not raw:
        return 0
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    candles = data.get("candles", {}).get("1h", []) if isinstance(data, dict) else []
    if len(candles) < 2:
        return 0
    return price_momentum(candles, 1)


def get_eth_sm_signal():
    """SM positioning for ETH."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    if isinstance(data, dict):
        markets = data.get("markets", data)
    else:
        markets = data
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None

    asset_long_pct = 0.0
    asset_short_pct = 0.0
    asset_traders = 0
    eth_cc_15m = 0.0
    found = False

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        if token != ASSET:
            continue
        found = True
        direction = str(m.get("direction", "")).lower()
        pct = safe_float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders = int(m.get("trader_count", m.get("traderCount", 0)))
        cc_15m_val = safe_float(m.get("contribution_pct_change_15m", 0))
        if direction == "long":
            asset_long_pct = pct
            asset_traders += traders
            eth_cc_15m = cc_15m_val
        elif direction == "short":
            asset_short_pct = pct
            asset_traders += traders
            eth_cc_15m = cc_15m_val

    if not found:
        return None

    total = asset_long_pct + asset_short_pct
    if total == 0:
        return {"direction": "NEUTRAL", "pct": 50, "traders": asset_traders, "cc_15m": eth_cc_15m}
    long_ratio = (asset_long_pct / total) * 100 if total > 0 else 50
    if long_ratio > 58:
        return {"direction": "LONG", "pct": long_ratio, "traders": asset_traders, "cc_15m": eth_cc_15m}
    elif long_ratio < 42:
        return {"direction": "SHORT", "pct": 100 - long_ratio, "traders": asset_traders, "cc_15m": eth_cc_15m}
    return {"direction": "NEUTRAL", "pct": 50, "traders": asset_traders, "cc_15m": eth_cc_15m}


# ═══════════════════════════════════════════════════════════════
# THESIS BUILDER (v3.0 logic preserved with MIN_SCORE 12)
# ═══════════════════════════════════════════════════════════════

def build_eth_thesis():
    """v3.0 hyperfeed + structural-veto logic preserved.
    Returns thesis dict or {"blocked": True, "reason": ...}."""

    # ── HYPERFEED PRIMARY DIRECTION ──
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

    # ── STRUCTURAL DATA ──
    eth_data = get_eth_full_picture()
    if not eth_data:
        return {"blocked": True, "reason": "no_asset_data"}

    candles_5m = eth_data.get("candles", {}).get("5m", [])
    candles_15m = eth_data.get("candles", {}).get("15m", [])
    candles_1h = eth_data.get("candles", {}).get("1h", [])
    candles_4h = eth_data.get("candles", {}).get("4h", [])
    asset_ctx = eth_data.get("asset_context", {}) or {}
    funding = safe_float(asset_ctx.get("funding", 0))
    oi = safe_float(asset_ctx.get("openInterest", 0))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return {"blocked": True, "reason": "insufficient_candles"}

    price = candle_close(candles_5m[-1])

    # ── STRUCTURAL GATES (v3.0 anti-chop) ──
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return {"blocked": True, "reason": f"4h_NEUTRAL_(sm_said_{direction})"}
    if trend_strength_4h < MIN_TREND_STRENGTH_4H:
        return {"blocked": True, "reason": f"4h_weak_{trend_strength_4h:.0%}"}

    # THE KEY V3.0 GATE — structural direction must match SM direction
    structural_dir = "LONG" if trend_4h == "BULLISH" else "SHORT"
    if structural_dir != direction:
        return {"blocked": True,
                "reason": f"direction_conflict_sm_{direction}_vs_4h_{structural_dir}"}

    trend_1h, trend_strength_1h = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return {"blocked": True, "reason": f"1h_{trend_1h}_vs_4h_{trend_4h}"}

    # ── MOMENTUM GATES ──
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)

    if direction == "LONG" and mom_15m < MIN_MOM_15M_PCT:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}
    if direction == "SHORT" and mom_15m > -MIN_MOM_15M_PCT:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}

    # ── BASE-TECH FLOOR (v3.0 / v5.1 Kodiak pattern) ──
    strong_15m = abs(mom_15m) > MIN_MOM_15M_PCT * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return {"blocked": True,
                "reason": f"base_tech_weak_15m({mom_15m:+.2f})_5m({mom_5m:+.2f})"}

    # ── RSI GATE ──
    closes_1h = [candle_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > RSI_LONG_MAX:
        return {"blocked": True, "reason": f"rsi_overbought_{rsi:.0f}"}
    if direction == "SHORT" and rsi < RSI_SHORT_MIN:
        return {"blocked": True, "reason": f"rsi_oversold_{rsi:.0f}"}

    # ── ALL GATES PASSED — SCORE THE SIGNAL ──
    score = 0
    reasons = []

    # Base tech (5 pts core)
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{trend_strength_4h:.0%}")
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")
    if strong_15m:
        score += 1
        reasons.append(f"15m_strong_{mom_15m:+.2f}%")
    if aligned_5m and strong_15m:
        score += 1
        reasons.append("4TF_aligned")

    # SM concentration (already passed gates so it's aligned)
    if sm["pct"] >= 70 and sm["traders"] >= 50:
        score += 3
        reasons.append(f"SM_DOMINANT_{sm['pct']:.1f}%_{sm['traders']}t")
    elif sm["pct"] >= 60 and sm["traders"] >= 30:
        score += 2
        reasons.append(f"SM_STRONG_{sm['pct']:.1f}%_{sm['traders']}t")
    else:
        score += 1
        reasons.append(f"SM_aligned_{sm['pct']:.1f}%_{sm['traders']}t")

    # SM acceleration
    if sm["cc_15m"] > 0.5:
        score += 2
        reasons.append(f"SM_15m_strong_+{sm['cc_15m']:.2f}")
    elif sm["cc_15m"] > 0.2:
        score += 1
        reasons.append(f"SM_15m_+{sm['cc_15m']:.2f}")

    if sm["traders"] >= 80:
        score += 1
        reasons.append(f"SM_DEEP_{sm['traders']}t")

    # Funding
    if direction == "LONG" and funding < -0.0001:
        score += 2
        reasons.append(f"funding_pays_longs_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0.0001:
        score += 2
        reasons.append(f"funding_pays_shorts_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.0005) or (direction == "SHORT" and funding < -0.0005):
        score -= 1
        reasons.append(f"funding_crowded_{funding:+.4f}")

    # BTC correlation
    btc_mom_1h = get_btc_correlation()
    if (direction == "LONG" and btc_mom_1h > 0.3) or (direction == "SHORT" and btc_mom_1h < -0.3):
        score += 1
        reasons.append(f"btc_confirms_{btc_mom_1h:+.2f}%")

    # RSI room
    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4h momentum bonus / exhaustion penalty
    if (direction == "LONG" and mom_4h > 1.0) or (direction == "SHORT" and mom_4h < -1.0):
        score += 1
        reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

    if direction == "LONG" and mom_4h > 5.0:
        score -= 2
        reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif direction == "SHORT" and mom_4h < -5.0:
        score -= 2
        reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif direction == "LONG" and mom_4h > 3.0:
        score -= 1
        reasons.append(f"MOVE_TIRING_{mom_4h:+.1f}%")
    elif direction == "SHORT" and mom_4h < -3.0:
        score -= 1
        reasons.append(f"MOVE_TIRING_{mom_4h:+.1f}%")

    # Time-of-day
    tod_mod, tod_reason = time_of_day_modifier()
    score += tod_mod
    if tod_reason:
        reasons.append(tod_reason)

    return {
        "asset": ASSET,
        "direction": direction,
        "score": round(score, 2),
        "price": price,
        "trend_4h": trend_4h,
        "trend_strength_4h": round(trend_strength_4h, 3),
        "trend_1h": trend_1h,
        "mom_15m": round(mom_15m, 3),
        "mom_1h": round(mom_1h, 3),
        "mom_4h": round(mom_4h, 3),
        "funding": funding,
        "oi": oi,
        "rsi": round(rsi, 1),
        "btc_mom_1h": round(btc_mom_1h, 3),
        "sm": sm,
        "sm_aligned": True,        # by definition: gates passed
        "reasons": reasons,
    }


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_payload(thesis, leverage, margin_usd):
    """All declared scanner fields go in `data`, NOT `meta`. (Turbine v2.0.11.)"""
    sm = thesis.get("sm", {}) or {}
    return {
        "address": POLAR_WALLET,
        "scannerId": SCANNER_NAME,
        "signalType": "POLAR_ETH_THESIS",
        "asset": thesis["asset"],
        "direction": thesis["direction"],
        "score": float(thesis["score"]),
        "timestamp": int(time.time() * 1000),
        "factors": {},
        "data": {
            "score": thesis["score"],
            "leverage": leverage,
            "marginUsd": margin_usd,
            "smPctOfTopTraders": float(sm.get("pct", 0)),
            "smTraderCount": int(sm.get("traders", 0)),
            "smCc15m": float(sm.get("cc_15m", 0)),
            "smAligned": bool(thesis["sm_aligned"]),
            "trend4h": thesis["trend_4h"],
            "trendStrength4h": thesis["trend_strength_4h"],
            "trend1h": thesis["trend_1h"],
            "mom15mPct": thesis["mom_15m"],
            "mom1hPct": thesis["mom_1h"],
            "mom4hPct": thesis["mom_4h"],
            "fundingRate": float(thesis["funding"]),
            "oiTrend": "rising" if thesis["oi"] > 0 else "unknown",
            "btcMom1hPct": thesis["btc_mom_1h"],
            "rsi": thesis["rsi"],
            "reasons": " | ".join(thesis.get("reasons", [])),
        },
        "meta": {
            "_polar_producer_version": "2.5.0",
        },
    }


def push_signal(payload):
    if not POLAR_WALLET:
        cfg.log("POLAR_WALLET env var not set; cannot push signal")
        return False

    cmd = [
        OPENCLAW_BIN, "senpi", "external-scanner", "ingest",
        "--address", POLAR_WALLET,
        "--scanner", SCANNER_NAME,
        "--payload", json.dumps(payload),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            cfg.log(f"ingest failed for {payload['asset']} {payload['direction']}: {r.stderr.strip()}")
            return False
        if r.stdout.strip():
            try:
                response = json.loads(r.stdout)
                if isinstance(response, dict) and response.get("ok") is False:
                    cfg.log(f"ingest rejected: {response.get('error')}")
                    return False
            except (json.JSONDecodeError, TypeError):
                pass
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        cfg.log(f"ingest exception for {payload['asset']}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    if not POLAR_WALLET:
        cfg.output({
            "status": "error",
            "error": "POLAR_WALLET env var not set. Set it to the Polar strategy wallet (must match runtime.yaml).",
            "_polar_producer_version": "2.5.0",
        })
        return

    lock = acquire_lock()
    if lock is None:
        print(json.dumps({
            "status": "skip",
            "reason": "previous run still active — cron reentrancy guard",
            "_polar_producer_version": "2.5.0",
        }))
        return

    try:
        account_value, pos_count = get_account_value()
        if account_value is None or account_value <= 0:
            cfg.output({
                "status": "ok",
                "note": "cannot read account value; skip tick",
                "_polar_producer_version": "2.5.0",
            })
            return

        if is_asset_cooled_down(ASSET):
            cfg.output({
                "status": "ok",
                "note": f"{ASSET} in 240min cooldown",
                "_polar_producer_version": "2.5.0",
            })
            return

        thesis = build_eth_thesis()

        if thesis.get("blocked"):
            cfg.output({
                "status": "ok",
                "note": f"thesis_blocked: {thesis['reason']}",
                "_polar_producer_version": "2.5.0",
            })
            return

        if thesis["score"] < MIN_SCORE:
            cfg.output({
                "status": "ok",
                "note": f"score_below_min: {thesis['score']} < {MIN_SCORE}",
                "thesis_direction": thesis["direction"],
                "reasons_preview": thesis["reasons"][:3],
                "_polar_producer_version": "2.5.0",
            })
            return

        leverage, lev_label = get_leverage_for_score(thesis["score"])
        margin_usd = round(account_value * MARGIN_PCT, 2)

        payload = build_signal_payload(thesis, leverage, margin_usd)
        pushed = 1 if push_signal(payload) else 0
        if pushed:
            mark_asset_emitted(ASSET)

        elapsed = time.time() - run_start
        warn = "WARN_OVER_180S" if elapsed > 180 else None
        cfg.output({
            "status": "ok",
            "asset": ASSET,
            "direction": thesis["direction"],
            "score": thesis["score"],
            "leverage": leverage,
            "lev_label": lev_label,
            "margin_usd": margin_usd,
            "signals_pushed": pushed,
            "account_value": round(account_value, 2),
            "open_positions": pos_count,
            "elapsed_sec": round(elapsed, 2),
            "warn": warn,
            "_polar_producer_version": "2.5.0",
        })
    finally:
        release_lock(lock)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
