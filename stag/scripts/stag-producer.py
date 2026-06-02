#!/usr/bin/env python3
# Senpi STAG Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""STAG v1.0.0 — Parabolic-Run Hunter.

Reference setup: HYPE 2026-05, $40 → $65 over 16 days. Standard DSL trails
would have chopped a position out on the 5-8% intraday gyrations between
May 17-25. Stag is the entry-side pair for the `parabolic_runner` DSL preset
— enter only when there's a real parabolic-runner setup, then let the wide
DSL hold it through the gyrations.

ALL FIVE gates required:

  1. Structural trend: close > 200-bar 4h SMA AND 7d high made within last 48h
  2. Strength:        7d move >= 25% (defines "parabolic" vs normal trend)
  3. Volume:          recent 24h volume >= 1.5x trailing 7d average
  4. Acceleration:    4d move >= 7d move / 2 (recent half at least as fast)
  5. SM aligned:      Smart Money LONG >= 60%

LONG only — parabolic crashes happen too fast for momentum-style shorts.

Architecture: helpers-native producer + parabolic_runner DSL preset. Tick
600s — these setups don't appear in 5 minutes. Per-asset cooldown 24h — don't
re-enter the same asset after a bad take. Producer NEVER closes — DSL owns
exits.

REQUIRES USER-SCOPE AUTH for leaderboard_get_markets.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stag_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "stag_signals"
SIGNAL_TYPE = "STAG_PARABOLIC_RUNNER"

MAX_LEVERAGE = 5            # parabolic_runner DSL accepts 25% max_loss → at 5x that's 5% price stop
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 5

DEFAULT_WHITELIST = ["BTC", "ETH", "SOL", "HYPE"]

# Lookbacks (4h bars):
DEFAULT_TREND_LOOKBACK = 42        # 7 days @ 4h
DEFAULT_ACCEL_LOOKBACK = 24        # 4 days @ 4h
DEFAULT_FRESH_HIGH_BARS = 12       # 48h — 7d high must be within last 48h
DEFAULT_SMA_PERIOD = 200           # structural trend filter (~33 days @ 4h)

DEFAULT_MIN_TREND_PCT = 25.0       # the "parabolic" threshold
DEFAULT_STRONG_TREND_PCT = 40.0

# Volume:
DEFAULT_VOLUME_RECENT_BARS = 6     # 24h
DEFAULT_VOLUME_BASELINE_BARS = 42  # 7d (same as trend lookback for symmetry)
DEFAULT_VOLUME_SURGE_RATIO = 1.5

DEFAULT_SM_TILT_MIN = 60.0


