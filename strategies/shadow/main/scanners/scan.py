"""SHADOW — supervised scanner: mirror vetted traders' FRESH opens only.

Per tick: resolve the watched cohort (explicit `traderAddresses`, else auto-select the
top-N vetted traders by ALL-TIME realized PnL, cached daily in ctx.state), fetch each
trader's current open positions, diff against the seeded per-trader book to find only
NEWLY opened (coin, side) pairs, require optional multi-trader confirmation, reject any
whose price has already run past the entry-slippage cap, and emit a budget-relative,
min-notional-floored INTENT. The runtime sizes, owns slots/dedup, and trails the DSL exit.
Read-only, single-pass. Never inherits an existing book — first sight of a trader seeds it."""

import sys
import time

import scoring

CACHE_VERSION = 1
_DEFAULT_TTL = 3600


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error must NOT roll back the whole tick."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[shadow.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _unwrap(resp, *keys):
    """discovery/market responses wrap payloads under data/<key>; return the first list/dict."""
    if resp is None:
        return None
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), (list, dict)):
                return data[k]
    return data


def _resolve_cohort(ctx, cached, inputs, now):
    """Explicit traderAddresses win. Else auto-select the top-N vetted traders by ALL-TIME
    realized PnL (>= autoSelectMinRealizedUsd), cached daily so we don't re-rank every tick."""
    explicit = [a.lower() for a in (inputs.get("traderAddresses") or []) if isinstance(a, str) and a]
    max_watch = int(inputs.get("maxWatched", 3))
    if explicit:
        return explicit[:max_watch], cached
    refresh_h = float(inputs.get("cohortRefreshHours", 24))
    if (cached.get("addrs") and cached.get("cache_version") == CACHE_VERSION
            and (now - cached.get("refreshed_at", 0)) / 3600 < refresh_h):
        return cached["addrs"], cached
    smin = float(inputs.get("autoSelectMinRealizedUsd", 1_000_000))
    resp = _read(ctx, "discovery_get_top_traders", {
        "time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
        "open_position_filter": False, "limit": int(inputs.get("autoSelectPool", 50)), "offset": 0})
    raw = _unwrap(resp, "traders", "data") or []
    picked = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict):
            continue
        addr = (t.get("address") or t.get("trader_address") or "").lower()
        rp = scoring._f(t, "realizedProfitAndLoss", "profit_and_loss_realized",
                        "realized_profit_and_loss", "realizedPnl", default=0.0)
        if addr and rp >= smin and addr not in picked:
            picked.append(addr)
        if len(picked) >= max_watch:
            break
    if not picked:
        return cached.get("addrs", []), cached                 # failed refresh -> keep old cache
    return picked, {"refreshed_at": now, "cache_version": CACHE_VERSION, "addrs": picked}


def _fetch_states(ctx, addrs):
    """discovery_get_trader_state in batches of 50 -> {addr: trader-state dict}."""
    out = {}
    for i in range(0, len(addrs), 50):
        resp = _read(ctx, "discovery_get_trader_state",
                     {"trader_addresses": addrs[i:i + 50], "include_position_age": True})
        data = _unwrap(resp, "traders")
        for t in (data or []) if isinstance(data, list) else []:
            if isinstance(t, dict):
                addr = (t.get("address") or t.get("trader_address") or "").lower()
                if addr:
                    out[addr] = t
    return out


def scan(inputs, ctx):
    now = time.time()
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    min_confirm = int(inputs.get("minConfirm", 1))
    max_slip = float(inputs.get("maxEntrySlippagePct", 1.5))

    last = (ctx.state.last() or {}) if ctx.state else {}
    seen_map = dict(last.get("seen") or {})
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    addrs, cohort = _resolve_cohort(ctx, last.get("cohort", {}), inputs, now)

    def _persist(new_seen):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"cohort": cohort, "seen": new_seen, "recent": recent})
        except Exception as exc:  # noqa: BLE001
            print(f"[shadow.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if not addrs:
        _persist(seen_map)
        return []

    states = _fetch_states(ctx, addrs)

    fresh_by_trader = {}
    new_seen = {}
    for addr in addrs:                              # only re-seed CURRENTLY-watched traders
        positions = scoring.extract_positions(states.get(addr))
        fresh, keys = scoring.diff_fresh(addr, positions, seen_map)
        new_seen[addr] = keys
        if fresh:
            fresh_by_trader[addr] = fresh

    agg = scoring.aggregate_fresh(fresh_by_trader)
    cands = [a for a in agg.values() if a["confirm"] >= min_confirm]     # confirmation gate

    out = []
    for a in sorted(cands, key=lambda c: c["confirm"], reverse=True):
        k = scoring.pos_key(a["coin"], a["side"])
        if recent.get(k) is not None and (now - recent[k]) < ttl:        # dedup: one fire per book event
            continue
        slip = scoring.chase_pct(a["entry_avg"], a.get("mark", 0.0), a["side"])
        if a.get("mark", 0.0) > 0 and slip > max_slip:                   # already ran past a fair fill
            print(f"[shadow.scan] skip {k}: chase {slip:.2f}% > cap {max_slip}%", file=sys.stderr)
            continue
        out.append({
            "asset": a["coin"],
            "direction": a["side"],
            "marginPct": scoring.margin_pct_for(a["confirm"], inputs),
            "leverage": scoring.leverage_for(a["lev_avg"], inputs),
            "data": {
                "score": scoring.score_candidate(a, slip),
                "direction": a["side"],
                "signalKind": "FRESH_ENTRY_MIRROR",
                "confirmCount": a["confirm"],
                "entrySlippagePct": slip,
                "traderEntry": round(a["entry_avg"], 6),
                "traders": a["traders"],
                "reasons": [f"{a['confirm']} watched trader(s) opened {a['side']} {a['coin']} fresh",
                            f"chase {slip:+.2f}% vs their entry (cap {max_slip}%)"],
            },
        })
        recent[k] = now

    _persist(new_seen)
    return out
