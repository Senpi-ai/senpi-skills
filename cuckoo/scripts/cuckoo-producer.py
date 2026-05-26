#!/usr/bin/env python3
# Senpi CUCKOO Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""CUCKOO v1.0.0 — Copy-the-Copiers (meta-strategy follower).

Cuckoo lets the best-performing STRATEGIES on the platform do the work and rides
their consensus. Each tick it:

  1. Auto-discovers the top strategies by realized performance
     (discovery_get_top_strategies).
  2. Pulls each top strategy's current positions (leaderboard_get_trader_
     positions on the strategy wallet).
  3. Builds (asset, direction) candidates aggregated across strategies,
     PERFORMANCE-WEIGHTED — a stronger strategy gets more say, capped so one
     outlier can't dominate.
  4. Trades the highest weighted-consensus candidate.

This is a META layer: the strategies it follows are themselves copy/algo/
trader-following strategies, so Cuckoo copies the copiers. NEW archetype #14.
Distinct from Remora (operator-picked whales) and the individual trader-
followers. Follow conviction → let-winners-run DSL (wide ladder + staleness
cap). Producer enters only — the DSL owns exits.

REQUIRES USER-SCOPE AUTH for discovery_get_top_strategies /
leaderboard_get_trader_positions.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cuckoo_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "cuckoo_signals"
SIGNAL_TYPE = "CUCKOO_META_CONSENSUS"

MAX_LEVERAGE = 10
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 4
DEFAULT_TOP_N = 12              # how many top strategies to follow
DEFAULT_MIN_STRATEGIES = 2     # require at least this many agreeing
DEFAULT_MIN_NOTIONAL_USD = 2000
DEFAULT_WEIGHT_CAP = 3.0       # max per-strategy weight (outlier guard)
DEFAULT_HIGH_WEIGHT = 6.0      # aggregate weight that earns the bonus point


def _resolve_wallet():
    env_val = (os.environ.get("CUCKOO_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def safe_float(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Pure meta-consensus logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def performance_weight(roi_pct, cap=DEFAULT_WEIGHT_CAP):
    """Map a strategy's ROI% to a follow weight in [0.5, cap]. A flat strategy
    weighs 1.0; a +100% strategy hits the cap; a losing strategy floors at
    0.5 (it still counts a little, but barely)."""
    w = 1.0 + (safe_float(roi_pct) / 50.0)
    if w < 0.5:
        return 0.5
    if w > cap:
        return cap
    return w


def mirror_direction(pos):
    """LONG / SHORT for a position (explicit direction/side field, else szi
    sign). None if undeterminable."""
    if not isinstance(pos, dict):
        return None
    d = str(pos.get("direction", pos.get("side", ""))).upper()
    if d in ("LONG", "SHORT"):
        return d
    szi = safe_float(pos.get("szi", pos.get("size", 0)))
    if szi > 0:
        return "LONG"
    if szi < 0:
        return "SHORT"
    return None


def position_asset(pos):
    if not isinstance(pos, dict):
        return ""
    return str(pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))).upper()


def position_notional(pos):
    if not isinstance(pos, dict):
        return 0.0
    size = abs(safe_float(pos.get("szi", pos.get("size", 0))))
    entry = safe_float(pos.get("entryPx", pos.get("entryPrice", pos.get("entry", 0))))
    notional = size * entry
    if notional > 0:
        return notional
    margin = abs(safe_float(pos.get("marginUsed", pos.get("margin", 0))))
    return margin if margin > 0 else size


def tally_consensus(entries):
    """Aggregate per-strategy votes into weighted (asset, direction) candidates.
    `entries` = list of {"asset","direction","weight"}. Returns a dict keyed by
    (asset, direction) -> {"asset","direction","count","weight"}."""
    agg = {}
    for e in entries:
        asset = str(e.get("asset", "")).upper()
        direction = e.get("direction")
        if not asset or direction not in ("LONG", "SHORT"):
            continue
        weight = safe_float(e.get("weight"), 1.0)
        key = (asset, direction)
        rec = agg.setdefault(key, {"asset": asset, "direction": direction, "count": 0, "weight": 0.0})
        rec["count"] += 1
        rec["weight"] += weight
    return agg


def consensus_score(count, total_weight, high_weight=DEFAULT_HIGH_WEIGHT):
    """Score a weighted-consensus candidate (max ~6)."""
    score = 2  # held by at least one top strategy
    if count >= 4:
        score += 3
    elif count >= 3:
        score += 2
    elif count == 2:
        score += 1
    if total_weight >= high_weight:
        score += 1
    return score


# ═══════════════════════════════════════════════════════════════
# Data fetchers (defensive shape unwrapping)
# ═══════════════════════════════════════════════════════════════

