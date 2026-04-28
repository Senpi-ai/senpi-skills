#!/usr/bin/env python3
# Senpi KODIAK Producer v6.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""KODIAK v6.0 Producer — SOL Alpha Hunter (v2-runtime-native + 5x lev fix).

Migrates Kodiak from v5.1 scanner architecture (script calls
create_position directly) to v2-runtime-native producer pattern. The
ONLY thesis-side change: drop default leverage from 10x to 5x. v5.1's
build_sol_thesis logic (4h structure + 1h confirm + 15m direction +
base-tech-score floor + scoring) is preserved verbatim.

Architecture:
  1. This producer (cron, 3min): emits scored thesis signals only.
  2. Runtime (senpi-trading-runtime v2): receives signals via
     external_scanner ingest, LLM-gates them (pass-through), executes
     with FEE_OPTIMIZED_LIMIT (entries: maker-only, no taker; exits:
     maker-first + taker fallback), and manages DSL exits autonomously.

Why 5x not 10x: v5.1 ran -17.8% / -$178 over 26 trades on 10x SOL.
Single-asset SOL chop at 10x amplified per-trade variance to a
loss-biased distribution. At 5x same signal selection, half the
per-trade variance — winners and losers both halve, but losers
fall under the new tighter Phase 1 max_loss 15% (= 3% price stop)
WAY before they could compound to a wallet-eroding hit.

NO execution code. NO DSL code. NO position-tracking. Daily-loss /
drawdown / max-positions / consec-loss / per-asset cooldown all
enforced by the runtime's declarative guard_rails.

v5.1 entry logic preserved:
  - SOL only
  - 4h trend structure required (not NEUTRAL)
  - trend_strength_4h >= 0.75 (4 of 5 candles, not 3 of 5)
  - 1h trend matches 4h
  - 15m momentum confirms direction
  - Base-tech-score floor: strong_15m OR aligned_5m
  - MIN_SCORE 10 (composite: 4h trend + 1h confirm + SM + funding +
    OI + BTC correlation + RSI room + 4h momentum)
  - 240min per-asset cooldown

Environment variables:
  SENPI_API_KEY     — for MCP access
  KODIAK_WALLET     — Kodiak v6 wallet (must match runtime YAML).
                      AGENT-SPECIFIC env var by design — do NOT fall
                      back to a generic STRATEGY_ADDRESS. Per Turbine
                      v2.0.9 contamination fix.
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  OPENCLAW_BIN      — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME — optional override (default "kodiak_signals")
  KODIAK_MARGIN_PCT — optional, default 0.20 (20% of account value)
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
import kodiak_config as cfg


# v6.0: Agent-specific wallet env var. NO fallback to STRATEGY_ADDRESS
# (Turbine v2.0.9 contamination fix).
KODIAK_WALLET = os.environ.get("KODIAK_WALLET", "")
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "kodiak_signals")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
MARGIN_PCT = float(os.environ.get("KODIAK_MARGIN_PCT", "0.20"))

ASSET = "SOL"  # Single-asset focus


def _wallet_state_dir():
    if KODIAK_WALLET:
        h = hashlib.sha256(KODIAK_WALLET.lower().encode()).hexdigest()[:12]
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
# HARDCODED CONSTANTS (v5.1 base-tech-floor preserved)
# ═══════════════════════════════════════════════════════════════
MIN_SCORE = 10
MIN_MOM_15M_PCT = 0.1
MIN_TREND_STRENGTH_4H = 0.75    # v5.1: was 0.60 implicit; raised to 0.75 (4 of 5)
RSI_LONG_MAX = 72
RSI_SHORT_MIN = 28
ASSET_COOLDOWN_MINUTES = 240

# v6.0 conviction-tiered leverage. Was {17:10, 15:7, 14:5} on Polar /
# {15-20x default} on Kodiak v5.1. Drop to 5x default with 7x apex.
LEVERAGE_TIERS = [
    {"min_score": 13, "leverage": 7, "label": "apex"},
    {"min_score": 11, "leverage": 6, "label": "conviction"},
    {"min_score": 10, "leverage": 5, "label": "standard"},
]
DEFAULT_LEVERAGE = 5


