"""PILOTFISH — Smart-Money Accumulation. The single supervised scanner.

Every other copy strategy reads a STANDING position — who is long what, right now, which
is already priced. PILOTFISH reads the DERIVATIVE: it snapshots the proven cohort's
dollar-weighted net bias per name and enters when that conviction is WIDENING.

Each tick:
  1) SLOW CLOCK — refresh the proven cohort (top traders by realized PnL) when due.
  2) Read the cohort's live books, roll up net/gross notional per coin.
  3) Diff against the snapshot from ~biasLookbackHours ago; open on names where the
     DOMINANT side's conviction is widening, sized by shrunk conviction.

NEVER closes — DSL owns every exit. Read-only, single-pass, no daemon.
marginPct is a PERCENT in (0,100].
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[pilotfish.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _rows(payload, *keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
        for v in payload.values():
            if isinstance(v, list):
                return v
    return []


def _held(ctx):
    data = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet},
                 "clearinghouse")
    if data is None:
        return None
    held = set()
    views = list(data.values()) + [data] if isinstance(data, dict) else []
    for view in views:
        if not isinstance(view, dict):
            continue
        for ap in (view.get("assetPositions") or []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            coin = pos.get("coin") or pos.get("asset")
            if coin and scoring._f(pos.get("szi"), 0.0) != 0.0:
                held.add(scoring._bare_upper(coin))
    return held


def _refresh_cohort(ctx, inputs):
    """The proven cohort: top traders by realized PnL. Refreshed on a slow clock."""
    addrs, offset = [], 0
    page = int(scoring._f(inputs.get("pageSize"), 500))
    cap = int(scoring._f(inputs.get("cohortCap"), 80))
    min_real = scoring._f(inputs.get("minRealizedUsd"), 1_000_000)
    for _ in range(int(scoring._f(inputs.get("maxPages"), 4))):
        data = _read(ctx, "discovery_get_top_traders",
                     {"time_frame": "MONTHLY", "sort_by": "PROFIT_AND_LOSS_REALIZED",
                      "limit": page, "offset": offset},
                     "discovery_get_top_traders")
        rows = _rows(data, "traders", "results", "data")
        if not rows:
            break
        for t in rows:
            if not isinstance(t, dict):
                continue
            realized = scoring._f(t.get("realizedPnl", t.get("realized_profit_and_loss")), 0.0)
            addr = scoring._trader_address(t)
            if addr and realized >= min_real:
                addrs.append(addr)
        if len(addrs) >= cap:
            break
        offset += page
    seen, out = set(), []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:cap]


def _cohort_states(ctx, addrs, inputs):
    batch = int(scoring._f(inputs.get("stateBatch"), 40))
    states = []
    for i in range(0, len(addrs), batch):
        data = _read(ctx, "discovery_get_trader_state",
                     {"trader_addresses": addrs[i:i + batch]}, "discovery_get_trader_state")
        states.extend(_rows(data, "traders", "states", "data"))
    return states


def scan(inputs, ctx):
    now = time.time()
    max_slots = int(scoring._f(inputs.get("maxSlots"), 5))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 21600)
    refresh_s = scoring._f(inputs.get("cohortRefreshHours"), 12.0) * 3600.0
    look_s = scoring._f(inputs.get("biasLookbackHours"), 6.0) * 3600.0

    st = (ctx.state.last() or {}) if ctx.state else {}
    cohort = list(st.get("cohort", []) or [])
    last_refresh = scoring._f(st.get("last_refresh"), 0.0)
    recent = dict(st.get("recent", {}) or {})
    history = list(st.get("history", []) or [])       # [{ts, bias:{asset:{...}}}]

    if not cohort or (now - last_refresh) >= refresh_s:
        fresh = _refresh_cohort(ctx, inputs)
        if fresh:
            cohort, last_refresh = fresh, now
            print(f"[pilotfish.scan] cohort refreshed: {len(cohort)} proven wallets", file=sys.stderr)
    if not cohort:
        print("[pilotfish.scan] no cohort — nothing to follow this tick", file=sys.stderr)
        return []

    cur = scoring.cohort_bias(_cohort_states(ctx, cohort, inputs))
    if not cur:
        print("[pilotfish.scan] cohort books unreadable — no opens this tick", file=sys.stderr)
        return []

    # the comparison snapshot: the most recent one at least `look_s` old
    prior = {}
    prior_ts = None
    for snap in history:
        ts = scoring._f(snap.get("ts"), 0.0)
        if now - ts >= look_s and (prior_ts is None or ts > prior_ts):
            prior, prior_ts = (snap.get("bias") or {}), ts

    out = []
    if prior_ts is None:
        print("[pilotfish.scan] no comparison snapshot yet — building history, no opens "
              "(expected on a cold start)", file=sys.stderr)
    else:
        held = _held(ctx)
        if held is None:
            print("[pilotfish.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
            held = None
        else:
            free = max_slots - len(held)
            picks = scoring.accumulating(cur, prior, inputs)
            for p in picks:
                if free <= 0:
                    break
                if p["asset"] in held:
                    continue
                if recent.get(p["asset"]) is not None and (now - recent[p["asset"]]) < ttl:
                    continue
                if p["score"] < scoring._f(inputs.get("minScore"), 4):
                    continue
                band = scoring.band_for(p["score"], inputs)
                lev, mgn = scoring.sizing_for(band, inputs)
                recent[p["asset"]] = now
                free -= 1
                hrs = (now - prior_ts) / 3600.0
                out.append({
                    "asset": p["name"], "direction": p["direction"],
                    "marginPct": mgn, "leverage": lev,
                    "data": {"score": p["score"], "band": band, "direction": p["direction"],
                             "leverage": lev, "bias": p["bias"], "priorBias": p["prev_bias"],
                             "biasDelta": p["delta"], "members": p["members"],
                             "conviction": p["conviction"],
                             "reasons": [
                                 f"proven cohort net bias {p['prev_bias']:+.2f} -> {p['bias']:+.2f} "
                                 f"over ~{hrs:.0f}h (widening by {p['delta']:.2f})",
                                 f"{p['n_short']} short vs {p['n_long']} long of "
                                 f"{p['members']} positioned wallets",
                                 f"conviction {p['conviction']:.2f} (shrunk for sample size)"]},
                })
                print(f"[pilotfish.scan] OPEN {p['direction']} {p['name']}: bias "
                      f"{p['prev_bias']:+.2f}->{p['bias']:+.2f} over ~{hrs:.0f}h, "
                      f"{p['members']} wallets, {lev}x {mgn}%", file=sys.stderr)

    if not out:
        print(f"[pilotfish.scan] no opens: cohort={len(cohort)} names={len(cur)}", file=sys.stderr)

    history.append({"ts": now, "bias": cur})
    keep_s = max(look_s * 4, 86400.0)
    history = [h for h in history if now - scoring._f(h.get("ts"), 0.0) <= keep_s][-48:]
    if ctx.state is not None:
        try:
            ctx.state.append({"cohort": cohort, "last_refresh": last_refresh,
                              "recent": recent, "history": history,
                              "result": {"ts": now, "opened": len(out), "names": len(cur)}})
        except Exception as exc:  # noqa: BLE001
            print(f"[pilotfish.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
