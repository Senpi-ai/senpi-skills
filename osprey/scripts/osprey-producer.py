#!/usr/bin/env python3
# Senpi OSPREY Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""OSPREY v1.0.0 — Cross-Venue Lag (crypto leader → XYZ equity proxy).

When a crypto leader (BTC) makes a strong move, crypto-correlated equities
priced on Hyperliquid XYZ — Coinbase (COIN), MicroStrategy (MSTR), miners —
tend to FOLLOW, but on a different venue and with a lag (trade.xyz pricing can
trail spot crypto, especially around its reference windows).

Osprey measures each proxy's catch-up GAP each tick:

    expected_move = leader_move_pct × proxy_beta
    gap           = expected_move − proxy_actual_move

A large gap in the LEADER'S direction means the proxy hasn't caught up yet:
  leader +X% and gap still positive → proxy lagging up   → LONG proxy
  leader −X% and gap still negative → proxy lagging down  → SHORT proxy
If the proxy already moved proportionally (or overshot), the gap shrinks or
flips sign → no trade.

Distinct from Mantis (#9 cross-asset lag), which trades crypto→crypto laggards
surfaced by market_get_cross_asset_flows. Osprey trades the CROSS-VENUE
crypto→XYZ-equity lag and SELF-COMPUTES the gap from candles, because
cross_asset_flows only surfaces crypto laggards. A catch-up move can extend, so
the DSL is the let-winners-run preset. Producer NEVER closes — DSL owns exits.

REQUIRES USER-SCOPE AUTH for leaderboard_get_markets.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import osprey_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "osprey_signals"
SIGNAL_TYPE = "OSPREY_CROSS_VENUE_LAG"

MAX_LEVERAGE = 10
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 4
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70

DEFAULT_LEADER = "BTC"
# Proxy XYZ equities and their typical beta to the leader. Operator-tunable.
DEFAULT_PROXIES = [
    {"proxy": "xyz:COIN", "beta": 1.8},
    {"proxy": "xyz:MSTR", "beta": 2.5},
]
DEFAULT_MOVE_LOOKBACK = 4            # 1h bars — the "recent move" window for both legs
DEFAULT_MIN_LEADER_MOVE = 2.0       # leader must move at least this % to matter
DEFAULT_MIN_GAP_PCT = 2.0           # proxy must still owe at least this % of catch-up
DEFAULT_STRONG_GAP_PCT = 5.0


def _resolve_wallet():
    env_val = (os.environ.get("OSPREY_WALLET") or "").strip()
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
# Pure cross-venue lag math (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def move_pct(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or the reference price is non-positive."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def catchup_gap(leader_move, proxy_move, beta):
    """How much of the expected catch-up move the proxy still owes.
    expected = leader_move × beta; gap = expected − actual."""
    return (leader_move * beta) - proxy_move


def lag_direction(leader_move, gap, min_leader_move, min_gap):
    """Direction to trade the proxy so it profits from closing the gap.
    None unless the leader moved enough AND the proxy still owes a gap in the
    LEADER'S direction (same sign). An overshot proxy (gap flips sign) is
    skipped — the catch-up is already done."""
    if leader_move is None or gap is None:
        return None
    if abs(leader_move) < min_leader_move:
        return None
    if abs(gap) < min_gap:
        return None
    if (leader_move > 0) != (gap > 0):   # gap must share the leader's sign
        return None
    return "LONG" if gap > 0 else "SHORT"


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
# Thesis builder — one proxy
# ═══════════════════════════════════════════════════════════════

def build_thesis(proxy_cfg, leader_move, proxy_closes, proxy_candles, config):
    proxy = proxy_cfg["proxy"]
    beta = float(proxy_cfg.get("beta", 1.0))
    lookback = int(config.get("moveLookbackBars", DEFAULT_MOVE_LOOKBACK))
    min_leader = float(config.get("minLeaderMovePct", DEFAULT_MIN_LEADER_MOVE))
    min_gap = float(config.get("minGapPct", DEFAULT_MIN_GAP_PCT))
    strong_gap = float(config.get("strongGapPct", DEFAULT_STRONG_GAP_PCT))

    proxy_move = move_pct(proxy_closes, lookback)
    if proxy_move is None:
        return None
    gap = catchup_gap(leader_move, proxy_move, beta)
    direction = lag_direction(leader_move, gap, min_leader, min_gap)
    if direction is None:
        return None

    sm_dir, sm_tilt = fetch_sm_direction(proxy)
    sm_min = float(config.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(config.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    vol_trend = volume_trend(proxy_candles)

    score = 0
    reasons = [f"leader_{leader_move:+.1f}%", f"{proxy}_lag_{proxy_move:+.1f}%", f"gap_{gap:+.1f}%"]
    score += 2  # leader moved + proxy owes a gap in the leader's direction (gate)
    if abs(gap) >= strong_gap:
        score += 2
        reasons.append(f"gap_strong_{gap:+.1f}%")
    # SM is often sparse on XYZ equities — agreement is a bonus, not a gate.
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    if vol_trend > 15:
        score += 1
        reasons.append(f"vol_rising_{vol_trend:+.0f}%")

    return {
        "coin": proxy,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "leader_move_pct": round(leader_move, 2),
        "proxy_move_pct": round(proxy_move, 2),
        "gap_pct": round(gap, 2),
        "beta": beta,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": sm_tilt,
        "volume_trend_pct": round(vol_trend, 2),
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(thesis, margin_usd, leverage, held_assets, leader):
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
        "leader": leader,
        "leaderMovePct": thesis.get("leader_move_pct") or 0.0,
        "proxyMovePct": thesis.get("proxy_move_pct") or 0.0,
        "gapPct": thesis.get("gap_pct") or 0.0,
        "beta": thesis.get("beta") or 0.0,
        "smDirection": thesis.get("sm_direction") or "NONE",
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
# MAIN — measure leader move, score each proxy's catch-up gap
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    leader = config.get("leader", DEFAULT_LEADER)
    proxies = config.get("proxies", DEFAULT_PROXIES)

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_osprey_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_osprey_producer_version": VERSION})
        return

    lookback = int(config.get("moveLookbackBars", DEFAULT_MOVE_LOOKBACK))
    leader_candles = fetch_candles(leader)
    leader_closes = [_f(c, "close", "c") for c in leader_candles]
    leader_move = move_pct(leader_closes, lookback)
    min_leader = float(config.get("minLeaderMovePct", DEFAULT_MIN_LEADER_MOVE))

    if leader_move is None or abs(leader_move) < min_leader:
        cfg.output({
            "status": "ok",
            "note": f"WAITING — leader {leader} move {leader_move if leader_move is not None else 'n/a'} below {min_leader}% threshold",
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_osprey_producer_version": VERSION,
        })
        return

    candidates = []
    for proxy_cfg in proxies:
        proxy = proxy_cfg["proxy"].upper()
        if proxy in held_set or cfg.was_recently_signaled(proxy):
            continue
        proxy_candles = fetch_candles(proxy_cfg["proxy"])
        if len(proxy_candles) <= lookback:
            continue
        proxy_closes = [_f(c, "close", "c") for c in proxy_candles]
        thesis = build_thesis(proxy_cfg, leader_move, proxy_closes, proxy_candles, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": f"WAITING — {leader} moved {leader_move:+.1f}% but no proxy still owes a catch-up gap",
            "leader_move_pct": round(leader_move, 2),
            "proxies": [p["proxy"] for p in proxies],
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_osprey_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: (c["score"], abs(c["gap_pct"])), reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.15))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(best, margin_usd, leverage, held_assets, leader)
    if pushed:
        cfg.record_signal(best["coin"])

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["coin"],
            "direction": best["direction"],
            "leader_move_pct": best["leader_move_pct"],
            "proxy_move_pct": best["proxy_move_pct"],
            "gap_pct": best["gap_pct"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "leader": leader,
        "leader_move_pct": round(leader_move, 2),
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_osprey_producer_version": VERSION,
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
        name=f"osprey-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