# ═══════════════════════════════════════════════════════════════
# UTILITIES (preserved from v5.1)
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


def candle_volume(c):
    return safe_float(c.get("volume", c.get("v", c.get("vlm", 0))))


def price_momentum(candles, n_bars=1):
    """Return % change over the last n_bars."""
    if len(candles) < n_bars + 1:
        return 0
    cur = candle_close(candles[-1])
    prev = candle_close(candles[-1 - n_bars])
    if prev <= 0:
        return 0
    return (cur - prev) / prev * 100


def trend_structure(candles, lookback=6):
    """Return (label, strength) where strength is fraction of higher-lows
    (BULLISH) or lower-highs (BEARISH). v5.1 requires >= 0.75 for entry."""
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


def volume_ratio(candles, lookback=10):
    if len(candles) < lookback + 1:
        return 1.0
    vols = [candle_volume(c) for c in candles[-(lookback+1):-1]]
    if not vols or sum(vols) == 0:
        return 1.0
    avg = sum(vols) / len(vols)
    return candle_volume(candles[-1]) / avg if avg > 0 else 1.0


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
# ACCOUNT VALUE (for sizing)
# ═══════════════════════════════════════════════════════════════

def get_account_value():
    if not KODIAK_WALLET:
        return None, None
    ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                            strategy_wallet=KODIAK_WALLET)
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
# MCP DATA FETCHERS (preserved from v5.1)
# ═══════════════════════════════════════════════════════════════

def get_sol_full_picture():
    """Fetch SOL data across timeframes + funding + OI."""
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
    """BTC 1h momentum for cross-asset confirmation."""
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


def get_sol_sm_signal():
    """SM positioning for SOL: returns (direction, pct, traders, cc_15m) or None."""
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
    sol_cc_15m = 0.0
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
            sol_cc_15m = cc_15m_val
        elif direction == "short":
            asset_short_pct = pct
            asset_traders += traders
            sol_cc_15m = cc_15m_val

    if not found:
        return None

    total = asset_long_pct + asset_short_pct
    if total == 0:
        return {"direction": "NEUTRAL", "pct": 50, "traders": asset_traders, "cc_15m": sol_cc_15m}
    long_ratio = (asset_long_pct / total) * 100 if total > 0 else 50
    if long_ratio > 58:
        return {"direction": "LONG", "pct": long_ratio, "traders": asset_traders, "cc_15m": sol_cc_15m}
    elif long_ratio < 42:
        return {"direction": "SHORT", "pct": 100 - long_ratio, "traders": asset_traders, "cc_15m": sol_cc_15m}
    return {"direction": "NEUTRAL", "pct": 50, "traders": asset_traders, "cc_15m": sol_cc_15m}


# ═══════════════════════════════════════════════════════════════
# THESIS BUILDER (preserved verbatim from v5.1)
# ═══════════════════════════════════════════════════════════════

