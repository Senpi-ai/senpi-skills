"""CUCKOO — pure meta-consensus math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Cuckoo producer's pure functions
(cuckoo-producer.py v1.0.1 — `performance_weight`, `mirror_direction`,
`position_asset`, `position_notional`, `tally_consensus`, `consensus_score`).
The math is reproduced VERBATIM so a fidelity harness can diff this against the
v2 producer on the same snapshot. scan.py does the reads (top-strategies +
per-strategy positions) and hands plain lists/dicts to these functions.

Thesis (copy-the-copiers / copy_trading · copy_the_copiers):
  Each top strategy casts ONE vote per (asset, direction) it holds above
  minNotionalUsd, weighted by performance_weight(roi) = clamp(1 + roi/50,
  0.5, weightCap). Votes are tallied into weighted (asset, direction)
  candidates; entry requires >= minStrategies agreeing. A stronger strategy
  gets more say (capped so one outlier can't dominate)."""


# v2 producer constants (cuckoo-producer.py)
DEFAULT_WEIGHT_CAP = 3.0       # max per-strategy weight (outlier guard)
DEFAULT_HIGH_WEIGHT = 6.0      # aggregate weight that earns the bonus point


def safe_float(v, default=0.0):
    """Verbatim from v2 safe_float."""
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


def performance_weight(roi_pct, cap=DEFAULT_WEIGHT_CAP):
    """Map a strategy's ROI% to a follow weight in [0.5, cap]. A flat strategy
    weighs 1.0; a +100% strategy hits the cap; a losing strategy floors at
    0.5 (it still counts a little, but barely). Verbatim from v2."""
    w = 1.0 + (safe_float(roi_pct) / 50.0)
    if w < 0.5:
        return 0.5
    if w > cap:
        return cap
    return w


def mirror_direction(pos):
    """LONG / SHORT for a position (explicit direction/side field, else szi
    sign). None if undeterminable. Verbatim from v2."""
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
    """Asset symbol for a position (multi-key fallback). Verbatim from v2.

    Preserves the symbol as returned by the upstream strategy's position
    (e.g. an `xyz:` prefix survives). Case is uppercased, matching v2."""
    if not isinstance(pos, dict):
        return ""
    return str(pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))).upper()


def position_notional(pos):
    """USD notional for a position (size*entry, else marginUsed, else size).
    Verbatim from v2."""
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
    (asset, direction) -> {"asset","direction","count","weight"}. Verbatim."""
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
    """Score a weighted-consensus candidate (max ~6). Verbatim from v2.

      +2 held by at least one top strategy
      +3 if 4+ strategies agree, +2 if 3, +1 if 2
      +1 if aggregate weight >= high_weight"""
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


def gather_entries(strategies, positions_by_wallet, min_notional, cap):
    """One weighted vote per (strategy, asset, direction) for every qualifying
    position the top strategies hold. Port of v2 gather_entries — but the MCP
    fetch is hoisted into scan.py (read-guarded there) and the already-fetched
    positions are passed in via `positions_by_wallet` (wallet -> [positions]).

    `strategies` = [{"wallet", "roi"}]. Returns the entries list for
    tally_consensus()."""
    entries = []
    for strat in strategies:
        wallet = strat["wallet"]
        weight = performance_weight(strat["roi"], cap)
        positions = positions_by_wallet.get(wallet, [])
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
