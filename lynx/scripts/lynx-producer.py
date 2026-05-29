#!/usr/bin/env python3
# Senpi LYNX Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""LYNX v1.0.0 — Adaptive MIN_SCORE Self-Tuner.

Lynx is the Vulture v4.1 story productized into a first-class agent pattern.
It runs a simple multi-asset momentum scorer on BTC/ETH/SOL/HYPE, and every
N ticks runs an audit on its own closed-trade history (via audit_query). If
any score bucket at or below the current floor has accumulated `minBucketN`
samples AND is averaging worse than `bucketBleedThresholdPct`, Lynx
auto-raises its MIN_SCORE above that bucket and logs the adjustment.

The same operation Vulture's agent ran by hand for v4.1, but on a scheduled
cron, with the same 30-trade-table style of analysis. NEW archetype #15:
Self-tuning / adaptive-threshold agent.

Scoring (max ~8): 4h trend strength (+3), 1h confirmation (+2), SM aligned
(+2), volume rising (+1). Initial MIN_SCORE 4 (permissive — gives the
auto-tuner data to work with). Auto-tunes up to maxMinScore (default 7).

Producer NEVER closes — DSL owns exits. LLM gate is pass-through. Wide
let-winners-run DSL since this is a momentum agent.

REQUIRES USER-SCOPE AUTH for leaderboard_get_markets + audit_query.
"""

import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lynx_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "lynx_signals"
SIGNAL_TYPE = "LYNX_ADAPTIVE_MOMENTUM"

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3
DEFAULT_INITIAL_MIN_SCORE = 4
DEFAULT_MAX_MIN_SCORE = 7         # upper bound — Lynx never tunes above this
DEFAULT_AUDIT_EVERY_SEC = 21600   # 6h between audits (each audit is one MCP call)
DEFAULT_MIN_BUCKET_N = 8          # need at least this many trades in a bucket to act
DEFAULT_BUCKET_BLEED_PCT = -1.0   # bucket avg ROE below this → cull
DEFAULT_AUDIT_LIMIT = 200         # max closed-position records to pull per audit

DEFAULT_WHITELIST = ["BTC", "ETH", "SOL", "HYPE"]
DEFAULT_SM_TILT_MIN = 55


def _resolve_wallet():
    env_val = (os.environ.get("LYNX_WALLET") or "").strip()
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
# Pure self-tuning logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

_SCORE_RE = re.compile(r"\bscore[:\s=]+(\d+)\b", re.IGNORECASE)


def parse_score_from_reasoning(text):
    """Extract `score N` from ai_reasoning text. Looks for 'score 5' or
    'score: 5' or 'Score=5' (case-insensitive). Returns int or None."""
    if not text or not isinstance(text, str):
        return None
    m = _SCORE_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (ValueError, IndexError):
        return None


def compute_bucket_stats(trades):
    """Bucket trades by score and compute per-bucket stats.

    Each trade: {"score": int, "roe_pct": float} (other fields ignored).

    Returns {score: {"n": int, "avg_roe_pct": float, "win_rate_pct": float}}.
    Trades with no score are dropped (no bucket). Win = ROE > 0.
    """
    buckets = {}
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        s = t.get("score")
        if s is None:
            continue
        try:
            score = int(s)
            roe = float(t.get("roe_pct", 0))
        except (TypeError, ValueError):
            continue
        b = buckets.setdefault(score, {"n": 0, "total_roe": 0.0, "wins": 0})
        b["n"] += 1
        b["total_roe"] += roe
        if roe > 0:
            b["wins"] += 1
    out = {}
    for score, b in buckets.items():
        out[score] = {
            "n": b["n"],
            "avg_roe_pct": (b["total_roe"] / b["n"]) if b["n"] else 0.0,
            "win_rate_pct": (b["wins"] * 100.0 / b["n"]) if b["n"] else 0.0,
        }
    return out


def recommend_min_score(bucket_stats, current_min_score, min_bucket_n, bucket_bleed_pct, max_min_score):
    """Decide whether to raise MIN_SCORE based on the bucket stats.

    Rule: find the HIGHEST score bucket at-or-above the current floor that
    has accumulated `min_bucket_n` samples AND is bleeding (avg_roe_pct
    below `bucket_bleed_pct`). If found, recommend MIN_SCORE = that score + 1
    (so the next eligible bucket starts above it). Caps at `max_min_score`.
    If no bucket meets the bleed criterion, returns the current_min_score.
    """
    bleeding_floors = [
        score for score, stats in bucket_stats.items()
        if score >= current_min_score
        and stats["n"] >= min_bucket_n
        and stats["avg_roe_pct"] < bucket_bleed_pct
    ]
    if not bleeding_floors:
        return current_min_score
    highest_bleeding = max(bleeding_floors)
    recommended = highest_bleeding + 1
    return min(recommended, max_min_score)


def should_update_threshold(current, recommended, hysteresis=1):
    """Only update if the recommended threshold is at least `hysteresis`
    above the current. Prevents flapping on small noise."""
    if recommended is None or current is None:
        return False
    return recommended >= current + hysteresis


# ═══════════════════════════════════════════════════════════════
# Pure scoring logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def pct_move(closes, lookback):
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def trend_direction(strength_pct, threshold):
    if strength_pct is None or abs(strength_pct) < threshold:
        return None
    return "LONG" if strength_pct > 0 else "SHORT"


def lynx_score(trend_strength_4h, trend_1h_aligned, sm_aligned, volume_rising):
    """Compose a Lynx score from boolean / magnitude inputs.

    - 4h trend strength: +3 if |move| >= 4%, +2 if >= 2%, +1 if >= 1%
    - 1h confirmation:   +2 if aligned to 4h direction
    - SM aligned:        +2 if SM tilts in same direction past threshold
    - Volume rising:     +1 if recent vol > prior baseline

    Max ~8. Floor is operator-tunable via MIN_SCORE.
    """
    s = 0
    if trend_strength_4h is None:
        return 0
    abs_t = abs(trend_strength_4h)
    if abs_t >= 4.0:
        s += 3
    elif abs_t >= 2.0:
        s += 2
    elif abs_t >= 1.0:
        s += 1
    if trend_1h_aligned:
        s += 2
    if sm_aligned:
        s += 2
    if volume_rising:
        s += 1
    return s


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


def fetch_closed_trades(user_id, limit):
    """Pull closed-trade records via audit_query. Returns a list of
    {score, roe_pct, ts} (best-effort extraction; missing fields = None)."""
    if not user_id:
        return []
    raw = cfg.mcp_call(
        "audit_query",
        user_ids=[user_id],
        action_type="close",
        limit=int(limit),
    )
    if not raw:
        return []
    d = raw.get("data", raw) if isinstance(raw, dict) else raw
    entries = d if isinstance(d, list) else (d.get("entries", d.get("results", [])) if isinstance(d, dict) else [])
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        reasoning = e.get("ai_reasoning") or e.get("reasoning") or ""
        score = parse_score_from_reasoning(reasoning)
        roe = e.get("roe_pct") or e.get("pnl_pct") or e.get("roi_pct")
        try:
            roe_v = float(roe) if roe is not None else 0.0
        except (TypeError, ValueError):
            roe_v = 0.0
        ts = e.get("ts") or e.get("timestamp") or e.get("created_at")
        out.append({"score": score, "roe_pct": roe_v, "ts": ts})
    return out


# ═══════════════════════════════════════════════════════════════
# Self-tuning audit (the centerpiece)
# ═══════════════════════════════════════════════════════════════

def run_audit_if_due(config, state, now):
    """If the audit interval has elapsed, pull closed trades, compute bucket
    stats, and possibly update MIN_SCORE. Returns updated state + the
    audit summary (or None if skipped)."""
    audit_every = float(config.get("auditEverySec", DEFAULT_AUDIT_EVERY_SEC))
    last_audit_at = state.get("last_audit_at")
    if last_audit_at is not None:
        try:
            if (now - float(last_audit_at)) < audit_every:
                return state, None
        except (TypeError, ValueError):
            pass

    user_id = config.get("senpiUserId")
    if not user_id:
        return state, {"skipped": "no senpiUserId in config — set it to enable self-tuning"}

    trades = fetch_closed_trades(user_id, int(config.get("auditLimit", DEFAULT_AUDIT_LIMIT)))
    bucket_stats = compute_bucket_stats(trades)
    current_min = int(state.get("current_min_score") or config.get("initialMinScore", DEFAULT_INITIAL_MIN_SCORE))
    min_n = int(config.get("minBucketN", DEFAULT_MIN_BUCKET_N))
    bleed_pct = float(config.get("bucketBleedThresholdPct", DEFAULT_BUCKET_BLEED_PCT))
    max_min = int(config.get("maxMinScore", DEFAULT_MAX_MIN_SCORE))

    recommended = recommend_min_score(bucket_stats, current_min, min_n, bleed_pct, max_min)
    state = dict(state)
    state["last_audit_at"] = float(now)
    state["audit_count"] = int(state.get("audit_count", 0)) + 1

    audit_summary = {
        "trades_examined": len(trades),
        "trades_with_score": sum(1 for t in trades if t.get("score") is not None),
        "bucket_stats": {str(k): v for k, v in sorted(bucket_stats.items(), reverse=True)},
        "current_min_score": current_min,
        "recommended_min_score": recommended,
        "updated": False,
    }

    if should_update_threshold(current_min, recommended):
        adjustments = list(state.get("adjustments", []))
        adjustments.append({
            "ts": float(now),
            "prev": current_min,
            "new": recommended,
            "trades_examined": len(trades),
            "bleeding_buckets": [
                {"score": s, "stats": v}
                for s, v in bucket_stats.items()
                if s >= current_min and v["n"] >= min_n and v["avg_roe_pct"] < bleed_pct
            ],
        })
        state["adjustments"] = adjustments
        state["current_min_score"] = recommended
        audit_summary["updated"] = True

    return state, audit_summary


# ═══════════════════════════════════════════════════════════════
# Thesis builder — one asset
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, current_min_score, config):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles = data.get("data", {}).get("candles", {})
    closes_4h = [_f(c, "close", "c") for c in candles.get("4h", [])]
    closes_1h = [_f(c, "close", "c") for c in candles.get("1h", [])]
    if len(closes_4h) < 8 or len(closes_1h) < 8:
        return None

    move_4h = pct_move(closes_4h, 6)    # 24h on 4h bars
    move_1h = pct_move(closes_1h, 4)    # 4h on 1h bars
    direction = trend_direction(move_4h, 1.0)
    if direction is None:
        return None
    one_h_aligned = (move_1h is not None) and ((move_1h > 0) == (move_4h > 0)) and (abs(move_1h) >= 0.5)

    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(config.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_aligned = (sm_dir == direction and sm_tilt >= sm_min)

    # Volume surge
    vols = [_f(c, "volume", "v") for c in candles.get("4h", [])]
    vol_rising = False
    if len(vols) >= 12:
        recent = sum(vols[-3:]) / 3
        baseline = sum(vols[-12:-3]) / 9
        vol_rising = baseline > 0 and (recent / baseline) >= 1.3

    score = lynx_score(move_4h, one_h_aligned, sm_aligned, vol_rising)
    if score < current_min_score:
        return None

    reasons = [f"trend4h_{move_4h:+.1f}%", f"score_{score}", f"floor_{current_min_score}"]
    if one_h_aligned:
        reasons.append("1h_aligned")
    if sm_aligned:
        reasons.append(f"sm_{sm_tilt:.0f}%")
    if vol_rising:
        reasons.append("vol_rising")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h_pct": round(move_4h, 2),
        "trend_1h_pct": round(move_1h, 2) if move_1h is not None else 0.0,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": sm_tilt,
        "vol_rising": vol_rising,
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(thesis, margin_usd, leverage, held_assets, current_min_score):
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
        "currentMinScore": current_min_score,
        "trend4hPct": thesis.get("trend_4h_pct") or 0.0,
        "trend1hPct": thesis.get("trend_1h_pct") or 0.0,
        "smDirection": thesis.get("sm_direction") or "NONE",
        "smTiltPct": thesis.get("sm_tilt_pct") or 0.0,
        "volRising": bool(thesis.get("vol_rising")),
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
            score=min(thesis["score"] / 8.0, 1.0),
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
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    whitelist = config.get("whitelist", DEFAULT_WHITELIST)

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_lynx_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_lynx_producer_version": VERSION})
        return

    state = cfg.read_lynx_state()
    initial_min = int(config.get("initialMinScore", DEFAULT_INITIAL_MIN_SCORE))
    if state.get("current_min_score") is None:
        state["current_min_score"] = initial_min

    now = time.time()
    # Run audit if due (the self-tuning step)
    state, audit_summary = run_audit_if_due(config, state, now)
    if audit_summary is not None:
        cfg.write_lynx_state(state)

    current_min_score = int(state.get("current_min_score", initial_min))

    candidates = []
    for asset in whitelist:
        a = str(asset).upper()
        if a in held_set or cfg.was_recently_signaled(a):
            continue
        thesis = build_thesis(a, current_min_score, config)
        if thesis:
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": f"WAITING — no asset cleared current MIN_SCORE={current_min_score}",
            "current_min_score": current_min_score,
            "audit": audit_summary,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_lynx_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: (c["score"], abs(c["trend_4h_pct"])), reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.20))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(best, margin_usd, leverage, held_assets, current_min_score)
    if pushed:
        cfg.record_signal(best["coin"])

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["coin"],
            "direction": best["direction"],
            "score": best["score"],
            "current_min_score": current_min_score,
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "audit": audit_summary,
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_lynx_producer_version": VERSION,
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
        name=f"lynx-producer-{_wallet_lock_id}",
        tick_timeout=240,
    )
