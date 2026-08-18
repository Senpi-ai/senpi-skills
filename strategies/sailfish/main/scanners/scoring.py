"""SAILFISH — pure relative-strength thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Sailfish producer's pure logic
(`relative_strength`, `rank_assets`, `leader_above_runner_up`) plus the inline
score builder from v2 `main()`. The math/indexing is reproduced VERBATIM so a
fidelity harness can diff this against the v2 producer on the same market
snapshot.

Sailfish ranks the whitelist (BTC/ETH/SOL/HYPE) by 4h relative strength each
tick and longs the leader iff (a) the leader's own RS >= minLeaderRsPct AND
(b) the leader beats the runner-up by >= leaderMarginPct (no whipsaw on tight
races). Single-position; the producer never closes — rotation is realized via
the DSL trail's natural exit + the next tick's re-evaluation.

`scan.py` does the MCP reads + state; this module stays pure and unit-testable
on plain close lists.
"""


# ── candle close accessor (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts via _f(c, "close", "c"); the list branch is defensive and never
# fires on dict candles, so it does not change v2 behaviour.

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    if isinstance(c, dict):
        val = c.get("close")
        if val is None:
            val = c.get("c")
        return _f(val if val is not None else 0.0)
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])
    return 0.0


# ── pure relative-strength logic (ported VERBATIM from v2 sailfish-producer.py) ──

def relative_strength(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    Used as a single-asset RS proxy; ranking sorts these across the universe.
    None if insufficient data or ref price is non-positive. Verbatim from v2."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def rank_assets(strength_by_asset):
    """Sort {asset: strength} by strength descending. Assets with None
    strength are dropped. Returns list of (asset, strength). Verbatim from v2."""
    pairs = [(a, s) for a, s in strength_by_asset.items() if s is not None]
    pairs.sort(key=lambda t: t[1], reverse=True)
    return pairs


def leader_above_runner_up(ranked, min_leader_rs_pct, margin_pct):
    """Given a ranked list of (asset, strength), return the leader iff:
      1. leader's own RS >= min_leader_rs_pct (leader must actually be up), AND
      2. leader - runner_up >= margin_pct (clean separation, no whipsaw).
    If there's only one ranked asset, the margin requirement defaults to met.
    Returns (asset, strength, margin_vs_runner_up) or None. Verbatim from v2."""
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


# ── score builder (ported VERBATIM from v2 main()) ──

def build_score(leader_asset, leader_rs, margin_vs_runner, has_held):
    """Port of v2 main()'s inline scoring. Returns (score, reasons).

    Base 3 (leader cleared both gates) + 1 if leader_rs >= 4.0 (leader_strong)
    + 1 if margin_vs_runner >= 3.0 (dominant_margin). A `post_dsl_re_entry`
    reason flag is appended when there is an existing/held position (re-entering
    after a prior DSL exit) — informational only, no score effect. Max ~5.
    Verbatim from v2 (margin gates/cutoffs preserved)."""
    score = 3   # base — leader cleared both RS and margin gates
    reasons = [
        f"{leader_asset} leads the pack at {leader_rs:+.2f}%",
        f"ahead of the runner-up by {margin_vs_runner:+.2f} points",
    ]
    if leader_rs >= 4.0:
        score += 1
        reasons.append("the leader's move is strong")
    if margin_vs_runner >= 3.0:
        score += 1
        reasons.append("a dominant lead over the field")
    if has_held:
        # Re-entering after a prior position exited — leadership has either
        # confirmed the holdover OR rotated. Either way it's a fresh decision.
        reasons.append("re-entering after a prior exit")
    return score, reasons
