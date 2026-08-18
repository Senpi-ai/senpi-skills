"""ALBATROSS — supervised scanner (Runtime 3.0 port of the v2 Arena conviction mirror).

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - refreshes the conviction-weighted LEADER POOL (cached in ctx.state with a
    leaderRefreshHours TTL; 4 weekly + 1 monthly arena_leaderboard reads only on
    refresh), degrading to the cached pool whenever a refresh read fails — a cohort
    read failure NEVER crashes the tick and NEVER empties an existing pool,
  - for each pooled leader: resolves strategy wallets (strategy_list), pulls current
    positions (discovery_get_trader_state), and diffs against the per-leader snapshot
    in ctx.state to detect NEW positions,
  - emits one MIRROR signal per new position: asset+direction COPIED from the leader,
    a fixed conviction-pool marginPct INTENT (PERCENT) + capped leverage, top-level.

Read-only + single-pass. No daemon, no push_signal, no create_position, no cancel_order.
Derived universe — the assets come from the leaders' positions, not a fixed list.
The runtime sizes the dollars, owns slots/cooldowns/risk gates, and trails the DSL exit.

FIDELITY NOTES vs albatross-producer.py v1.0.1:
  - SIZING: v2 stored marginPct=0.15 as a FRACTION and computed marginUsd =
    accountValue * 0.15 in the producer. This port emits `marginPct` as a PERCENT
    (15) at the TOP level and lets the runtime size (marginPct/100)*withdrawable.
    A defensive guard treats any inputs.marginPct <= 1.0 as a pasted fraction and
    x100s it. The SIZE is identical for the default config; only WHO multiplies
    moves to the runtime (per the 3.0 contract).
  - DEDUP: v2 recent-signals.json (TTL 240s, key `<COIN>:<DIRECTION>`) -> ctx.state
    `signaled` map, same TTL + 4xTTL prune semantics.
  - POOL CACHE: v2 state/leader-pool.json (leaderRefreshHours TTL) -> ctx.state
    `pool` record (refreshed_at + leaders). Same TTL; degrade-to-cache on refresh
    read failure is preserved (v2 compute_conviction_pool returned [] on a failed
    week read; this port instead KEEPS the prior cached pool, matching the gold
    cohort peers — a strictly safer degrade that never blanks a live pool).
  - PER-LEADER SNAPSHOTS: v2 state/leader-positions/<uid>.json -> ctx.state
    `leader_snapshots` map (uid -> list of position keys). Same diff semantics.
  - DROPPED (v2 -> 3.0): the producer's push_signal + record-on-disk are gone (the
    runtime ingests scan()'s return). v2 had NO order-lifecycle mutations to drop.
    The producer's `_get_current_week_number` + explicit per-week-number fetch
    collapses to "pull the current week + the 3 prior periods" via period_number
    offsets when available; if the API doesn't expose periodNumber we fall back to
    the default current-period weekly read repeated is avoided — we degrade to the
    single current-week snapshot (weeks_traded floor then naturally blocks until
    more weekly history accrues). FLAGGED.
  - leverage clamped to MAX_LEVERAGE (5) verbatim; betashop xHandle excluded by
    default (config.excludeXHandles).
"""

import sys
import time

import scoring

VERSION = "1.0.0"

