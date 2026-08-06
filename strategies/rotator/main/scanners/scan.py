"""ROTATOR — supervised scanner: rebalance a concentrated (<=2) conviction book on a
fixed clock (default 3h) from a blend of three SHORT-HORIZON reads.

Per tick (every ~3h): read this wallet's open positions (dual-DEX), and if a slot is
free, pull cross-asset flows (alts lagging BTC's move), the market funding regime
(crowding => contrarian), and leaderboard momentum events (winners entering strong
phases), blend them into ONE conviction score per candidate, and emit the top 1-2
LONG/SHORT intents to fill the free slots. Rotate-by-attrition: NEVER closes — the DSL
exits, and the next rebalance fills the freed slot with the current best. Read-only,
single-pass, aggressive size on a deliberately small book."""

import sys
import time

import scoring


def _read(ctx, tool, args, label):
    """Guarded MCP read: degrade to None, never crash the whole tick."""
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[rotator.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _held(ctx):
    """Bare-uppercase coins with an open position (dual-DEX main+xyz), or None on failure.
    Reading assetPositions off the TOP level silently yields NOTHING held -> re-opens a
    name already held; both sub-DEX views must be walked (Hyperliquid wallet structure)."""
    d = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet}, "clearinghouse")
    if not isinstance(d, dict):
        return None
    rows = []
    for sec in ("main", "xyz"):
        s = d.get(sec)
        if isinstance(s, dict):
            rows.extend(s.get("assetPositions", s.get("asset_positions", [])) or [])
    if not rows:
        rows = d.get("assetPositions", d.get("asset_positions", [])) or []
    out = set()
    for e in rows:
        pos = e.get("position", e) if isinstance(e, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        if coin and scoring._f(pos.get("szi")) != 0:
            out.add(coin.split(":", 1)[-1].upper())
    return out


def _events(ctx, inputs):
    raw = _read(ctx, "leaderboard_get_momentum_events",
                {"tier": int(scoring._f(inputs.get("minEventTier"), 2))}, "momentum_events")
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        ev = raw.get("events", raw.get("momentum_events", raw.get("results", [])))
        return ev if isinstance(ev, list) else []
    return []


def scan(inputs, ctx):
    now = time.time()
    max_slots = int(scoring._f(inputs.get("maxSlots"), 2))
    min_score = scoring._f(inputs.get("minScore"), 4.0)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 10800)
    leader = str(inputs.get("leaderAsset", "BTC"))

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (st.get("recent") or {}).items() if (now - v) < ttl}

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent, "result": {"ts": now}})
        except Exception as exc:  # noqa: BLE001
            print(f"[rotator.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    held = _held(ctx)
    if held is None:
        return []                                     # clearinghouse unreadable — act next tick
    free = max_slots - len(held)
    if free <= 0:
        print(f"[rotator.scan] book full ({len(held)}/{max_slots}) — rotate-by-attrition", file=sys.stderr)
        _persist()
        return []

    flow = scoring.unwrap_flow(_read(ctx, "market_get_cross_asset_flows",
                                     {"leader_asset": leader,
                                      "min_move_pct": scoring._f(inputs.get("leaderMinMovePct"), 1.0)},
                                     "cross_asset_flows"))
    cands0 = scoring.laggards(flow, inputs)
    if not cands0:
        print(f"[rotator.scan] no qualifying laggards (leader {leader} quiet)", file=sys.stderr)
        _persist()
        return []

    reg = _read(ctx, "market_get_funding_regime", {}, "funding_regime")
    regime = reg.get("regime") if isinstance(reg, dict) else None
    ev_per, ev_total = scoring.events_by_coin(_events(ctx, inputs), now, inputs)

    scored = [scoring.blended_score(lg, regime, ev_per, ev_total, inputs) for lg in cands0]
    scored = [s for s in scored if s["score"] >= min_score]
    scored.sort(key=lambda s: s["score"], reverse=True)

    out = []
    for s in scored:
        if free <= 0:
            break
        bare = str(s["coin"]).split(":", 1)[-1].upper()
        if bare in held:                              # already hold this name
            continue
        if recent.get(bare) is not None and (now - recent[bare]) < ttl:
            continue
        band = scoring.band_for(s["score"], inputs)
        lev, mgn = scoring.sizing_for(band, inputs)
        out.append({
            "asset": s["coin"], "direction": s["direction"], "marginPct": mgn, "leverage": lev,
            "data": {"score": s["score"], "direction": s["direction"], "band": band,
                     "regime": regime or "NEUTRAL", "gapPct": s["gap"], "followRate": s["follow"],
                     "confidence": s["conf"], "reasons": s["reasons"]},
        })
        recent[bare] = now
        free -= 1
        print(f"[rotator.scan] OPEN {s['direction']} {s['coin']} score={s['score']} "
              f"band={band} {lev}x {mgn}%", file=sys.stderr)

    _persist()
    return out
