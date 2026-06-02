#!/usr/bin/env python3
# Senpi MARLIN Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""MARLIN v1.0.0 — Order-Book Imbalance Momentum.

A persistent resting-depth imbalance in the L2 order book is a near-term
pressure tell: bids ≫ asks over the top levels = accumulation / buy
pressure; asks ≫ bids = distribution / sell pressure. Marlin enters in the
imbalance direction WHEN short-term price momentum and Smart Money agree.

This is NOT a per-tick scalper. The order-book imbalance is the entry-TIMING
edge on a momentum thesis — Marlin holds the position with a wide "let
winners run" DSL (a 24h hard_timeout caps the short-horizon thesis). Trading
the book as a scalp would just bleed fees; we time the entry with it and
let the move work.

LONG when: book is bid-heavy (imbalance >= imbalanceMin) AND 15m momentum is
up AND SM net long. SHORT on the mirror. Universe: BTC/ETH/SOL/HYPE.

Producer NEVER closes — DSL owns exits.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import marlin_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "marlin_signals"
SIGNAL_TYPE = "MARLIN_BOOK_IMBALANCE"

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 5

DEFAULT_LEVELS_N = 10               # top N book levels per side to sum
DEFAULT_IMBALANCE_MIN = 1.5         # bid/ask depth ratio to call it bid-heavy (1/1.5 = ask-heavy)
DEFAULT_IMBALANCE_STRONG = 2.5      # strongly-imbalanced bonus
DEFAULT_MOM_MIN_PCT = 0.1          # 15m momentum magnitude that confirms the side
DEFAULT_SM_TILT_MIN = 55


def _resolve_wallet():
    env_val = (os.environ.get("MARLIN_WALLET") or "").strip()
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
# Microstructure helpers (pure — unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def book_imbalance(asset_data, levels_n=DEFAULT_LEVELS_N):
    """Return (imbalance_ratio, bid_depth, ask_depth) over the top `levels_n`
    book levels per side. ratio > 1 = bid-heavy (buy pressure); < 1 =
    ask-heavy (sell pressure). ratio is None when the book is unusable.

    market_get_asset_data order_book shape: {"levels": [bids, asks]} where
    each side is a list of {"px","sz","n"}; bids[0] is best bid, asks[0] best ask.
    """
    ob = (asset_data.get("data", {}) or {}).get("order_book", {}) or {}
    levels = ob.get("levels") or []
    if not isinstance(levels, list) or len(levels) < 2:
        return None, 0.0, 0.0
    bids = levels[0][:levels_n] if isinstance(levels[0], list) else []
    asks = levels[1][:levels_n] if isinstance(levels[1], list) else []
    bid_depth = sum(_f(l, "sz") for l in bids if isinstance(l, dict))
    ask_depth = sum(_f(l, "sz") for l in asks if isinstance(l, dict))
    if bid_depth <= 0 and ask_depth <= 0:
        return None, bid_depth, ask_depth
    if ask_depth <= 0:
        return float("inf"), bid_depth, ask_depth
    return bid_depth / ask_depth, bid_depth, ask_depth


def imbalance_direction(ratio, imbalance_min):
    """Map an imbalance ratio to a trade side, or None if too balanced.
    bid-heavy (ratio >= min) → LONG; ask-heavy (ratio <= 1/min) → SHORT."""
    if ratio is None or imbalance_min <= 0:
        return None
    if ratio >= imbalance_min:
        return "LONG"
    if ratio <= (1.0 / imbalance_min):
        return "SHORT"
    return None


def price_move_pct(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0.0
    old = _f(candles[-(n_bars + 1)], "close", "c")
    new = _f(candles[-1], "close", "c")
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100


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
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_market_data(asset):
    return cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["5m", "15m"],
        include_funding=False,
        include_order_book=True,
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
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


# ═══════════════════════════════════════════════════════════════
# Thesis builder
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, entry_cfg):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_5m = data.get("data", {}).get("candles", {}).get("5m", [])
    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    if len(candles_15m) < 2 or len(candles_5m) < 2:
        return None

    levels_n = int(entry_cfg.get("levelsN", DEFAULT_LEVELS_N))
    imb_min = float(entry_cfg.get("imbalanceMin", DEFAULT_IMBALANCE_MIN))
    imb_strong = float(entry_cfg.get("imbalanceStrong", DEFAULT_IMBALANCE_STRONG))

    # GATE 1 — order-book imbalance picks the side
    ratio, bid_depth, ask_depth = book_imbalance(data, levels_n)
    direction = imbalance_direction(ratio, imb_min)
    if direction is None:
        return None

    # GATE 2 — short-term momentum must confirm the imbalance side
    mom_15m = price_move_pct(candles_15m, 1)
    mom_min = float(entry_cfg.get("momMinPct", DEFAULT_MOM_MIN_PCT))
    if direction == "LONG" and mom_15m < mom_min:
        return None
    if direction == "SHORT" and mom_15m > -mom_min:
        return None

    # GATE 3 — Smart-Money agreement
    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    mom_5m = price_move_pct(candles_5m, 1)
    vol_pct = volume_trend(candles_5m)
    # display ratio (inf → large sentinel for telemetry)
    disp_ratio = 99.0 if ratio == float("inf") else round(ratio, 3)

    score = 0
    reasons = []

    # Imbalance magnitude (gate-confirmed) + strong bonus
    score += 2
    reasons.append(f"book_imbalance_{disp_ratio}x")
    strong = (ratio == float("inf")) or (direction == "LONG" and ratio >= imb_strong) or \
             (direction == "SHORT" and ratio <= (1.0 / imb_strong))
    if strong:
        score += 1
        reasons.append("imbalance_strong")

    # Momentum confirms (gate) + 5m alignment
    score += 2
    reasons.append(f"mom_15m_{mom_15m:+.2f}%")
    if (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0):
        score += 1
        reasons.append(f"mom_5m_aligned_{mom_5m:+.2f}%")

    # SM aligned (gate) + strong
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
    if sm_tilt >= 70:
        score += 1
        reasons.append("sm_strongly_tilted")

    # Volume rising
    if vol_pct > 10:
        score += 1
        reasons.append(f"vol_rising_{vol_pct:+.0f}%")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "imbalance_ratio": disp_ratio,
        "bid_depth": round(bid_depth, 2),
        "ask_depth": round(ask_depth, 2),
        "mom_15m_pct": round(mom_15m, 3),
        "mom_5m_pct": round(mom_5m, 3),
        "sm_direction": sm_dir,
        "sm_tilt_pct": sm_tilt,
        "volume_trend_pct": round(vol_pct, 2),
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
        "imbalanceRatio": thesis.get("imbalance_ratio") or 0.0,
        "bidDepth": thesis.get("bid_depth") or 0.0,
        "askDepth": thesis.get("ask_depth") or 0.0,
        "mom15mPct": thesis.get("mom_15m_pct") or 0.0,
        "mom5mPct": thesis.get("mom_5m_pct") or 0.0,
        "smDirection": thesis["sm_direction"],
        "smTiltPct": thesis.get("sm_tilt_pct") or 0.0,
        "volumeTrendPct": thesis.get("volume_trend_pct") or 0.0,
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
        cfg.output({"status": "error", "reason": "no_wallet", "_marlin_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_marlin_producer_version": VERSION})
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
            "note": "WAITING — no book-imbalance + momentum + SM alignment on universe",
            "universe": universe,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_marlin_producer_version": VERSION,
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
        "_marlin_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=180,
        name=f"marlin-producer-{_wallet_lock_id}",
        tick_timeout=150,
    )
