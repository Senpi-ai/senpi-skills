#!/usr/bin/env python3
# Senpi SALAMANDER Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SALAMANDER v1.0.0 — Pullback catcher.

LONG when: 4h trend is BULLISH AND 1h price pulled back 3-7% from
the recent high AND Smart Money is > 55% long.
SHORT when: 4h trend is BEARISH AND 1h price rallied 3-7% from the
recent low AND SM is > 55% short.

"Buying the dip" or "shorting the rally" within an established trend.
Pullbacks shallower than 3% are noise; deeper than 7% suggest the
trend is breaking. Sweet spot: 3-7%.

Asymmetric DSL:
  Phase 1 max_loss 10% (wider — pullbacks need room to develop)
  Phase 2 first tier +5% / lock 30% (tight — when thesis works, lock fast)
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import salamander_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "salamander_signals"
SIGNAL_TYPE = "SALAMANDER_PULLBACK"

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL"]
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 5
DEFAULT_MIN_SCORE = 5
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70
DEFAULT_PULLBACK_MIN = 3.0
DEFAULT_PULLBACK_MAX = 7.0
DEFAULT_PULLBACK_LOOKBACK = 24


def _resolve_wallet():
    env_val = (os.environ.get("SALAMANDER_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def _f(c, primary, alt=None, default=0):
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def trend_4h(candles_4h, lookback=6):
    if len(candles_4h) < lookback:
        return "NEUTRAL"
    lows = [_f(c, "low", "l") for c in candles_4h[-lookback:]]
    highs = [_f(c, "high", "h") for c in candles_4h[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH"
    if lower_highs >= total * 0.6:
        return "BEARISH"
    return "NEUTRAL"


def detect_pullback(candles_1h, lookback_hours, min_pct, max_pct, trend):
    """Returns (direction, pullback_pct) when a pullback within the trend
    falls in the [min_pct, max_pct] band, else (None, 0).

    For BULLISH trend: looks at how far the latest close is BELOW the
    recent high within lookback. If 3-7%, it's a LONG pullback.
    For BEARISH trend: looks at how far latest close is ABOVE the recent
    low. If 3-7%, it's a SHORT rally.
    """
    if len(candles_1h) < lookback_hours + 1 or trend == "NEUTRAL":
        return None, 0.0
    window = candles_1h[-lookback_hours:]
    closes = [_f(c, "close", "c") for c in window]
    if not closes or closes[-1] <= 0:
        return None, 0.0
    if trend == "BULLISH":
        recent_high = max(closes[:-1])
        if recent_high <= 0:
            return None, 0.0
        pullback_pct = ((recent_high - closes[-1]) / recent_high) * 100
        if min_pct <= pullback_pct <= max_pct:
            return "LONG", pullback_pct
    else:  # BEARISH
        recent_low = min(closes[:-1])
        if recent_low <= 0:
            return None, 0.0
        rally_pct = ((closes[-1] - recent_low) / recent_low) * 100
        if min_pct <= rally_pct <= max_pct:
            return "SHORT", rally_pct
    return None, 0.0


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
    long_pct, short_pct, found = 0.0, 0.0, False
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


def build_thesis(asset, entry_cfg):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    lookback = int(entry_cfg.get("pullbackLookbackHours", DEFAULT_PULLBACK_LOOKBACK))
    if len(candles_1h) < lookback + 1 or len(candles_4h) < 6:
        return None

    t4 = trend_4h(candles_4h)
    if t4 == "NEUTRAL":
        return None

    direction, pullback_pct = detect_pullback(
        candles_1h, lookback,
        float(entry_cfg.get("pullbackMinPct", DEFAULT_PULLBACK_MIN)),
        float(entry_cfg.get("pullbackMaxPct", DEFAULT_PULLBACK_MAX)),
        t4,
    )
    if direction is None:
        return None

    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(entry_cfg.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    score = 0
    reasons = []

    # 4h trend aligned (gate-confirmed, this is the foundation)
    score += 3
    reasons.append(f"4h_{t4.lower()}_trend")

    # Pullback in sweet spot 3-7%
    score += 2
    reasons.append(f"pullback_{pullback_pct:+.2f}%")

    # Sweet spot midpoint bonus (4-6% is ideal — too shallow = noise, too deep = trend break)
    if 4.0 <= pullback_pct <= 6.0:
        score += 1
        reasons.append("pullback_in_midpoint")

    # SM aligned
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")

    # SM strongly tilted
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h": t4,
        "pullback_pct": round(pullback_pct, 3),
        "sm_direction": sm_dir,
        "sm_tilt_pct": sm_tilt,
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
        "trend4h": thesis["trend_4h"],
        "pullbackPct": thesis["pullback_pct"],
        "smDirection": thesis["sm_direction"],
        "smTiltPct": thesis["sm_tilt_pct"],
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
        cfg.output({"status": "error", "reason": "no_wallet", "_salamander_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_salamander_producer_version": VERSION})
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
            "note": "WAITING — no pullback in trend with SM agreement on universe",
            "universe": universe,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_salamander_producer_version": VERSION,
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
        "_salamander_producer_version": VERSION,
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
        name=f"salamander-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
