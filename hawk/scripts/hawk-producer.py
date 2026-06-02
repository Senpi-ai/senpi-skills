#!/usr/bin/env python3
# Senpi HAWK Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""HAWK v1.0.0 — 4h Breakout Buyer / Breakdown Seller.

LONG when price breaks above the 7-day high AND Smart Money is
> 55% long. SHORT when price breaks below the 7-day low AND Smart Money
is > 55% short. Universe: BTC, ETH, SOL.

Failed breakouts must be cut fast. DSL Phase 1 max_loss 8% with retrace
threshold 5. Phase 2 locks fast at +5% (lock_hw_pct: 30%) so a real
breakout that runs immediately starts banking ratchet-protected profit.

Producer ticks every 300s. Scoring is 5 components, max ~9.
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hawk_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "hawk_signals"
SIGNAL_TYPE = "HAWK_BREAKOUT"

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL"]
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 5
DEFAULT_MIN_SCORE = 5
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70
DEFAULT_BREAKOUT_LOOKBACK_HOURS = 168  # 7 days


def _resolve_wallet():
    env_val = (os.environ.get("HAWK_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Breakout detection
# ═══════════════════════════════════════════════════════════════

def _candle_field(c, primary, alt=None, default=0):
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def detect_breakout(candles_1h, lookback_hours):
    """Returns (direction, magnitude_pct) when price breaks above lookback
    high or below lookback low, else (None, 0).

    Breakout direction:
      "LONG" if latest close > max(prior closes within lookback)
      "SHORT" if latest close < min(prior closes within lookback)
      else None
    """
    if len(candles_1h) < lookback_hours + 1:
        return None, 0.0
    window = candles_1h[-lookback_hours:]
    latest = candles_1h[-1]
    latest_close = _candle_field(latest, "close", "c")
    prior_closes = [_candle_field(c, "close", "c") for c in window[:-1]]
    if not prior_closes or latest_close <= 0:
        return None, 0.0
    high = max(prior_closes)
    low = min(prior_closes)
    if latest_close > high:
        return "LONG", ((latest_close - high) / high) * 100
    if latest_close < low:
        return "SHORT", ((low - latest_close) / low) * 100
    return None, 0.0


def trend_4h(candles_4h, lookback=6):
    if len(candles_4h) < lookback:
        return "NEUTRAL"
    lows = [_candle_field(c, "low", "l") for c in candles_4h[-lookback:]]
    highs = [_candle_field(c, "high", "h") for c in candles_4h[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH"
    if lower_highs >= total * 0.6:
        return "BEARISH"
    return "NEUTRAL"


def volume_ratio(candles_1h):
    if len(candles_1h) < 10:
        return 1.0
    avg_prior = sum(_candle_field(c, "volume", "v") for c in candles_1h[-10:-1]) / 9
    latest_vol = _candle_field(candles_1h[-1], "volume", "v")
    if avg_prior <= 0:
        return 1.0
    return latest_vol / avg_prior


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_market_data(asset):
    return cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h", "4h"],
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

    long_pct = 0.0
    short_pct = 0.0
    found = False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != asset:
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
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


# ═══════════════════════════════════════════════════════════════
# Thesis builder
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, entry_cfg):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    lookback = int(entry_cfg.get("breakoutLookbackHours", DEFAULT_BREAKOUT_LOOKBACK_HOURS))
    if len(candles_1h) < lookback + 1 or len(candles_4h) < 6:
        return None

    direction, magnitude_pct = detect_breakout(candles_1h, lookback)
    if direction is None:
        return None

    # SM gate must agree with breakout direction
    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(entry_cfg.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    # 4h trend must align with direction (no fighting trend on breakouts)
    t4 = trend_4h(candles_4h)
    trend_aligned = (
        (direction == "LONG" and t4 == "BULLISH")
        or (direction == "SHORT" and t4 == "BEARISH")
    )

    vol_x = volume_ratio(candles_1h)

    score = 0
    reasons = []

    # Breakout magnitude (+3 / +2 / +1)
    if magnitude_pct >= 1.0:
        score += 3
        reasons.append(f"breakout_strong_{magnitude_pct:+.2f}%")
    elif magnitude_pct >= 0.3:
        score += 2
        reasons.append(f"breakout_{magnitude_pct:+.2f}%")
    else:
        score += 1
        reasons.append(f"breakout_weak_{magnitude_pct:+.2f}%")

    # SM aligned (gate-confirmed)
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")

    # SM strongly tilted
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")

    # 4h trend aligned
    if trend_aligned:
        score += 2
        reasons.append(f"4h_trend_aligned_{t4.lower()}")

    # Volume confirmation (>=1.5x average)
    if vol_x >= 1.5:
        score += 1
        reasons.append(f"vol_{vol_x:.1f}x")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "breakout_pct": round(magnitude_pct, 3),
        "sm_direction": sm_dir,
        "sm_tilt_pct": sm_tilt,
        "trend_4h": t4,
        "volume_ratio": round(vol_x, 2),
    }


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
        "breakoutPct": thesis["breakout_pct"],
        "smDirection": thesis["sm_direction"],
        "smTiltPct": thesis["sm_tilt_pct"],
        "trend4h": thesis["trend_4h"],
        "volumeRatio": thesis["volume_ratio"],
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
            score=min(thesis["score"] / 9.0, 1.0),
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


def main():
    run_start = time.time()
    config = cfg.load_config()
    universe = [a.upper() for a in config.get("universe", DEFAULT_UNIVERSE)]

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_hawk_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_hawk_producer_version": VERSION})
        return

    candidates = []
    for asset in universe:
        if asset in held_set:
            continue
        if cfg.was_recently_signaled(asset):
            continue
        thesis = build_thesis(asset, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no breakout with SM agreement on universe",
            "universe": universe,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_hawk_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: c["score"], reverse=True)
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
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_hawk_producer_version": VERSION,
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
        name=f"hawk-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