_MAX_LEVERAGE = 5            # v2 MAX_LEVERAGE
_DEFAULT_LEVERAGE = 5        # v2 DEFAULT_LEVERAGE
_DEFAULT_RECENT_TTL = 240    # v2 RECENT_SIGNAL_TTL_SEC (race-window dedup)
_DEFAULT_POOL_SIZE = 5       # v2 DEFAULT_POOL_SIZE
_DEFAULT_REFRESH_HOURS = 4   # v2 DEFAULT_REFRESH_HOURS
_DEFAULT_MIN_WEEKS = 3       # v2 DEFAULT_MIN_WEEKS_TRADED
_DEFAULT_MIN_WEEKLY_VOL = 50000  # v2 DEFAULT_MIN_WEEKLY_VOL_USD


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back
    the whole tick (the contract turns any propagated exception into an empty tick).
    Returns None on failure so the existing degrade paths apply (pool -> cached pool;
    a failed per-leader read -> that leader is skipped this tick)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[albatross.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _entries(resp):
    """arena_leaderboard -> entries list, defensively unwrapping data/leaderboard."""
    if not resp:
        return []
    if isinstance(resp, dict) and resp.get("success") is False:
        return []
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    lb = data.get("leaderboard", data) if isinstance(data, dict) else data
    if isinstance(lb, dict):
        ents = lb.get("entries", [])
    elif isinstance(lb, list):
        ents = lb
    else:
        ents = []
    return ents if isinstance(ents, list) else []


def _period_number(resp):
    if not resp:
        return None
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    lb = data.get("leaderboard", data) if isinstance(data, dict) else data
    if isinstance(lb, dict):
        return lb.get("periodNumber")
    return None


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across main/xyz
    (two views of ONE cross-margined wallet — summing double-counts shared free
    balance -> 2x sizing). assetPositions are per-sub-DEX so enumerated across both.
    Ported verbatim from v2 cfg.get_positions, incl. the read-sanity guard (margin
    in use + empty positions -> skip tick)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
            })

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; running held-asset dedup off that re-enters held
    # names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[albatross.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _refresh_pool(ctx, inputs, now):
    """Pull the current week + 3 prior weekly leaderboards + the current month, and
    compose the conviction pool via scoring.build_pool. READ-GUARDED throughout;
    if NO weekly snapshot can be read the caller keeps the cached pool."""
    # Resolve the current week's period number from a small qualified read.
    cur_resp = _read(ctx, "arena_leaderboard",
                     {"period_type": "WEEK", "qualified": True, "limit": 50})
    current_week = _period_number(cur_resp)

    week_lists = []
    cur_entries = _entries(cur_resp)
    if cur_entries:
        week_lists.append(cur_entries)

    if current_week is not None:
        # Pull the 3 prior weeks by explicit period_number (verbatim v2 offset loop).
        for offset in range(1, 4):
            wk = current_week - offset
            if wk <= 0:
                continue
            resp = _read(ctx, "arena_leaderboard",
                         {"period_type": "WEEK", "qualified": True, "limit": 50,
                          "period_number": wk})
            ents = _entries(resp)
            if ents:
                week_lists.append(ents)
    else:
        # FLAGGED degrade: API didn't expose periodNumber. We only have the current
        # week; weeks_traded floor then blocks until more weekly history accrues
        # across future refreshes (no double-counting the same week).
        print("[albatross.scan] periodNumber unavailable — pool from current week only "
              "(weeks_traded floor will gate until more history)", file=sys.stderr)

    if not week_lists:
        return None  # signal "no usable weekly read" -> caller keeps cached pool

    month_resp = _read(ctx, "arena_leaderboard",
                       {"period_type": "MONTH", "qualified": True, "limit": 50})
    month_entries = _entries(month_resp)

    leaders = scoring.build_pool(week_lists, month_entries, inputs)
    return {"refreshed_at": now, "leaders": leaders, "weeks_pulled": len(week_lists)}


def _get_pool(ctx, inputs, now, cached_pool):
    """Return (pool_record, refreshed_bool). Serves the cached pool while fresh;
    on a stale cache attempts a refresh; on a failed refresh DEGRADES to the cached
    pool (never blanks a live pool) per the gold cohort pattern."""
    refresh_hours = scoring._f(inputs.get("leaderRefreshHours", _DEFAULT_REFRESH_HOURS),
                               _DEFAULT_REFRESH_HOURS)
    if (cached_pool and cached_pool.get("leaders")
            and (now - cached_pool.get("refreshed_at", 0)) < refresh_hours * 3600):
        return cached_pool, False
    fresh = _refresh_pool(ctx, inputs, now)
    if fresh is None:
        # refresh read failed entirely — keep whatever we had
        return (cached_pool or {"refreshed_at": 0, "leaders": []}), False
    return fresh, True


