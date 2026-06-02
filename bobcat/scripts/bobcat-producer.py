#!/usr/bin/env python3
# Senpi BOBCAT Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""BOBCAT v1.0.0 — Big Tech equity perp trend follower.

Universe: NVDA, TSLA, AAPL, META, MSFT, GOOGL, AMZN, AMD, MU, INTC,
TSM, ORCL on Hyperliquid XYZ. LONG OR SHORT on 4h trend + Smart Money
direction. Standard DSL — Phase 1 15% max_loss, Phase 2 standard ladder.
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bobcat_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "bobcat_signals"
SIGNAL_TYPE = "BOBCAT_BIGTECH"

DEFAULT_UNIVERSE = [
    "xyz:NVDA", "xyz:TSLA", "xyz:AAPL", "xyz:META", "xyz:MSFT",
    "xyz:GOOGL", "xyz:AMZN", "xyz:AMD", "xyz:MU", "xyz:INTC",
    "xyz:TSM", "xyz:ORCL",
]
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 5
DEFAULT_MIN_SCORE = 5
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70


def _resolve_wallet():
    env_val = (os.environ.get("BOBCAT_WALLET") or "").strip()
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


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_f(c, "low", "l") for c in candles[-lookback:]]
    highs = [_f(c, "high", "h") for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


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
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


def build_thesis(asset, entry_cfg):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    if len(candles_4h) < 6 or len(candles_1h) < 6:
        return None

    t4, s4 = trend_structure(candles_4h)
    t1, _ = trend_structure(candles_1h)
    if t4 == "NEUTRAL":
        return None

    direction = "LONG" if t4 == "BULLISH" else "SHORT"

    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(entry_cfg.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    score = 3  # 4h trend (gate-confirmed)
    reasons = [f"4h_{t4.lower()}_{s4:.0%}"]
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 2
        reasons.append(f"1h_confirms_{t1.lower()}")
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")

    return {
        "coin": asset, "direction": direction, "score": score, "reasons": reasons,
        "trend_4h": t4, "trend_4h_strength": s4, "trend_1h": t1,
        "sm_direction": sm_dir, "sm_tilt_pct": sm_tilt,
    }


def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        return False
    coin = thesis["coin"]
    if coin.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": thesis["score"], "leverage": leverage, "marginUsd": margin_usd,
        "direction": thesis["direction"], "reasons": thesis["reasons"],
        "trend4h": thesis["trend_4h"], "trend4hStrength": thesis["trend_4h_strength"],
        "trend1h": thesis["trend_1h"],
        "smDirection": thesis["sm_direction"], "smTiltPct": thesis["sm_tilt_pct"],
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS, scanner=SCANNER_NAME,
            asset=coin, direction=thesis["direction"],
            score=min(thesis["score"] / 9.0, 1.0),
            signal_type=SIGNAL_TYPE, data=data_block,
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
    universe = config.get("universe", DEFAULT_UNIVERSE)

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_bobcat_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_bobcat_producer_version": VERSION})
        return

    candidates = []
    for asset in universe:
        if asset.upper() in held_set:
            continue
        if cfg.was_recently_signaled(asset):
            continue
        thesis = build_thesis(asset, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no big-tech setup with 4h trend + SM agreement",
            "universe": universe,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_bobcat_producer_version": VERSION,
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
            "coin": best["coin"], "direction": best["direction"],
            "score": best["score"], "leverage": leverage, "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_bobcat_producer_version": VERSION,
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
        name=f"bobcat-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
