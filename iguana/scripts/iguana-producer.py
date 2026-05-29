#!/usr/bin/env python3
# Senpi IGUANA Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""IGUANA v1.0.0 — XYZ Macro Index Trend.

"I want stock-market exposure on Hyperliquid without picking stocks."

Iguana trend-follows the broad XYZ indices `xyz:SP500` + `xyz:XYZ100`. No
stock-picking, no commodities, no pre-IPO. Two assets, one decision per tick.

Closest thing to an index-fund equivalent — but 24/7. ONBOARDING TIER.

Architecture (helpers-native): producer ticks every 300s, computes a 4h trend
strength per index, picks the stronger if both align (or the lone strong one),
and emits IGUANA_INDEX_TREND in the trend's direction. LLM gate is pass-through.

DSL: balanced + 48h hard_timeout (XYZ weekend pricing-gap risk).
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import iguana_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "iguana_signals"
SIGNAL_TYPE = "IGUANA_INDEX_TREND"

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3
DEFAULT_MIN_SCORE = 4

DEFAULT_WHITELIST = ["xyz:SP500", "xyz:XYZ100"]
DEFAULT_TREND_LOOKBACK = 24    # 24 × 4h bars = 4 days
DEFAULT_MIN_TREND_PCT = 1.5    # minimum |4-day move| to call it a trend
DEFAULT_STRONG_TREND_PCT = 4.0


def _resolve_wallet():
    env_val = (os.environ.get("IGUANA_WALLET") or "").strip()
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
# Pure index-trend logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def trend_strength(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or the reference price is non-positive."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def trend_direction(strength, min_pct):
    """Direction implied by trend strength. None if magnitude below threshold."""
    if strength is None or abs(strength) < min_pct:
        return None
    return "LONG" if strength > 0 else "SHORT"


def pick_strongest_trend(per_asset_strength, min_pct):
    """Among {asset: strength}, return the asset with the highest |strength|
    above min_pct. Returns (asset, strength) or None."""
    best, best_mag = None, -1.0
    for asset, strength in per_asset_strength.items():
        if strength is None:
            continue
        mag = abs(strength)
        if mag < min_pct:
            continue
        if mag > best_mag:
            best_mag, best = mag, (asset, strength)
    return best


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


def volume_trend(candles, lookback=6):
    if len(candles) < lookback:
        return 0.0
    vols = [_f(c, "volume", "v") for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ═══════════════════════════════════════════════════════════════
# Thesis builder
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, strength, candles, config):
    min_pct = float(config.get("minTrendPct", DEFAULT_MIN_TREND_PCT))
    strong_pct = float(config.get("strongTrendPct", DEFAULT_STRONG_TREND_PCT))

    direction = trend_direction(strength, min_pct)
    if direction is None:
        return None

    vol = volume_trend(candles)

    score = 3   # base — trend strength above min
    reasons = [f"{asset}_4d_trend_{strength:+.1f}%"]
    if abs(strength) >= strong_pct:
        score += 2
        reasons.append(f"trend_strong_{strength:+.1f}%")
    if vol > 15:
        score += 1
        reasons.append(f"vol_rising_{vol:+.0f}%")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_pct": round(strength, 2),
        "volume_trend_pct": round(vol, 2),
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
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "trendPct": thesis.get("trend_pct") or 0.0,
        "volumeTrendPct": thesis.get("volume_trend_pct") or 0.0,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
            score=min(thesis["score"] / 6.0, 1.0),
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
    lookback = int(config.get("trendLookbackBars", DEFAULT_TREND_LOOKBACK))

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_iguana_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_iguana_producer_version": VERSION})
        return

    candles_by_asset = {}
    strength_by_asset = {}
    for asset in whitelist:
        if asset.upper() in held_set or cfg.was_recently_signaled(asset):
            continue
        candles = fetch_candles(asset)
        if len(candles) <= lookback:
            continue
        closes = [_f(c, "close", "c") for c in candles]
        candles_by_asset[asset] = candles
        strength_by_asset[asset] = trend_strength(closes, lookback)

    min_pct = float(config.get("minTrendPct", DEFAULT_MIN_TREND_PCT))
    picked = pick_strongest_trend(strength_by_asset, min_pct)
    if picked is None:
        cfg.output({
            "status": "ok",
            "note": f"WAITING — neither index has a 4d move past {min_pct}%",
            "whitelist": whitelist,
            "strength": {k: round(v, 2) if v is not None else None for k, v in strength_by_asset.items()},
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_iguana_producer_version": VERSION,
        })
        return

    asset, strength = picked
    thesis = build_thesis(asset, strength, candles_by_asset[asset], config)
    if thesis is None or thesis["score"] < int(config.get("minScore", DEFAULT_MIN_SCORE)):
        cfg.output({
            "status": "ok",
            "note": "WAITING — pick cleared trend gate but missed minScore",
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_iguana_producer_version": VERSION,
        })
        return

    margin_pct = float(config.get("marginPct", 0.20))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(thesis, margin_usd, leverage, held_assets)
    if pushed:
        cfg.record_signal(thesis["coin"])

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": thesis["coin"],
            "direction": thesis["direction"],
            "trend_pct": thesis["trend_pct"],
            "score": thesis["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": thesis["reasons"][:5],
        },
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_iguana_producer_version": VERSION,
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
        name=f"iguana-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
