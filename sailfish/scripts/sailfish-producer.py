#!/usr/bin/env python3
# Senpi SAILFISH Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SAILFISH v1.0.0 — Relative-Strength Rotator (Crypto Majors).

"Always hold the strongest major."

Each tick Sailfish ranks BTC/ETH/SOL/HYPE by 4h relative strength and longs
the strongest. The runtime is single-position and the producer never closes
(DSL owns exits), so "rotation" here means: enter the current leader when a
slot is open. When the existing position naturally exits via the DSL trail,
Sailfish enters the new leader on the next tick.

Distinct from Chameleon (relative-value mean-reversion of pair ratios) —
Sailfish is **momentum rotation**: follow the strongest. To avoid leadership
whipsaw, the producer requires the leader to be margin > leaderMarginPct
ahead of the runner-up (default 1.5%).

Architecture: helpers-native producer + balanced DSL with a 96h hard_timeout
so leadership can play out. Stateless ranking + race-window dedup. Producer
NEVER closes — DSL owns exits.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sailfish_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "sailfish_signals"
SIGNAL_TYPE = "SAILFISH_RS_LEADER"

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3
DEFAULT_MIN_SCORE = 4

DEFAULT_WHITELIST = ["BTC", "ETH", "SOL", "HYPE"]
DEFAULT_RS_LOOKBACK = 16        # 16 × 4h bars = ~2.7 days
DEFAULT_MIN_LEADER_RS_PCT = 1.0  # leader's own RS must be >= this %
DEFAULT_LEADER_MARGIN_PCT = 1.5  # leader must beat the runner-up by this much


def _resolve_wallet():
    env_val = (os.environ.get("SAILFISH_WALLET") or "").strip()
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
# Pure relative-strength logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def relative_strength(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    Used as a single-asset RS proxy; ranking sorts these across the universe.
    None if insufficient data or ref price is non-positive."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def rank_assets(strength_by_asset):
    """Sort {asset: strength} by strength descending. Assets with None
    strength are dropped. Returns list of (asset, strength)."""
    pairs = [(a, s) for a, s in strength_by_asset.items() if s is not None]
    pairs.sort(key=lambda t: t[1], reverse=True)
    return pairs


def leader_above_runner_up(ranked, min_leader_rs_pct, margin_pct):
    """Given a ranked list of (asset, strength), return the leader iff:
      1. leader's own RS >= min_leader_rs_pct (leader must actually be up), AND
      2. leader - runner_up >= margin_pct (clean separation, no whipsaw).
    If there's only one ranked asset, the margin requirement defaults to met.
    Returns (asset, strength, margin_vs_runner_up) or None."""
    if not ranked:
        return None
    leader_asset, leader_rs = ranked[0]
    if leader_rs < min_leader_rs_pct:
        return None
    if len(ranked) == 1:
        return (leader_asset, leader_rs, float('inf'))
    runner_rs = ranked[1][1]
    margin = leader_rs - runner_rs
    if margin < margin_pct:
        return None
    return (leader_asset, leader_rs, margin)


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


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(asset, leader_rs, margin_vs_runner, score, reasons, margin_usd, leverage, held_assets, ranked):
    if not STRATEGY_ADDRESS:
        return False
    if asset.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": score,
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": "LONG",
        "reasons": reasons,
        "leaderRsPct": float(leader_rs) or 0.0,
        "leaderMarginPct": float(margin_vs_runner) if margin_vs_runner != float('inf') else 0.0,
        "rankSnapshot": [{"asset": a, "rs": round(s, 2)} for a, s in ranked[:6]],
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=asset,
            direction="LONG",
            score=min(score / 6.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {asset}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {asset}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    whitelist = config.get("whitelist", DEFAULT_WHITELIST)
    lookback = int(config.get("rsLookbackBars", DEFAULT_RS_LOOKBACK))
    min_leader_rs = float(config.get("minLeaderRsPct", DEFAULT_MIN_LEADER_RS_PCT))
    margin_pct_threshold = float(config.get("leaderMarginPct", DEFAULT_LEADER_MARGIN_PCT))

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_sailfish_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_sailfish_producer_version": VERSION})
        return

    strength_by_asset = {}
    for asset in whitelist:
        candles = fetch_candles(asset)
        if len(candles) <= lookback:
            continue
        closes = [_f(c, "close", "c") for c in candles]
        strength_by_asset[asset.upper()] = relative_strength(closes, lookback)

    ranked = rank_assets(strength_by_asset)
    picked = leader_above_runner_up(ranked, min_leader_rs, margin_pct_threshold)

    if picked is None:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no clear leader (insufficient RS or margin)",
            "ranked": [{"asset": a, "rs": round(s, 2)} for a, s in ranked],
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_sailfish_producer_version": VERSION,
        })
        return

    leader_asset, leader_rs, margin_vs_runner = picked

    if leader_asset in held_set:
        cfg.output({
            "status": "ok",
            "note": f"HOLDING — {leader_asset} is the current leader and we already own it",
            "ranked": [{"asset": a, "rs": round(s, 2)} for a, s in ranked],
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_sailfish_producer_version": VERSION,
        })
        return

    if cfg.was_recently_signaled(leader_asset):
        cfg.output({
            "status": "ok",
            "note": f"WAITING — {leader_asset} is the leader but recently-signaled (race-window dedup)",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_sailfish_producer_version": VERSION,
        })
        return

    # Score
    score = 3   # base — leader cleared both RS and margin gates
    reasons = [f"{leader_asset}_rs_{leader_rs:+.2f}%", f"margin_vs_runner_up_{margin_vs_runner:+.2f}pp"]
    if leader_rs >= 4.0:
        score += 1
        reasons.append("leader_strong")
    if margin_vs_runner >= 3.0:
        score += 1
        reasons.append("dominant_margin")
    if held_assets:
        # Re-entering after a prior position exited — leadership has either
        # confirmed the holdover OR rotated. Either way it's a fresh decision.
        reasons.append("post_dsl_re_entry")

    if score < int(config.get("minScore", DEFAULT_MIN_SCORE)):
        cfg.output({
            "status": "ok",
            "note": "WAITING — leader cleared margin but missed minScore",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_sailfish_producer_version": VERSION,
        })
        return

    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_pct = float(config.get("marginPct", 0.20))
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(leader_asset, leader_rs, margin_vs_runner, score, reasons,
                         margin_usd, leverage, held_assets, ranked)
    if pushed:
        cfg.record_signal(leader_asset)

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": leader_asset,
            "direction": "LONG",
            "leader_rs_pct": round(leader_rs, 2),
            "margin_vs_runner_up_pp": round(margin_vs_runner, 2) if margin_vs_runner != float('inf') else None,
            "score": score,
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": reasons[:5],
        },
        "ranked": [{"asset": a, "rs": round(s, 2)} for a, s in ranked],
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_sailfish_producer_version": VERSION,
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
        name=f"sailfish-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
