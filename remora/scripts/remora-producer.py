#!/usr/bin/env python3
# Senpi REMORA Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""REMORA v1.0.0 — Whale Single-Position Mirror.

Remora rides a small, hand-picked set of whale traders the way a remora fish
rides a shark. Each tick it:

  1. Pulls each whale's open positions (leaderboard_get_trader_positions).
  2. Picks each whale's highest-conviction (largest-notional) position.
  3. Aggregates across whales into (asset, direction) candidates, counting how
     many whales hold each — CONSENSUS is the edge multiplier.
  4. Optionally validates whale quality (discovery_get_trader_state ELITE /
     RELIABLE tier) as a scoring bonus.
  5. Mirrors the highest-scoring candidate (same asset + direction), sized by
     Remora's OWN margin %, leverage capped.

Distinct from the broad trader-followers (Raptor/Jackal/Spider) that scan a
leaderboard universe and synthesize. Remora is a focused MIRROR of whales YOU
choose. Whales hold winners, so the DSL is the let-winners-run preset (wide
ladder), with a hard_timeout as a staleness cap. Producer enters only — the DSL
owns exits (a whale-exit mirror is a future enhancement).

REQUIRES USER-SCOPE AUTH for leaderboard_get_trader_positions /
discovery_get_trader_state.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remora_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "remora_signals"
SIGNAL_TYPE = "REMORA_WHALE_MIRROR"

MAX_LEVERAGE = 10
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 4
DEFAULT_MIN_NOTIONAL_USD = 5000   # ignore dust positions
# Whale-quality tiers that earn the discovery_get_trader_state bonus.
QUALITY_TIERS = {"ELITE", "RELIABLE", "PROFITABLE"}


def _resolve_wallet():
    env_val = (os.environ.get("REMORA_WALLET") or "").strip()
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
# Pure mirror logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def position_notional(pos):
    """USD notional of a position for conviction ranking: size × entry,
    falling back to marginUsed, then to raw size."""
    if not isinstance(pos, dict):
        return 0.0
    size = abs(safe_float(pos.get("szi", pos.get("size", 0))))
    entry = safe_float(pos.get("entryPx", pos.get("entryPrice", pos.get("entry", 0))))
    notional = size * entry
    if notional > 0:
        return notional
    margin = abs(safe_float(pos.get("marginUsed", pos.get("margin", 0))))
    return margin if margin > 0 else size


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


def top_position(positions, min_notional=0.0):
    """The single largest-notional position with a determinable direction and
    notional >= min_notional. None if the whale holds nothing qualifying."""
    best, best_n = None, -1.0
    for p in positions or []:
        if not isinstance(p, dict):
            continue
        if mirror_direction(p) is None or not position_asset(p):
            continue
        n = position_notional(p)
        if n < min_notional:
            continue
        if n > best_n:
            best_n, best = n, p
    return best


def consensus_bonus(count):
    """Score bonus for how many whales independently hold the same
    asset+direction. 3+ whales is a strong consensus."""
    if count >= 3:
        return 3
    if count == 2:
        return 2
    return 0


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_whale_positions(trader_id):
    """Returns a list of position dicts for one whale, unwrapping the
    nested data.positions.positions shape."""
    raw = cfg.mcp_call("leaderboard_get_trader_positions", trader_id=trader_id)
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
    if isinstance(rp, dict):  # nested one level deeper (observed shape)
        nested = rp.get("positions", [])
        return nested if isinstance(nested, list) else []
    return []


def fetch_whale_tier(trader_id):
    """ELITE / RELIABLE / etc. for one whale, or None if unavailable."""
    raw = cfg.mcp_call("discovery_get_trader_state", trader_id=trader_id)
    if not raw or not isinstance(raw, dict):
        return None
    d = raw.get("data", raw)
    if not isinstance(d, dict):
        return None
    tier = d.get("tier", d.get("classification", d.get("rating")))
    return str(tier).upper() if tier else None