def fetch_top_strategies(top_n):
    """Returns a list of {wallet, roi} for the top strategies by performance.
    Unwraps the discovery response defensively (multi-key fallbacks)."""
    raw = cfg.mcp_call("discovery_get_top_strategies", limit=top_n)
    if not raw:
        return []
    d = raw.get("data", raw) if isinstance(raw, dict) else raw
    items = d
    if isinstance(d, dict):
        items = d.get("strategies", d.get("top_strategies", d.get("results", [])))
    if not isinstance(items, list):
        return []
    out = []
    for it in items[:top_n]:
        if not isinstance(it, dict):
            continue
        wallet = (
            it.get("strategyWalletAddress")
            or it.get("strategy_wallet")
            or it.get("wallet")
            or it.get("trader_id")
            or it.get("address")
        )
        if not wallet:
            continue
        roi = safe_float(
            it.get("roi", it.get("roe", it.get("totalPnlPct", it.get("totalPnl", 0))))
        )
        out.append({"wallet": str(wallet), "roi": roi})
    return out


def fetch_strategy_positions(wallet):
    """Positions for one strategy, unwrapping the nested
    data.positions.positions shape (same as Remora/Spider)."""
    raw = cfg.mcp_call("leaderboard_get_trader_positions", trader_id=wallet)
    if not raw:
        return []
    if not isinstance(raw, dict):
        return raw if isinstance(raw, list) else []
    d = raw.get("data", raw)
    if isinstance(d, list):
        return d
    if not isinstance(d, dict):
        return []
    rp = d.get("positions", d.get("top_positions", []))
    if isinstance(rp, list):
        return rp
    if isinstance(rp, dict):
        nested = rp.get("positions", [])
        return nested if isinstance(nested, list) else []
    return []


# ═══════════════════════════════════════════════════════════════
# Candidate building
# ═══════════════════════════════════════════════════════════════

def gather_entries(strategies, config):
    """One weighted vote per (strategy, asset, direction) for every qualifying
    position the top strategies hold. Returns the entries list for
    tally_consensus()."""
    min_notional = float(config.get("minNotionalUsd", DEFAULT_MIN_NOTIONAL_USD))
    cap = float(config.get("weightCap", DEFAULT_WEIGHT_CAP))
    entries = []
    for strat in strategies:
        wallet = strat["wallet"]
        weight = performance_weight(strat["roi"], cap)
        positions = fetch_strategy_positions(wallet)
        seen = set()  # dedupe within a strategy: one vote per asset+direction
        for pos in positions:
            asset = position_asset(pos)
            direction = mirror_direction(pos)
            if not asset or direction is None:
                continue
            if position_notional(pos) < min_notional:
                continue
            key = (asset, direction)
            if key in seen:
                continue
            seen.add(key)
            entries.append({"asset": asset, "direction": direction, "weight": weight})
    return entries


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(cand, score, reasons, margin_usd, leverage, held_assets, strategies_followed):
    if not STRATEGY_ADDRESS:
        return False
    coin = cand["asset"]
    if coin.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": score,
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": cand["direction"],
        "reasons": reasons,
        "strategyCount": cand["count"],
        "consensusWeight": round(cand["weight"], 2),
        "strategiesFollowed": strategies_followed,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=cand["direction"],
            score=min(score / 6.0, 1.0),
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
# MAIN — discover top strategies, weight consensus, follow the best
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    top_n = int(config.get("topN", DEFAULT_TOP_N))
    min_strategies = int(config.get("minStrategies", DEFAULT_MIN_STRATEGIES))

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_cuckoo_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_cuckoo_producer_version": VERSION})
        return

    strategies = fetch_top_strategies(top_n)
    if not strategies:
        cfg.output({
            "status": "ok",
            "note": "WAITING — discovery_get_top_strategies returned no strategies",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_cuckoo_producer_version": VERSION,
        })
        return

    entries = gather_entries(strategies, config)
    consensus = tally_consensus(entries)

    scored = []
    for cand in consensus.values():
        if cand["count"] < min_strategies:
            continue
        if cand["asset"].upper() in held_set or cfg.was_recently_signaled(cand["asset"]):
            continue
        score = consensus_score(cand["count"], cand["weight"], float(config.get("highWeight", DEFAULT_HIGH_WEIGHT)))
        if score >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            reasons = [
                f"{cand['asset']}_{cand['direction']}",
                f"top_strategies_{cand['count']}",
                f"weighted_{cand['weight']:.1f}",
            ]
            scored.append((score, reasons, cand))

    if not scored:
        cfg.output({
            "status": "ok",
            "note": f"WAITING — no asset held by >= {min_strategies} top strategies in agreement",
            "strategies_followed": len(strategies),
            "candidates_seen": len(consensus),
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_cuckoo_producer_version": VERSION,
        })
        return

    scored.sort(key=lambda t: (t[0], t[2]["weight"], t[2]["count"]), reverse=True)
    best_score, best_reasons, best = scored[0]

    margin_pct = float(config.get("marginPct", 0.15))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(best, best_score, best_reasons, margin_usd, leverage, held_assets, len(strategies))
    if pushed:
        cfg.record_signal(best["asset"])

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["asset"],
            "direction": best["direction"],
            "strategy_count": best["count"],
            "consensus_weight": round(best["weight"], 2),
            "score": best_score,
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best_reasons[:5],
        },
        "strategies_followed": len(strategies),
        "candidates_count": len(scored),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_cuckoo_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=600,   # 10min — top-strategy consensus drifts slowly
        name=f"cuckoo-producer-{_wallet_lock_id}",
        tick_timeout=240,
    )
