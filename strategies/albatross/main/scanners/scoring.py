"""ALBATROSS — pure multi-week Arena conviction math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Albatross v1.0.x producer
(albatross-producer.py). Given the raw arena_leaderboard entry lists already
fetched by scan.py (4 weekly snapshots + 1 monthly), this composes the
per-trader conviction pool VERBATIM so a fidelity harness can diff it against
the v2 producer on the same snapshot.

The thesis: the Senpi Arena resets weekly, so single-week mirroring is a
survivor-bias trap. Score each trader by a composite that rewards multi-week
persistence and penalizes weekly variance:

    conviction = 0.3 * monthly_roe
               + 0.7 * mean(weekly_roe across last 4 weeks)
               - 0.5 * pstdev(weekly_roe)

Filter to conviction > 0, weeks_traded >= minWeeksTraded, each appearance's
notionalVolume >= minWeeklyVolumeUsd, xHandle not excluded. Top-N by conviction.
The pool is the cohort; scan.py then diffs each pooled leader's open positions
tick-over-tick and mirrors NEW ones.
"""

import statistics


def _f(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


# ── conviction-weighted leader pool (verbatim compute_conviction_pool) ──

def build_pool(week_entry_lists, month_entries, config):
    """Compose the top-N conviction pool.

    week_entry_lists: list of arena_leaderboard `entries` lists, one per week
        (current + last 3). Each entry is a raw leaderboard dict.
    month_entries: the current-month arena_leaderboard `entries` list.
    config: inputs dict (weights, floors, exclude set, pool size).

    Returns a list of leader dicts, best conviction first, capped to pool_size.
    Pure: callers pass the already-fetched leaderboard rows; no clock/MCP here.
    """
    weights = config.get("convictionWeights", {}) or {}
    w_monthly = _f(weights.get("monthlyRoe", 0.3), 0.3)
    w_weekly_mean = _f(weights.get("weeklyRoeMean", 0.7), 0.7)
    w_stdev_penalty = _f(weights.get("weeklyRoeStdevPenalty", 0.5), 0.5)
    min_weeks_traded = int(config.get("minWeeksTraded", 3))
    min_weekly_vol = _f(config.get("minWeeklyVolumeUsd", 50000), 50000.0)
    excluded_handles = {str(h).lower() for h in config.get("excludeXHandles", ["betashop"])}
    pool_size = int(config.get("leaderPoolSize", 5))

    # Aggregate per senpiUserId across the weekly snapshots. Each snapshot is a
    # distinct week, so a uid appearing in k snapshots = traded k of the 4 weeks.
    # (v2 keyed weekly_roe_by_week by week_num; here each list is one week, so
    # we accumulate a roe per appearance — identical math, snapshot-indexed.)
    by_uid = {}
    for week_idx, entries in enumerate(week_entry_lists):
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            uid = e.get("senpiUserId")
            if not uid:
                continue
            handle = str(e.get("xHandle") or "").lower()
            if handle in excluded_handles:
                continue
            vol = _f(e.get("notionalVolume", 0))
            if vol < min_weekly_vol:
                continue
            roe = _f(e.get("roePct", 0))
            snap = by_uid.setdefault(uid, {
                "senpiUserId": uid,
                "username": e.get("senpiUserName"),
                "xHandle": e.get("xHandle"),
                "weekly_roe_by_week": {},
                "totalPnl": _f(e.get("totalPnl", 0)),
                "currentBalance": _f(e.get("currentBalance", 0)),
            })
            snap["weekly_roe_by_week"][str(week_idx)] = roe

    monthly_roe_by_uid = {
        e.get("senpiUserId"): _f(e.get("roePct", 0))
        for e in (month_entries or [])
        if isinstance(e, dict) and e.get("senpiUserId")
    }

    candidates = []
    for uid, snap in by_uid.items():
        weekly_roes = list(snap["weekly_roe_by_week"].values())
        if len(weekly_roes) < min_weeks_traded:
            continue
        weekly_mean = statistics.mean(weekly_roes)
        weekly_std = statistics.pstdev(weekly_roes) if len(weekly_roes) > 1 else 0
        monthly_roe = monthly_roe_by_uid.get(uid, 0.0)
        conviction = (
            w_monthly * monthly_roe
            + w_weekly_mean * weekly_mean
            - w_stdev_penalty * weekly_std
        )
        if conviction <= 0:
            continue
        candidates.append({
            "senpiUserId": uid,
            "username": snap["username"],
            "xHandle": snap["xHandle"],
            "totalPnl": snap["totalPnl"],
            "currentBalance": snap["currentBalance"],
            "weekly_roe": weekly_roes,
            "weekly_mean_roe": round(weekly_mean, 3),
            "weekly_stdev_roe": round(weekly_std, 3),
            "monthly_roe": round(monthly_roe, 3),
            "conviction_score": round(conviction, 3),
            "weeks_traded": len(weekly_roes),
        })

    candidates.sort(key=lambda c: c["conviction_score"], reverse=True)
    return candidates[:pool_size]


# ── leader position normalisation + new-position diff ──

def normalize_positions(states):
    """Flatten discovery_get_trader_state `traders` into mirror-able positions.

    Verbatim port of v2 fetch_leader_positions' inner loop: one row per non-zero
    open position, direction from szi sign, leverage unwrapped from the nested
    {value:..} shape the API sometimes returns.
    """
    positions = []
    for state in states or []:
        if not isinstance(state, dict):
            continue
        for pos in state.get("openPositions", []) or []:
            if not isinstance(pos, dict):
                continue
            coin = pos.get("coin") or pos.get("asset")
            szi = _f(pos.get("szi", 0))
            if not coin or szi == 0:
                continue
            lev_raw = pos.get("leverage")
            if isinstance(lev_raw, dict):
                lev = _f(lev_raw.get("value", 1), 1.0)
            else:
                lev = _f(lev_raw, 1.0)
            positions.append({
                "wallet": state.get("traderAddress"),
                "coin": coin,
                "direction": "LONG" if szi > 0 else "SHORT",
                "size": abs(szi),
                "entryPx": _f(pos.get("entryPx", 0)),
                "leverage": lev,
            })
    return positions


def position_key(p):
    """Dedup key for a leader position: `<COIN>:<DIRECTION>` (verbatim v2)."""
    return f"{p.get('coin')}:{p.get('direction')}"


def detect_new(current_positions, last_keys):
    """Positions present now but absent from the last snapshot's key set.

    `last_keys` is a set of position_key()s from the previous tick. Verbatim port
    of v2 detect_new_positions (which read the snapshot off disk; here scan.py
    carries it in ctx.state)."""
    last = set(last_keys or [])
    return [p for p in current_positions if position_key(p) not in last]


# ── sizing + wire score ──

def wire_score(conviction_score):
    """Normalise the composite conviction into the [0,1] wire score the v2
    producer emitted: min(conviction / 50, 1.0). Carried on data{} only."""
    return round(min(_f(conviction_score) / 50.0, 1.0), 4)
