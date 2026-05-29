#!/usr/bin/env python3
# Senpi SHEEP Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SHEEP v1.0.0 — Long-Only Triple-EMA-Stacked Trend.

"Buy crypto only when it's clearly going up across every timeframe."

Sheep fires LONG only when the fast EMA is above the slow EMA on ALL THREE
timeframes (15m, 1h, 4h) for an asset in the configured whitelist. When the
stack breaks on any timeframe, the entry signal stops. Never shorts —
removing the "what's a short?" cognitive load is the whole point.

A triple-timeframe EMA stack is a visual rule a beginner can sanity-check on
any chart: each higher timeframe's fast line should be above its slow line,
all three simultaneously. ONBOARDING TIER.

Architecture: helpers-native producer + balanced DSL. Stateless scoring +
race-window dedup. Producer NEVER closes — DSL owns exits.

REQUIRES USER-SCOPE AUTH for leaderboard_get_markets (SM bonus).
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheep_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "sheep_signals"
SIGNAL_TYPE = "SHEEP_TRIPLE_EMA_LONG"

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3
DEFAULT_MIN_SCORE = 4
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70

DEFAULT_WHITELIST = ["BTC", "ETH", "SOL", "HYPE"]
DEFAULT_EMA_FAST = 9
DEFAULT_EMA_SLOW = 21
DEFAULT_MIN_STACKED_FRAMES = 3   # require ALL three timeframes


def _resolve_wallet():
    env_val = (os.environ.get("SHEEP_WALLET") or "").strip()
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
# Pure EMA + stack logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def ema(closes, period):
    """Exponential moving average. Returns the final EMA value, or None for
    insufficient data."""
    if not closes or len(closes) < period:
        return None
    k = 2.0 / (period + 1.0)
    # Seed with the SMA of the first `period` closes — the classic init.
    seed = sum(closes[:period]) / period
    ema_val = seed
    for c in closes[period:]:
        ema_val = (c - ema_val) * k + ema_val
    return ema_val


def is_stacked_bullish(closes, fast_period, slow_period):
    """True if fast EMA > slow EMA on this single timeframe. Stacked-bullish
    on one timeframe alone is not the full Sheep signal — combine across
    timeframes via stack_score()."""
    f = ema(closes, fast_period)
    s = ema(closes, slow_period)
    if f is None or s is None:
        return False
    return f > s


def stack_score(stacks_per_timeframe):
    """Given a list of per-timeframe stack booleans (e.g. [True, True, True]
    for 15m+1h+4h all stacked-bullish), return how many are stacked. Sheep
    requires this >= minStackedFrames to fire (default 3 = all)."""
    return sum(1 for s in stacks_per_timeframe if s)


def fast_slow_spread(closes, fast_period, slow_period):
    """% spread of fast EMA above slow EMA on this timeframe. Negative when
    fast is below slow. None for insufficient data."""
    f = ema(closes, fast_period)
    s = ema(closes, slow_period)
    if f is None or s is None or s <= 0:
        return None
    return ((f - s) / s) * 100.0


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_market_data(asset):
    return cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["15m", "1h", "4h"],
        include_funding=False,
        include_order_book=False,
    )


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
# Thesis builder — one asset
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, config):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles = data.get("data", {}).get("candles", {})
    closes_15m = [_f(c, "close", "c") for c in candles.get("15m", [])]
    closes_1h = [_f(c, "close", "c") for c in candles.get("1h", [])]
    closes_4h = [_f(c, "close", "c") for c in candles.get("4h", [])]

    fast = int(config.get("emaFast", DEFAULT_EMA_FAST))
    slow = int(config.get("emaSlow", DEFAULT_EMA_SLOW))
    min_stacked = int(config.get("minStackedFrames", DEFAULT_MIN_STACKED_FRAMES))

    stacks = [
        is_stacked_bullish(closes_15m, fast, slow),
        is_stacked_bullish(closes_1h, fast, slow),
        is_stacked_bullish(closes_4h, fast, slow),
    ]
    score = stack_score(stacks)
    if score < min_stacked:
        return None

    # Spread tier — wider 4h spread = stronger trend
    spread_4h = fast_slow_spread(closes_4h, fast, slow) or 0.0
    spread_1h = fast_slow_spread(closes_1h, fast, slow) or 0.0

    # SM is a bonus, not a gate
    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(config.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(config.get("smStrongTiltPct", DEFAULT_SM_STRONG))

    s = 3   # base for full triple-stack
    reasons = [f"{asset}_stack_15m_1h_4h", f"spread_4h_{spread_4h:+.2f}%"]
    if spread_4h >= 1.0:
        s += 1
        reasons.append("trend_strong_4h")
    if spread_1h >= 0.5:
        s += 1
        reasons.append("trend_strong_1h")
    if sm_dir == "LONG" and sm_tilt >= sm_min:
        s += 1
        reasons.append(f"sm_long_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            s += 1
            reasons.append("sm_strong")

    return {
        "coin": asset,
        "direction": "LONG",
        "score": s,
        "reasons": reasons,
        "spread_4h_pct": round(spread_4h, 2),
        "spread_1h_pct": round(spread_1h, 2),
        "stack_score": score,
        "sm_direction": sm_dir if sm_dir else "NONE",
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
        "spread4hPct": thesis.get("spread_4h_pct") or 0.0,
        "spread1hPct": thesis.get("spread_1h_pct") or 0.0,
        "stackScore": thesis.get("stack_score") or 0,
        "smDirection": thesis.get("sm_direction") or "NONE",
        "smTiltPct": thesis.get("sm_tilt_pct") or 0.0,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction="LONG",
            score=min(thesis["score"] / 8.0, 1.0),
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
# MAIN — iterate whitelist, find the strongest triple-stack LONG
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    whitelist = config.get("whitelist", DEFAULT_WHITELIST)

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_sheep_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_sheep_producer_version": VERSION})
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
            "note": "WAITING — no whitelisted asset has a full 15m+1h+4h EMA-stacked-bullish setup",
            "whitelist": whitelist,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_sheep_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: (c["score"], c["spread_4h_pct"]), reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.20))
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
            "stack_score": best["stack_score"],
            "spread_4h_pct": best["spread_4h_pct"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_sheep_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"sheep-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