def build_sol_thesis():
    """v5.1 logic preserved. Returns thesis dict or {"blocked": True, "reason": ...}."""
    sol_data = get_sol_full_picture()
    if not sol_data:
        return {"blocked": True, "reason": "no_sol_data"}

    candles_5m = sol_data.get("candles", {}).get("5m", [])
    candles_15m = sol_data.get("candles", {}).get("15m", [])
    candles_1h = sol_data.get("candles", {}).get("1h", [])
    candles_4h = sol_data.get("candles", {}).get("4h", [])
    asset_ctx = sol_data.get("asset_context", {}) or {}
    funding = safe_float(asset_ctx.get("funding", 0))
    oi = safe_float(asset_ctx.get("openInterest", 0))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return {"blocked": True, "reason": "insufficient_candles"}

    price = candle_close(candles_5m[-1])

    # ── 4h structure ──
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return {"blocked": True, "reason": "4h_NEUTRAL"}
    if trend_strength_4h < MIN_TREND_STRENGTH_4H:
        return {"blocked": True, "reason": f"4h_weak_{trend_strength_4h:.0%}"}

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ── 1h confirmation ──
    trend_1h, trend_strength_1h = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return {"blocked": True, "reason": f"1h_{trend_1h}_vs_4h_{trend_4h}"}

    # ── 15m momentum ──
    mom_5m = price_momentum(candles_5m, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)

    if direction == "LONG" and mom_15m < MIN_MOM_15M_PCT:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}
    if direction == "SHORT" and mom_15m > -MIN_MOM_15M_PCT:
        return {"blocked": True, "reason": f"15m_too_weak_{mom_15m:+.2f}"}

    # ── Base-tech-score floor (v5.1) ──
    strong_15m = abs(mom_15m) > MIN_MOM_15M_PCT * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return {"blocked": True,
                "reason": f"base_tech_weak_15m({mom_15m:+.2f})_5m({mom_5m:+.2f})"}

    # ── RSI gate ──
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

    # SM concentration
    sm = get_sol_sm_signal()
    sm_aligned = False
    if sm and sm["direction"] == direction:
        sm_aligned = True
        if sm["pct"] >= 70 and sm["traders"] >= 50:
            score += 3
            reasons.append(f"SM_DOMINANT_{sm['pct']:.1f}%_{sm['traders']}t")
        elif sm["pct"] >= 60 and sm["traders"] >= 30:
            score += 2
            reasons.append(f"SM_STRONG_{sm['pct']:.1f}%_{sm['traders']}t")
        else:
            score += 1
            reasons.append(f"SM_aligned_{sm['pct']:.1f}%_{sm['traders']}t")

        # SM 15m acceleration
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

    # Move exhaustion penalty
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
        "sm": sm if sm else {},
        "sm_aligned": sm_aligned,
        "reasons": reasons,
    }


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_payload(thesis, leverage, margin_usd):
    """All declared scanner fields go in `data`, NOT `meta`. (Turbine v2.0.11.)"""
    sm = thesis.get("sm", {}) or {}
    return {
        "address": KODIAK_WALLET,
        "scannerId": SCANNER_NAME,
        "signalType": "KODIAK_SOL_THESIS",
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
            "_kodiak_producer_version": "6.0.0",
        },
    }


def push_signal(payload):
    if not KODIAK_WALLET:
        cfg.log("KODIAK_WALLET env var not set; cannot push signal")
        return False

    cmd = [
        OPENCLAW_BIN, "senpi", "external-scanner", "ingest",
        "--address", KODIAK_WALLET,
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

    if not KODIAK_WALLET:
        cfg.output({
            "status": "error",
            "error": "KODIAK_WALLET env var not set. Set it to the Kodiak strategy wallet (must match runtime.yaml).",
            "_kodiak_producer_version": "6.0.0",
        })
        return

    lock = acquire_lock()
    if lock is None:
        print(json.dumps({
            "status": "skip",
            "reason": "previous run still active — cron reentrancy guard",
            "_kodiak_producer_version": "6.0.0",
        }))
        return

    try:
        # 1. Read account value for sizing
        account_value, pos_count = get_account_value()
        if account_value is None or account_value <= 0:
            cfg.output({
                "status": "ok",
                "note": "cannot read account value; skip tick",
                "_kodiak_producer_version": "6.0.0",
            })
            return

        # 2. Per-asset cooldown (defense-in-depth alongside runtime guard)
        if is_asset_cooled_down(ASSET):
            cfg.output({
                "status": "ok",
                "note": f"{ASSET} in 240min cooldown",
                "_kodiak_producer_version": "6.0.0",
            })
            return

        # 3. Build thesis (v5.1 logic preserved)
        thesis = build_sol_thesis()

        if thesis.get("blocked"):
            cfg.output({
                "status": "ok",
                "note": f"thesis_blocked: {thesis['reason']}",
                "_kodiak_producer_version": "6.0.0",
            })
            return

        # 4. MIN_SCORE gate
        if thesis["score"] < MIN_SCORE:
            cfg.output({
                "status": "ok",
                "note": f"score_below_min: {thesis['score']} < {MIN_SCORE}",
                "thesis_direction": thesis["direction"],
                "reasons_preview": thesis["reasons"][:3],
                "_kodiak_producer_version": "6.0.0",
            })
            return

        # 5. Compute sizing + leverage
        leverage, lev_label = get_leverage_for_score(thesis["score"])
        margin_usd = round(account_value * MARGIN_PCT, 2)

        # 6. Emit
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
            "_kodiak_producer_version": "6.0.0",
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
