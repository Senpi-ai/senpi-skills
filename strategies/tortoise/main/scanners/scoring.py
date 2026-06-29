"""TORTOISE — pure cadence/selection math (no I/O, no MCP, no clock side-effects).

A faithful Runtime 3.0 port of the v2 Tortoise producer's DCA-scheduler logic
(tortoise-producer.py v1.0.1). The cadence math is reproduced VERBATIM so a
fidelity harness can diff this against the v2 producer on the same history map +
`now`. Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`.

There is NO price scoring in Tortoise — "scoring" here means the DCA clock:
which whitelisted asset is most overdue past its interval. `now` and the
per-asset last-DCA history are passed IN by the caller (scan.py reads them from
ctx.state), keeping this module pure and unit-testable on plain dicts.

FIDELITY NOTES vs tortoise-producer.py v1.0.1:
  - seconds_since / is_dca_due / pick_next_dca_asset / next_due_seconds are
    ported VERBATIM (same >=, same never-DCA'd-wins sentinel, same case
    normalization, same future-timestamp clamp-to-0 clock-skew guard).
  - The v2 producer also clamped a future timestamp to 0.0 in seconds_since
    (max(0.0, now - last)); preserved.
"""


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def seconds_since(last_dca_ts, now_ts):
    """Seconds elapsed since the last DCA event. None for unknown (never-DCA'd)
    assets — they should be treated as MAXIMALLY overdue. Verbatim from v2.

    v2-quirk: a future timestamp clamps to 0.0 (clock-skew safety)."""
    if last_dca_ts is None:
        return None
    try:
        return max(0.0, float(now_ts) - float(last_dca_ts))
    except (TypeError, ValueError):
        return None


def is_dca_due(elapsed_sec, interval_sec):
    """True if the DCA interval has elapsed OR this asset has never been DCA'd
    (elapsed_sec=None). Never-DCA'd assets are always due. Verbatim from v2.

    v2-quirk: threshold is `>=`, not `>` (an asset exactly at the interval is
    due)."""
    if elapsed_sec is None:
        return True
    return elapsed_sec >= interval_sec


def pick_next_dca_asset(assets, last_dca_by_asset, interval_sec, now_ts):
    """Among `assets`, pick the one most overdue (longest since last DCA, past
    the interval). Never-DCA'd assets win over any DCA'd asset (infinite-overdue
    sentinel). Returns the upper-cased asset symbol or None if nothing is due.
    Verbatim from v2 pick_next_dca_asset (case normalized to upper)."""
    best_asset, best_elapsed = None, -1.0
    for asset in assets:
        key = str(asset).upper()
        last = last_dca_by_asset.get(key)
        elapsed = seconds_since(last, now_ts)
        if not is_dca_due(elapsed, interval_sec):
            continue
        # Never-DCA'd asset (elapsed is None) -> treat as infinitely overdue so
        # it wins over any DCA'd asset. Sentinel sortable above any real elapsed.
        rank = float('inf') if elapsed is None else elapsed
        if rank > best_elapsed:
            best_elapsed = rank
            best_asset = key
    return best_asset


def next_due_seconds(assets, history, interval_sec, now_ts):
    """Seconds until the next eligible asset comes due (for the WAITING
    diagnostic). 0 if any asset is already past-due; None only if `assets` is
    empty. Verbatim from v2 next_due_seconds."""
    soonest = None
    for asset in assets:
        last = history.get(str(asset).upper())
        if last is None:
            return 0
        wait = (interval_sec - (now_ts - last))
        if wait <= 0:
            return 0
        if soonest is None or wait < soonest:
            soonest = wait
    return soonest