def _resolve_wallet():
    env_val = (os.environ.get("STAG_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def _f(c, primary, alt=None, default=0.0):
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Pure parabolic-detection logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def pct_change(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or reference price is non-positive."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def sma(closes, period):
    """Simple moving average over the last `period` closes. None if
    insufficient data."""
    if not closes or len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


def is_above_sma(closes, period):
    """True if the latest close is above the N-period SMA. False if
    SMA can't be computed."""
    s = sma(closes, period)
    if s is None or not closes:
        return False
    return closes[-1] > s


def recent_high_bars_ago(closes, lookback):
    """How many bars back the highest close in `closes[-lookback-1:]` was
    made. 0 = latest bar is the high. None if insufficient data."""
    if not closes or len(closes) <= lookback:
        return None
    window = closes[-(lookback + 1):]
    high_idx_in_window = max(range(len(window)), key=lambda i: window[i])
    # bars_ago = distance from the end of the window to the high
    return len(window) - 1 - high_idx_in_window


def volume_surge(volumes, recent_bars, baseline_bars, min_ratio):
    """True if mean(last `recent_bars`) / mean(last `baseline_bars`) >=
    min_ratio. Returns (passed, ratio) — ratio is None on insufficient data."""
    if not volumes or len(volumes) < baseline_bars:
        return False, None
    recent = volumes[-recent_bars:]
    baseline = volumes[-baseline_bars:]
    rmean = sum(recent) / len(recent) if recent else 0.0
    bmean = sum(baseline) / len(baseline) if baseline else 0.0
    if bmean <= 0:
        return False, None
    ratio = rmean / bmean
    return (ratio >= min_ratio), ratio


def is_accelerating(short_strength_pct, long_strength_pct):
    """True if the shorter window's move is at least half the longer
    window's move — i.e., the recent half is keeping pace or faster.
    Both must be positive (we're gating LONG-only on bullish acceleration)."""
    if short_strength_pct is None or long_strength_pct is None:
        return False
    if long_strength_pct <= 0:
        return False
    return short_strength_pct >= (long_strength_pct / 2.0)


def parabolic_score(trend_pct, accelerating, vol_passed, vol_ratio, sm_aligned, strong_trend_pct):
    """Composite score for a parabolic setup. Gate (caller-side) is all-5
    pass; this scores HOW strong the setup is among passing candidates."""
    score = 3   # base — all five gates passed
    reasons = [f"trend_{trend_pct:+.1f}%"]
    if trend_pct >= strong_trend_pct:
        score += 2
        reasons.append(f"strong_{trend_pct:+.1f}%")
    if accelerating:
        score += 1
        reasons.append("accelerating")
    if vol_passed and vol_ratio is not None:
        if vol_ratio >= 2.0:
            score += 1
            reasons.append(f"vol_surge_{vol_ratio:.1f}x")
        else:
            reasons.append(f"vol_{vol_ratio:.1f}x")
    if sm_aligned:
        reasons.append("sm_aligned")
    return score, reasons


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_candles(asset):
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["4h"],
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return []
    return data.get("data", {}).get("candles", {}).get("4h", [])


def fetch_sm_direction(asset):
    raw = cfg.mcp_call("leaderboard_get_markets")
    if not raw or not raw.get("success", True):
        return None, 0.0
    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != asset.upper():
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


# ═══════════════════════════════════════════════════════════════
# Thesis builder — one asset, all five gates
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, config):
    candles = fetch_candles(asset)
    sma_period = int(config.get("smaPeriod", DEFAULT_SMA_PERIOD))
    trend_lb = int(config.get("trendLookbackBars", DEFAULT_TREND_LOOKBACK))
    accel_lb = int(config.get("accelLookbackBars", DEFAULT_ACCEL_LOOKBACK))
    fresh_high_lb = int(config.get("freshHighBars", DEFAULT_FRESH_HIGH_BARS))

    # Need enough candles for the deepest lookback (SMA, by default 200)
    if len(candles) < max(sma_period, trend_lb) + 1:
        return None

    closes = [_f(c, "close", "c") for c in candles]
    volumes = [_f(c, "volume", "v") for c in candles]

    # Gate 1: structural trend — above 200-SMA AND 7d high within last 48h
    above_sma = is_above_sma(closes, sma_period)
    if not above_sma:
        return None
    high_bars_ago = recent_high_bars_ago(closes, trend_lb)
    if high_bars_ago is None or high_bars_ago > fresh_high_lb:
        return None

    # Gate 2: strength — 7d move >= minTrendPct
    trend_pct = pct_change(closes, trend_lb)
    min_trend = float(config.get("minTrendPct", DEFAULT_MIN_TREND_PCT))
    if trend_pct is None or trend_pct < min_trend:
        return None

    # Gate 3: volume surge
    vol_recent = int(config.get("volRecentBars", DEFAULT_VOLUME_RECENT_BARS))
    vol_base = int(config.get("volBaselineBars", DEFAULT_VOLUME_BASELINE_BARS))
    vol_min = float(config.get("volSurgeRatio", DEFAULT_VOLUME_SURGE_RATIO))
    vol_passed, vol_ratio = volume_surge(volumes, vol_recent, vol_base, vol_min)
    if not vol_passed:
        return None

    # Gate 4: acceleration — recent half at least as fast as full window
    short_strength = pct_change(closes, accel_lb)
    accelerating = is_accelerating(short_strength, trend_pct)
    if not accelerating:
        return None

    # Gate 5: SM aligned LONG >= threshold
    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(config.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_aligned = (sm_dir == "LONG" and sm_tilt >= sm_min)
    if not sm_aligned:
        return None

    # All 5 gates passed — score the setup
    strong_trend = float(config.get("strongTrendPct", DEFAULT_STRONG_TREND_PCT))
    score, reasons = parabolic_score(trend_pct, accelerating, vol_passed, vol_ratio, sm_aligned, strong_trend)

    return {
        "coin": asset,
        "direction": "LONG",
        "score": score,
        "reasons": reasons,
        "trend_pct": round(trend_pct, 2),
        "short_strength_pct": round(short_strength, 2) if short_strength is not None else 0.0,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else 0.0,
        "high_bars_ago": int(high_bars_ago),
        "sm_tilt_pct": sm_tilt,
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        return False
    coin = thesis["coin"]
    if coin.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": "LONG",
        "reasons": thesis["reasons"],
        "trendPct": thesis.get("trend_pct") or 0.0,
        "shortStrengthPct": thesis.get("short_strength_pct") or 0.0,
        "volRatio": thesis.get("vol_ratio") or 0.0,
        "highBarsAgo": thesis.get("high_bars_ago") or 0,
        "smTiltPct": thesis.get("sm_tilt_pct") or 0.0,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction="LONG",
            score=min(thesis["score"] / 7.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {coin}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {coin}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    whitelist = config.get("whitelist", DEFAULT_WHITELIST)

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_stag_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_stag_producer_version": VERSION})
        return

    candidates = []
    for asset in whitelist:
        a = str(asset).upper()
        if a in held_set or cfg.was_recently_signaled(a):
            continue
        thesis = build_thesis(a, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no asset cleared all five parabolic gates",
            "whitelist": whitelist,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_stag_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: (c["score"], c["trend_pct"]), reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.25))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(best, margin_usd, leverage, held_assets)
    if pushed:
        cfg.record_signal(best["coin"])

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["coin"],
            "direction": "LONG",
            "trend_pct": best["trend_pct"],
            "short_strength_pct": best["short_strength_pct"],
            "vol_ratio": best["vol_ratio"],
            "sm_tilt_pct": best["sm_tilt_pct"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:6],
        },
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_stag_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=600,   # 10min — parabolic conditions don't appear in 5min
        name=f"stag-producer-{_wallet_lock_id}",
        tick_timeout=240,
    )
