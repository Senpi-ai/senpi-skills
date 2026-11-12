"""TORTOISE — pure schedule/selection math (no I/O, no MCP, no clock side-effects).

A faithful Runtime 3.0 port of the v2 Tortoise producer's scheduler logic
(tortoise-producer.py v1.0.1). The cadence math is reproduced VERBATIM so a
fidelity harness can diff this against the v2 producer on the same anchor map +
`now`. Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`.

There is NO price scoring in Tortoise — "scoring" here means the schedule clock:
which whitelisted asset is most overdue past its interval. `now` and the
per-asset anchor map are passed IN by the caller (scan.py reads them from
ctx.state), keeping this module pure and unit-testable on plain dicts.

2.0.0 — the clock is keyed to the last EXIT, not the last buy. 1.x handed
`pick_next_dca_asset` the buy-time map, so a name that had been held longer than
the interval was "overdue" the moment its stop fired and was bought straight
back on the next tick. `schedule_anchor` now builds the map the picker reads
from the LATEST of each name's last buy and last observed exit; `detect_exits`
is the pure half of "held at the last good read, flat now". The verbatim v2
functions below are unchanged — only the map they are handed differs.

FIDELITY NOTES vs tortoise-producer.py v1.0.1:
  - seconds_since / is_dca_due / pick_next_dca_asset / next_due_seconds are
    ported VERBATIM (same >=, same never-bought-wins sentinel, same case
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
    """Seconds elapsed since the anchor event (last buy or last exit). None for
    unknown (never-bought) assets — they should be treated as MAXIMALLY overdue.
    Verbatim from v2.

    v2-quirk: a future timestamp clamps to 0.0 (clock-skew safety)."""
    if last_dca_ts is None:
        return None
    try:
        return max(0.0, float(now_ts) - float(last_dca_ts))
    except (TypeError, ValueError):
        return None


def is_dca_due(elapsed_sec, interval_sec):
    """True if the interval has elapsed OR this asset has never been bought
    (elapsed_sec=None). Never-bought assets are always due. Verbatim from v2.

    v2-quirk: threshold is `>=`, not `>` (an asset exactly at the interval is
    due)."""
    if elapsed_sec is None:
        return True
    return elapsed_sec >= interval_sec


def pick_next_dca_asset(assets, last_dca_by_asset, interval_sec, now_ts):
    """Among `assets`, pick the one most overdue (longest since its anchor, past
    the interval). Never-bought assets win over any bought asset (infinite-overdue
    sentinel). Returns the upper-cased asset symbol or None if nothing is due.
    Verbatim from v2 pick_next_dca_asset (case normalized to upper). 2.0.0 hands
    it the `schedule_anchor` map instead of the raw buy-time map."""
    best_asset, best_elapsed = None, -1.0
    for asset in assets:
        key = str(asset).upper()
        last = last_dca_by_asset.get(key)
        elapsed = seconds_since(last, now_ts)
        if not is_dca_due(elapsed, interval_sec):
            continue
        # Never-bought asset (elapsed is None) -> treat as infinitely overdue so
        # it wins over any bought asset. Sentinel sortable above any real elapsed.
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


# ═══════════════════════════════════════════════════════════════
# 2.0.0 — the clock restarts at the EXIT
# ═══════════════════════════════════════════════════════════════

def detect_exits(prev_held, held_now, exits, now_ts):
    """Pure: names held at the previous GOOD read that are flat now have exited
    (the DSL closed them). Returns (new_exits_map, [exited names, sorted]).
    Stamped at the observing tick — the close happened somewhere inside the last
    interval, so the re-buy wait is at most one tick longer than configured."""
    prev = {str(h).upper() for h in (prev_held or [])}
    now_set = {str(h).upper() for h in (held_now or [])}
    out = dict(exits or {})
    exited = sorted(prev - now_set)
    for name in exited:
        out[name] = float(now_ts)
    return out, exited


def schedule_anchor(history, exits):
    """Pure: {ASSET: epoch} the picker reads — the LATEST of each name's last buy
    and last observed exit. A name in neither map is absent (never bought ->
    always due). A name bought but never seen held keeps its buy time, so an
    order the venue refused is retried once per interval, not every tick."""
    out = {}
    for src in (history or {}, exits or {}):
        for k, v in src.items():
            key = str(k).upper()
            ts = _f(v, None)
            if ts is None:
                continue
            if key not in out or ts > out[key]:
                out[key] = ts
    return out
