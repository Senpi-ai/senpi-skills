#!/usr/bin/env python3
# Senpi CHAMELEON Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""CHAMELEON v1.0.0 — Relative-Value / Pairs (ratio mean-reversion).

When the price RATIO between two correlated assets extends far from its
recent mean (a high |z-score|), it tends to revert. Chameleon trades that
reversion — but the Senpi runtime is single-position, so instead of a
two-leg market-neutral spread it takes a directional bet on the **high-beta
leg** of the pair, in the direction that profits as the ratio snaps back.

Pairs (config): ETH/BTC (trade ETH), SOL/ETH (trade SOL), SOL/BTC (trade SOL).
  ratio = numerator/denominator.
  z high (numerator rich)  → ratio reverts down → SHORT the leg if leg==num,
                              LONG if leg==den.
  z low  (numerator cheap) → ratio reverts up   → LONG the leg if leg==num,
                              SHORT if leg==den.

Mean-reversion class → tight "bank the snapback" DSL + 48h hard_timeout +
weak_peak_cut (a reversion resolves quickly or the thesis failed). Producer
NEVER closes — DSL owns exits.
"""

import hashlib
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chameleon_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "chameleon_signals"
SIGNAL_TYPE = "CHAMELEON_RATIO_REVERSION"

# Each pair: numerator / denominator, and which leg we actually trade (high-beta).
DEFAULT_PAIRS = [
    {"numerator": "ETH", "denominator": "BTC", "leg": "ETH"},
    {"numerator": "SOL", "denominator": "ETH", "leg": "SOL"},
    {"numerator": "SOL", "denominator": "BTC", "leg": "SOL"},
]
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 4

DEFAULT_LOOKBACK_BARS = 48          # 1h bars (~2 days) for the ratio mean/std
DEFAULT_Z_ENTRY_MIN = 2.0           # |z| to consider the ratio extended
DEFAULT_Z_STRONG = 3.0
DEFAULT_SM_TILT_MIN = 55


def _resolve_wallet():
    env_val = (os.environ.get("CHAMELEON_WALLET") or "").strip()
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
# Pure relative-value math (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def ratio_zscore(closes_a, closes_b, lookback):
    """z-score of the latest A/B ratio vs the mean/stdev of the ratio over
    the last `lookback` bars. Returns (z, ratio, mean, std) or None if data
    is insufficient or the ratio has ~no variance."""
    n = min(len(closes_a), len(closes_b))
    if n < lookback:
        return None
    a = closes_a[-lookback:]
    b = closes_b[-lookback:]
    ratios = [x / y for x, y in zip(a, b) if y > 0]
    if len(ratios) < lookback:
        return None
    mean = statistics.fmean(ratios)
    std = statistics.pstdev(ratios)
    if std <= 0:
        return None
    latest = ratios[-1]
    return (latest - mean) / std, latest, mean, std


def reversion_direction(z, leg, numerator, z_entry_min):
    """Direction to trade `leg` so the position profits from the ratio
    (numerator/denominator) reverting toward its mean. None if |z| too small."""
    if z is None or abs(z) < z_entry_min:
        return None
    rich = z > 0   # ratio above mean → numerator expensive vs denominator
    if leg == numerator:
        return "SHORT" if rich else "LONG"
    return "LONG" if rich else "SHORT"   # leg == denominator


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

def fetch_candles(asset):
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h"],
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return []
    return data.get("data", {}).get("candles", {}).get("1h", [])


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
# Thesis builder — one pair
# ═══════════════════════════════════════════════════════════════

def build_pair_thesis(pair, closes_by_asset, candles_by_asset, entry_cfg):
    num, den, leg = pair["numerator"], pair["denominator"], pair["leg"]
    ca, cb = closes_by_asset.get(num), closes_by_asset.get(den)
    if not ca or not cb:
        return None
    lookback = int(entry_cfg.get("lookbackBars", DEFAULT_LOOKBACK_BARS))
    z_min = float(entry_cfg.get("zEntryMin", DEFAULT_Z_ENTRY_MIN))
    z_strong = float(entry_cfg.get("zStrong", DEFAULT_Z_STRONG))

    zr = ratio_zscore(ca, cb, lookback)
    if zr is None:
        return None
    z, ratio, mean, std = zr
    direction = reversion_direction(z, leg, num, z_min)
    if direction is None:
        return None

    # Reversion must be starting, not still extending: |z| one bar ago > |z| now.
    zr_prev = ratio_zscore(ca[:-1], cb[:-1], lookback)
    turning = bool(zr_prev and abs(z) < abs(zr_prev[0]))

    sm_dir, sm_tilt = fetch_sm_direction(leg)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    leg_vol = volume_trend(candles_by_asset.get(leg, []))

    score = 0
    reasons = [f"{num}/{den}_z_{z:+.2f}"]
    score += 2  # |z| >= z_min gate-confirmed
    if abs(z) >= z_strong:
        score += 2
        reasons.append(f"z_extreme_{z:+.2f}")
    if turning:
        score += 1
        reasons.append("ratio_turning")
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
    if leg_vol > 10:
        score += 1
        reasons.append(f"vol_rising_{leg_vol:+.0f}%")

    return {
        "coin": leg,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "pair": f"{num}/{den}",
        "zscore": round(z, 3),
        "ratio": round(ratio, 6),
        "ratio_mean": round(mean, 6),
        "turning": turning,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": sm_tilt,
        "leg_volume_trend_pct": round(leg_vol, 2),
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
        "pair": thesis["pair"],
        "zscore": thesis.get("zscore") or 0.0,
        "ratio": thesis.get("ratio") or 0.0,
        "ratioMean": thesis.get("ratio_mean") or 0.0,
        "smDirection": thesis.get("sm_direction") or "NONE",
        "smTiltPct": thesis.get("sm_tilt_pct") or 0.0,
        "legVolumeTrendPct": thesis.get("leg_volume_trend_pct") or 0.0,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
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
# MAIN — scan all pairs, pick the most-extended reversion
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    pairs = config.get("pairs", DEFAULT_PAIRS)

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_chameleon_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_chameleon_producer_version": VERSION})
        return

    # Fetch 1h candles once per unique asset across all pairs.
    assets = sorted({a for p in pairs for a in (p["numerator"], p["denominator"], p["leg"])})
    candles_by_asset = {a: fetch_candles(a) for a in assets}
    closes_by_asset = {
        a: [_f(c, "close", "c") for c in candles_by_asset.get(a, [])]
        for a in assets
    }

    candidates = []
    for pair in pairs:
        leg = pair["leg"].upper()
        if leg in held_set or cfg.was_recently_signaled(leg):
            continue
        thesis = build_pair_thesis(pair, closes_by_asset, candles_by_asset, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no ratio extended past z-threshold with reversion starting",
            "pairs": [f"{p['numerator']}/{p['denominator']}" for p in pairs],
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_chameleon_producer_version": VERSION,
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
            "pair": best["pair"],
            "zscore": best["zscore"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_chameleon_producer_version": VERSION,
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
        name=f"chameleon-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