def _leader_wallets(ctx, senpi_user_id):
    """senpiUserId -> [strategy wallet addresses]. READ-GUARDED (verbatim v2)."""
    raw = _read(ctx, "strategy_list",
                {"userIds": [senpi_user_id], "status": ["ACTIVE"]})
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    strategies = data.get("strategies", data) if isinstance(data, dict) else data
    if not isinstance(strategies, list):
        return []
    return [s.get("strategyWalletAddress") for s in strategies
            if isinstance(s, dict) and s.get("strategyWalletAddress")]


def _leader_positions(ctx, wallets):
    """Current open positions across all of a leader's strategy wallets. READ-GUARDED."""
    if not wallets:
        return []
    raw = _read(ctx, "discovery_get_trader_state",
                {"trader_addresses": wallets, "latest": True})
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    states = data.get("traders", data) if isinstance(data, dict) else data
    if not isinstance(states, list):
        return []
    return scoring.normalize_positions(states)


# ── ctx.state: pool cache + per-leader snapshots + recent-signal dedup ──

def _load_state(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}, {}, {}
    last = ctx.state.last() or {}
    pool = last.get("pool", {}) if isinstance(last.get("pool"), dict) else {}
    snaps = last.get("leader_snapshots", {}) if isinstance(last.get("leader_snapshots"), dict) else {}
    signaled = last.get("signaled", {}) if isinstance(last.get("signaled"), dict) else {}
    return dict(pool), dict(snaps), dict(signaled)


def _prune_signaled(signaled, ttl, now):
    cutoff = now - (ttl * 4)  # verbatim v2 record_signal prune window
    return {k: v for k, v in signaled.items() if scoring._f(v, 0) >= cutoff}


def _was_recently_signaled(signaled, key, ttl, now):
    last = signaled.get(key.upper())
    if last is None:
        return False
    return (now - scoring._f(last, 0)) < ttl


