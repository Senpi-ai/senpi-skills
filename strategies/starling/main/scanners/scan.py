"""STARLING — Smart-Money Rotation Follower. The single supervised scanner.

Follows a cohort of proven-profitable Hyperliquid wallets and opens WITH them only
when several are FRESHLY piling into the same name in the same direction at the same
time (consensus forming = rotation) — not a name that has stood at consensus for a
while. Conviction/size scales with how many cohort wallets agree. NEVER closes — the
DSL owns every exit (rotate-by-attrition, exactly like gibbon).

Each tick:
  1) SLOW CLOCK — refresh the smart cohort every cohortRefreshHours (or first tick):
     paginated discovery_get_top_traders (ALL_TIME, realized), wallets with realized
     PnL >= smartMinRealizedUsd, capped at cohortCap. If derivation returns empty,
     KEEP the prior cohort (never wipe).
  2) Snapshot every cohort wallet's OPEN positions (discovery_get_trader_state,
     batched) -> current consensus counts (distinct wallets per asset/direction).
  3) DIFF this snapshot against the previous one -> fresh picks (consensus newly
     formed or still rising). For each fresh pick not already held and not signalled
     within recentSignalTtlSeconds, size by the agreement band and emit an OPEN.
  4) Persist cohort + snapshot + recent. NEVER closes.

Read-only + single-pass. marginPct is a PERCENT in (0,100]. No daemon.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[starling.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _cohort(ctx, inputs):
    """Derive the SMART cohort: paginated discovery_get_top_traders (ALL_TIME, sorted
    by realized PnL desc), keeping wallets with realized PnL >= smartMinRealizedUsd,
    capped at cohortCap. Returns a list of lower-cased addresses ([] on total failure —
    the caller then keeps the prior cohort). Ported verbatim-in-spirit from gibbon."""
    smin = scoring._f(inputs.get("smartMinRealizedUsd"), 1_000_000)
    cap = int(scoring._f(inputs.get("cohortCap"), 120))
    psize = int(scoring._f(inputs.get("pageSize"), 1000))
    pages = int(scoring._f(inputs.get("maxPages"), 6))

    smart, seen = [], set()
    for page in range(pages):
        d = _read(ctx, "discovery_get_top_traders",
                  {"time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
                   "open_position_filter": False, "limit": psize, "offset": page * psize},
                  f"discovery_get_top_traders(p{page})")
        rows = scoring.traders_of(d)
        if not rows:
            break
        top = None
        for t in rows:
            if not isinstance(t, dict):
                continue
            a = scoring.trader_address(t)
            if not a or a in seen:
                continue
            rp = scoring.realized(t)
            top = rp if top is None else max(top, rp)
            if rp >= smin and len(smart) < cap:
                smart.append(a)
                seen.add(a)
        if len(smart) >= cap:
            break
        if top is not None and top < smin:   # sorted desc by realized — past the floor, stop
            break
    return smart


def _snapshot_states(ctx, cohort, inputs):
    """discovery_get_trader_state in batches of stateBatch -> a flat list of trader-state
    dicts. Returns None iff EVERY batch read failed (unreadable — the caller then holds
    the prior snapshot and emits nothing, rather than treating it as 'cohort flat')."""
    batch = int(scoring._f(inputs.get("stateBatch"), 50))
    states, any_ok = [], False
    for i in range(0, len(cohort), batch):
        d = _read(ctx, "discovery_get_trader_state",
                  {"trader_addresses": cohort[i:i + batch]},
                  f"discovery_get_trader_state(b{i // batch})")
        if d is None:
            continue
        any_ok = True
        states.extend(scoring.traders_of(d))
    return states if any_ok else None


def _held(ctx):
    """Bare-uppercase set of coins with an open position on this wallet, or None on
    read failure. Ported verbatim from raven (clearinghouse assetPositions unwrap)."""
    d = _read(ctx, "strategy_get_clearinghouse_state",
              {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(d, dict):
        return None
    out = set()
    for e in d.get("assetPositions", d.get("asset_positions", [])) or []:
        pos = e.get("position", e) if isinstance(e, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        if coin and scoring._f(pos.get("szi")) != 0:
            out.add(coin.split(":", 1)[-1].upper())
    return out


def _prune_recent(recent, ttl, now):
    """Drop recent-signal entries older than 4x TTL so the map stays bounded."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in (recent or {}).items() if scoring._f(v) >= cutoff}