# ═══════════════════════════════════════════════════════════════
# Candidate aggregation
# ═══════════════════════════════════════════════════════════════

def gather_candidates(whales, config):
    """For each whale, take their top position; aggregate into
    (asset, direction) candidates with consensus count + max notional +
    whale quality. Returns a list of candidate dicts."""
    min_notional = float(config.get("minNotionalUsd", DEFAULT_MIN_NOTIONAL_USD))
    use_tier = bool(config.get("useWhaleQuality", True))

    agg = {}
    for whale in whales:
        trader_id = whale.get("trader_id") or whale.get("wallet") or whale
        if not trader_id:
            continue
        positions = fetch_whale_positions(trader_id)
        top = top_position(positions, min_notional)
        if not top:
            continue
        asset = position_asset(top)
        direction = mirror_direction(top)
        notional = position_notional(top)
        tier = fetch_whale_tier(trader_id) if use_tier else None

        key = (asset, direction)
        entry = agg.setdefault(key, {
            "asset": asset, "direction": direction,
            "count": 0, "max_notional": 0.0, "quality": False,
            "whales": [],
        })
        entry["count"] += 1
        entry["max_notional"] = max(entry["max_notional"], notional)
        if tier in QUALITY_TIERS:
            entry["quality"] = True
        entry["whales"].append(str(trader_id)[:10])

    return list(agg.values())


def score_candidate(cand, config):
    count = cand["count"]
    score = 3  # a tracked whale's top conviction position
    reasons = [f"{cand['asset']}_{cand['direction']}", f"whales_{count}", f"notional_${cand['max_notional']:,.0f}"]
    cb = consensus_bonus(count)
    if cb:
        score += cb
        reasons.append(f"consensus_{count}_whales")
    if cand.get("quality"):
        score += 1
        reasons.append("elite_tier")
    return score, reasons


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(cand, score, reasons, margin_usd, leverage, held_assets):
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
        "whaleCount": cand["count"],
        "maxNotionalUsd": round(cand["max_notional"], 2),
        "eliteTier": bool(cand.get("quality")),
        "whales": cand.get("whales", []),
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=cand["direction"],
            score=min(score / 7.0, 1.0),
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
# MAIN — gather whale tops, score, mirror the best
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    whales = config.get("whales", [])

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_remora_producer_version": VERSION})
        return
    if not whales:
        cfg.output({
            "status": "ok",
            "note": "no whales configured — set config.whales to a list of trader_ids/wallets",
            "_remora_producer_version": VERSION,
        })
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_remora_producer_version": VERSION})
        return

    candidates = gather_candidates(whales, config)
    scored = []
    for cand in candidates:
        if cand["asset"].upper() in held_set or cfg.was_recently_signaled(cand["asset"]):
            continue
        score, reasons = score_candidate(cand, config)
        if score >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            scored.append((score, reasons, cand))

    if not scored:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no qualifying whale position to mirror",
            "whales_tracked": len(whales),
            "candidates_seen": len(candidates),
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_remora_producer_version": VERSION,
        })
        return

    # Highest score, tie-break by consensus count then notional.
    scored.sort(key=lambda t: (t[0], t[2]["count"], t[2]["max_notional"]), reverse=True)
    best_score, best_reasons, best = scored[0]

    margin_pct = float(config.get("marginPct", 0.15))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(best, best_score, best_reasons, margin_usd, leverage, held_assets)
    if pushed:
        cfg.record_signal(best["asset"])

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["asset"],
            "direction": best["direction"],
            "whale_count": best["count"],
            "max_notional_usd": round(best["max_notional"], 2),
            "elite_tier": bool(best.get("quality")),
            "score": best_score,
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best_reasons[:5],
        },
        "candidates_count": len(scored),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_remora_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=600,   # 10min — whale positions change slowly
        name=f"remora-producer-{_wallet_lock_id}",
        tick_timeout=240,
    )