def scan(inputs, ctx):
    now = time.time()
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL), _DEFAULT_RECENT_TTL)

    # marginPct INTENT as a PERCENT in (0,100]. Defensive: a value <= 1.0 is a
    # pasted v2 FRACTION (v2 stored 0.15) -> x100 (-> 15). (dire/koala guard.)
    margin_pct = scoring._f(inputs.get("marginPct", 15), 15.0)
    if margin_pct <= 1.0:
        margin_pct = margin_pct * 100.0

    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)

    # config bundle passed to the pure pool builder
    pool_cfg = {
        "convictionWeights": inputs.get("convictionWeights", {
            "monthlyRoe": 0.3, "weeklyRoeMean": 0.7, "weeklyRoeStdevPenalty": 0.5}),
        "minWeeksTraded": int(inputs.get("minWeeksTraded", _DEFAULT_MIN_WEEKS)),
        "minWeeklyVolumeUsd": scoring._f(inputs.get("minWeeklyVolumeUsd", _DEFAULT_MIN_WEEKLY_VOL),
                                         _DEFAULT_MIN_WEEKLY_VOL),
        "excludeXHandles": inputs.get("excludeXHandles", ["betashop"]),
        "leaderPoolSize": int(inputs.get("leaderPoolSize", _DEFAULT_POOL_SIZE)),
    }

    account_value, positions = _get_account(ctx)
    cached_pool, leader_snapshots, signaled = _load_state(ctx)
    signaled = _prune_signaled(signaled, ttl, now)

    def _persist(pool_rec, result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({
                "pool": pool_rec,
                "leader_snapshots": leader_snapshots,
                "signaled": signaled,
                "result": result,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[albatross.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    if account_value <= 0:
        result = {"ts": now, "emitted": False, "note": "no account value"}
        _persist(cached_pool, result)
        print("[albatross.scan] WAITING — no account value", file=sys.stderr)
        return []

    held_set = {p["coin"].upper() for p in positions if p.get("coin")}
    held_assets = [p["coin"] for p in positions if p.get("coin")]

    pool_rec, refreshed = _get_pool(ctx, pool_cfg, now, cached_pool)
    leaders = pool_rec.get("leaders", []) if isinstance(pool_rec, dict) else []

    if not leaders:
        result = {"ts": now, "emitted": False, "pool_size": 0,
                  "note": "no qualifying leaders (conviction>0, >=min weeks)"}
        _persist(pool_rec, result)
        print(f"[albatross.scan] WAITING — empty leader pool (refreshed={refreshed})",
              file=sys.stderr)
        return []

    out = []
    pushed = []
    for leader in leaders:
        uid = leader.get("senpiUserId")
        if not uid:
            continue
        wallets = _leader_wallets(ctx, uid)
        if not wallets:
            continue
        current = _leader_positions(ctx, wallets)
        last_keys = set(leader_snapshots.get(str(uid), []))
        new_positions = scoring.detect_new(current, last_keys)
        # Update this leader's snapshot every tick regardless (verbatim v2 semantics).
        leader_snapshots[str(uid)] = [scoring.position_key(p) for p in current]

        for pos in new_positions:
            coin = pos["coin"]
            direction = pos["direction"]
            if coin.upper() in held_set:           # don't re-mirror a name we already hold
                continue
            dedup_key = f"{coin}:{direction}"
            if _was_recently_signaled(signaled, dedup_key, ttl, now):
                continue
            out.append({
                "asset": coin,
                "direction": direction,
                "marginPct": round(margin_pct, 4),   # PERCENT in (0,100] — runtime sizes the dollars
                "leverage": leverage,                # <= MAX_LEVERAGE; runtime applies it
                "data": {
                    "score": scoring.wire_score(leader.get("conviction_score")),
                    "direction": direction,
                    "signalKind": "ARENA_MIRROR",
                    "leaderUsername": leader.get("username"),
                    "leaderXHandle": leader.get("xHandle"),
                    "leaderConvictionScore": scoring._f(leader.get("conviction_score"), 0.0),
                    "leaderWeeksTraded": int(leader.get("weeks_traded") or 0),
                    "leaderWeeklyRoeMean": scoring._f(leader.get("weekly_mean_roe"), 0.0),
                    "leaderMonthlyRoe": scoring._f(leader.get("monthly_roe"), 0.0),
                    "leaderEntryPrice": scoring._f(pos.get("entryPx"), 0.0),
                    "leaderLeverage": scoring._f(pos.get("leverage"), 0.0),
                    "reasons": [
                        f"mirroring {leader.get('username')}'s new {coin} {direction}",
                        f"conviction score {leader.get('conviction_score')}",
                        f"weekly return averaged {leader.get('weekly_mean_roe')}% over {leader.get('weeks_traded')} weeks",
                        f"monthly return {leader.get('monthly_roe')}%",
                    ],
                    "heldAssets": held_assets,
                },
            })
            signaled[dedup_key.upper()] = now
            pushed.append({"coin": coin, "direction": direction,
                           "from": leader.get("username"),
                           "conviction": leader.get("conviction_score")})

    result = {"ts": now, "emitted": bool(out), "pool_size": len(leaders),
              "signals": len(out), "pushed": pushed, "held": held_assets,
              "pool_refreshed": refreshed}
    _persist(pool_rec, result)

    if out:
        print(f"[albatross.scan] EMIT {len(out)} mirror(s) from pool of {len(leaders)} "
              f"leaders: {[p['coin'] + ' ' + p['direction'] for p in pushed]}", file=sys.stderr)
    else:
        print(f"[albatross.scan] WAITING — no new leader positions; pool={len(leaders)} "
              f"held={held_assets} (refreshed={refreshed})", file=sys.stderr)
    return out