def _persist(ctx, cohort, cohort_ts, snapshot, recent, now, opened, held_n):
    if ctx.state is None:
        return
    try:
        ctx.state.append({
            "cohort": cohort, "cohort_ts": cohort_ts,
            "last_snapshot": snapshot, "recent": recent,
            "result": {"ts": now, "opened": opened, "held": held_n,
                       "cohort_size": len(cohort), "consensus_names": len(snapshot or {})},
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[starling.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)


def scan(inputs, ctx):
    now = time.time()
    refresh_s = scoring._f(inputs.get("cohortRefreshHours"), 12.0) * 3600.0
    max_slots = int(scoring._f(inputs.get("maxSlots"), 6))
    min_c = int(scoring._f(inputs.get("minConsensus"), 3))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 21600)

    st = (ctx.state.last() or {}) if ctx.state else {}
    cohort = list(st.get("cohort", []) or [])
    cohort_ts = scoring._f(st.get("cohort_ts"), 0.0)
    prev = dict(st.get("last_snapshot", {}) or {})
    recent = _prune_recent(st.get("recent", {}) or {}, ttl, now)

    # ── SLOW CLOCK: refresh the cohort only when due (or first tick / empty) ──
    if cohort_ts == 0.0 or (now - cohort_ts) >= refresh_s or not cohort:
        fresh_cohort = _cohort(ctx, inputs)
        if fresh_cohort:
            cohort, cohort_ts = fresh_cohort, now
            print(f"[starling.scan] COHORT refreshed: {len(cohort)} smart wallets "
                  f"(realized >= ${scoring._f(inputs.get('smartMinRealizedUsd'), 1e6):,.0f})",
                  file=sys.stderr)
        else:
            print(f"[starling.scan] cohort derivation empty — keeping prior cohort "
                  f"({len(cohort)} wallets)", file=sys.stderr)

    if not cohort:
        print("[starling.scan] no cohort yet — nothing to follow this tick", file=sys.stderr)
        _persist(ctx, cohort, cohort_ts, prev, recent, now, 0, 0)
        return []

    # ── snapshot the cohort's live books -> current consensus ──
    states = _snapshot_states(ctx, cohort, inputs)
    if states is None:
        print("[starling.scan] cohort states unreadable — holding prior snapshot", file=sys.stderr)
        _persist(ctx, cohort, cohort_ts, prev, recent, now, 0, None)
        return []
    cur = scoring.consensus_counts(states)
    names = scoring.name_map(states)               # BARE -> raw coin (preserves xyz: prefix)
    fresh = scoring.fresh_picks(cur, prev, inputs)

    held = _held(ctx)
    if held is None:
        # clearinghouse unreadable — do NOT advance the snapshot (keep the fresh
        # consensus actionable next tick), emit nothing this tick.
        print("[starling.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
        _persist(ctx, cohort, cohort_ts, prev, recent, now, 0, None)
        return []
    free = max_slots - len(held)

    out = []
    if free > 0 and fresh:
        for pick in fresh:
            if free <= 0:
                break
            bare = pick["asset"]
            direction = pick["direction"]
            count = int(pick["count"])
            if bare in held:
                continue
            if recent.get(bare) is not None and (now - scoring._f(recent[bare])) < ttl:
                continue
            asset = names.get(bare, bare)          # emit the tradeable (prefixed) name
            band = scoring.band_for(count, inputs)
            lev, mgn = scoring.sizing_for(band, inputs, None)
            recent[bare] = now
            free -= 1
            reasons = [f"{count}_smart_wallets_{direction}"]
            out.append({
                "asset": asset, "direction": direction, "marginPct": mgn, "leverage": lev,
                "data": {"consensusCount": count, "band": band, "direction": direction,
                         "reasons": reasons, "leverage": lev},
            })
            print(f"[starling.scan] OPEN {direction} {asset}: {count} smart wallets "
                  f"agree (band {band}) {lev}x {mgn}%", file=sys.stderr)

    if not out:
        n_consensus = sum(1 for a, dirs in cur.items()
                          for dv in (dirs or {}).values() if int(scoring._f(dv)) >= min_c)
        print(f"[starling.scan] no opens: cohort={len(cohort)} held={len(held)}/{max_slots} "
              f"free={free} names_at_consensus={n_consensus} fresh={len(fresh)}", file=sys.stderr)

    _persist(ctx, cohort, cohort_ts, cur, recent, now, len(out), len(held))
    return out
