#!/usr/bin/env python3
# Senpi EGRET Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""EGRET v1.0.0 — Smart-Money Divergence Fader.

When the Smart-Money crowd is extremely concentrated one direction but
price is NOT confirming — it has stalled or rolled over against the
crowd — the crowded side is exhausted and the unwind is the edge. Egret
fades it.

FADE SHORT when: SM is >= crowdingMinPct LONG (crowded longs) AND price
has stalled/fallen over the recent window (divergence) AND RSI is
stretched (overbought). FADE LONG on the mirror. Universe: BTC/ETH/SOL/HYPE.

This is a mean-reversion / unwind play — the move is BOUNDED (the crowd
covers, price snaps back, done). So:
  - DSL is TIGHT (T0 +5% / lock 30 — bank the snapback fast; a fader
    holding for a +50% trend would be violating its own thesis).
  - Entry is MAKER-ONLY (ensure_execution_as_taker: false) — a fade is
    non-urgent and fee-sensitive; if the maker doesn't fill, the trade
    wasn't ripe. Same class as the live faders Owl / Pangolin / Bald-eagle.

Producer NEVER closes — DSL owns exits.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import egret_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "egret_signals"
SIGNAL_TYPE = "EGRET_SM_FADE"

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 5

DEFAULT_CROWDING_MIN_PCT = 70.0     # SM concentration must be at least this to be "crowded"
DEFAULT_CROWDING_EXTREME_PCT = 80.0
DEFAULT_CONFIRM_MAX_PCT = 0.5       # if price moved less than this WITH the crowd over the window, it's diverging
DEFAULT_DIVERGENCE_LOOKBACK = 4     # 1h bars for the price-confirmation window


def _resolve_wallet():
    env_val = (os.environ.get("EGRET_WALLET") or "").strip()
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
# Technical helpers
# ═══════════════════════════════════════════════════════════════

def price_momentum(candles, n_bars):
    if len(candles) < n_bars + 1:
        return 0.0
    old = _f(candles[-(n_bars + 1)], "close", "c")
    new = _f(candles[-1], "close", "c")
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_market_data(asset):
    return cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h"],
        include_funding=False,
        include_order_book=False,
    )


def fetch_sm_direction(asset):
    """Return (direction, concentration_pct). direction = the crowded side;
    concentration_pct = how concentrated (e.g. 78 = 78% of top traders are
    on that side)."""
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


# ═══════════════════════════════════════════════════════════════
# Thesis builder — fade the exhausted crowd
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, entry_cfg):
    # GATE 1 — extreme SM crowding
    sm_dir, sm_pct = fetch_sm_direction(asset)
    if sm_dir not in ("LONG", "SHORT"):
        return None
    crowd_min = float(entry_cfg.get("crowdingMinPct", DEFAULT_CROWDING_MIN_PCT))
    crowd_extreme = float(entry_cfg.get("crowdingExtremePct", DEFAULT_CROWDING_EXTREME_PCT))
    if sm_pct < crowd_min:
        return None

    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    if len(candles_1h) < 16:
        return None
    closes = [_f(c, "close", "c") for c in candles_1h]

    lookback = int(entry_cfg.get("divergenceLookbackHours", DEFAULT_DIVERGENCE_LOOKBACK))
    confirm_max = float(entry_cfg.get("confirmMaxPct", DEFAULT_CONFIRM_MAX_PCT))
    mom = price_momentum(candles_1h, lookback)
    rsi = calc_rsi(closes)

    # GATE 2 — price NOT confirming the crowd (divergence)
    # Crowded LONG but price hasn't risen (mom <= confirm_max) → exhaustion → FADE SHORT.
    # Crowded SHORT but price hasn't fallen (mom >= -confirm_max) → FADE LONG.
    if sm_dir == "LONG":
        if mom > confirm_max:
            return None  # crowd long AND price rising — no divergence, don't fade a working trend
        fade_direction = "SHORT"
    else:  # SHORT
        if mom < -confirm_max:
            return None
        fade_direction = "LONG"

    score = 0
    reasons = []

    # SM crowding (gate-confirmed) + ultra-crowded bonus
    score += 2
    reasons.append(f"sm_crowded_{sm_dir.lower()}_{sm_pct:.0f}%")
    if sm_pct >= crowd_extreme:
        score += 1
        reasons.append("sm_ultra_crowded")

    # Divergence magnitude — the further price is from confirming, the stronger
    if (sm_dir == "LONG" and mom <= -0.5) or (sm_dir == "SHORT" and mom >= 0.5):
        score += 3
        reasons.append(f"price_diverging_{mom:+.2f}%")
    else:
        score += 2
        reasons.append(f"price_stalled_{mom:+.2f}%")

    # RSI exhaustion confirms the fade
    if fade_direction == "SHORT" and rsi >= 68:
        score += 2
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    elif fade_direction == "LONG" and rsi <= 32:
        score += 2
        reasons.append(f"rsi_oversold_{rsi:.0f}")
    elif (fade_direction == "SHORT" and rsi >= 60) or (fade_direction == "LONG" and rsi <= 40):
        score += 1
        reasons.append(f"rsi_stretched_{rsi:.0f}")

    return {
        "coin": asset,
        "direction": fade_direction,
        "score": score,
        "reasons": reasons,
        "sm_crowd_direction": sm_dir,
        "sm_crowd_pct": sm_pct,
        "price_momentum_pct": round(mom, 3),
        "rsi": round(rsi, 1),
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
        "smCrowdDirection": thesis["sm_crowd_direction"],
        "smCrowdPct": thesis.get("sm_crowd_pct") or 0.0,
        "priceMomentumPct": thesis.get("price_momentum_pct") or 0.0,
        "rsi": thesis.get("rsi") or 0.0,
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


# ═══════════════════════════════════════════════════════════════
# MAIN — multi-asset whitelist scan
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    universe = [a.upper() for a in config.get("universe", DEFAULT_UNIVERSE)]

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_egret_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_egret_producer_version": VERSION})
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
            "note": "WAITING — no exhausted-crowd divergence on universe",
            "universe": universe,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_egret_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.15))
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
        "_egret_producer_version": VERSION,
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
        name=f"egret-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
