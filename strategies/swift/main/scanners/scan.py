"""SWIFT — Fee-Aware Crypto Scalper. The single supervised scanner.

Scalping is the most-requested thing users ask us to build and the easiest to get
wrong: at this frequency the round trip is charged every time, so a signal that does
not clear its own cost is a guaranteed loser no matter how good the setup looks.

Each tick: derive a liquid crypto universe, and for every free slot take the best
short-horizon momentum setup that (a) is aligned across 5m and 15m, (b) has real
volume behind it, and (c) EXPECTS TO CAPTURE MORE THAN IT COSTS TO TRADE. The fee
gate runs first — see scoring.clears_fees.

NEVER closes: DSL owns every exit. Read-only, single-pass, no daemon.
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
        print(f"[swift.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _bare(name):
    return str(name).split(":", 1)[-1].upper()


def _held(ctx):
    """Bare-upper asset names this wallet already holds, or None if unreadable."""
    data = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet},
                 "clearinghouse")
    if data is None:
        return None
    held = set()
    views = data.values() if isinstance(data, dict) else []
    for view in list(views) + ([data] if isinstance(data, dict) else []):
        if not isinstance(view, dict):
            continue
        for ap in (view.get("assetPositions") or []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            coin = pos.get("coin") or pos.get("asset")
            if coin and scoring._f(pos.get("szi"), 0.0) != 0.0:
                held.add(_bare(coin))
    return held


def _universe(ctx, inputs):
    """Top-N most liquid CRYPTO names over the volume floor. Derived live, never hardcoded."""
    data = _read(ctx, "market_list_instruments", {}, "market_list_instruments(main)")
    if data is None:
        return None
    insts = data.get("instruments", data.get("universe", data)) if isinstance(data, dict) else data
    if not isinstance(insts, list):
        return None
    rows = []
    for inst in insts:
        if not isinstance(inst, dict) or inst.get("is_delisted"):
            continue
        name = inst.get("name") or (inst.get("context", {}) or {}).get("coin")
        if not name:
            continue
        c = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        rows.append({"name": str(name), "vol": scoring._f(c.get("dayNtlVlm")),
                     "venue_max": c.get("max_leverage", c.get("maxLeverage")) or inst.get("max_leverage")})
    floor = scoring._f(inputs.get("volFloorUsd"), 50_000_000)
    keep = [r for r in rows if r["vol"] >= floor]
    keep.sort(key=lambda r: r["vol"], reverse=True)
    return keep[:int(scoring._f(inputs.get("maxUniverse"), 20))]


def _candles(ctx, name, inputs):
    md = _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": ["5m", "15m"],
        "include_funding": False, "include_order_book": False,
    }, f"market_get_asset_data({name})")
    if not md:
        return None, None
    c = md.get("candles", {}) or {}
    return c.get("5m", []), c.get("15m", [])


def scan(inputs, ctx):
    now = time.time()
    max_slots = int(scoring._f(inputs.get("maxSlots"), 4))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 1800)

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = dict(st.get("recent", {}) or {})

    held = _held(ctx)
    if held is None:
        print("[swift.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
        return []
    free = max_slots - len(held)

    out, priced_out = [], 0
    if free > 0:
        universe = _universe(ctx, inputs)
        if universe is None:
            print("[swift.scan] instruments unreadable — no opens this tick", file=sys.stderr)
            universe = []
        looked = 0
        for row in universe:
            if free <= 0 or looked >= max(10, free * 5):
                break
            name = row["name"]
            bare = _bare(name)
            if bare in held:
                continue
            if recent.get(bare) is not None and (now - recent[bare]) < ttl:
                continue
            looked += 1
            fast, slow = _candles(ctx, name, inputs)
            if not fast or not slow:
                continue
            th = scoring.build_thesis(name, fast, slow, inputs)
            if th is None:
                priced_out += 1
                continue
            if th["score"] < scoring._f(inputs.get("minScore"), 4):
                continue
            band = scoring.band_for(th["score"], inputs)
            lev, mgn = scoring.sizing_for(band, inputs, row.get("venue_max"))
            recent[bare] = now
            free -= 1
            out.append({
                "asset": name, "direction": th["direction"], "marginPct": mgn, "leverage": lev,
                "data": {"score": th["score"], "band": band, "direction": th["direction"],
                         "leverage": lev, "reasons": th["reasons"],
                         "expectedCapturePct": th["fees"]["expected_capture_pct"],
                         "requiredPct": th["fees"]["required_pct"]},
            })
            print(f"[swift.scan] OPEN {th['direction']} {name}: score={th['score']} band={band} "
                  f"{lev}x {mgn}% | capture {th['fees']['expected_capture_pct']:.3f}% "
                  f"> needed {th['fees']['required_pct']:.3f}%", file=sys.stderr)

    if not out:
        print(f"[swift.scan] no opens: held={len(held)}/{max_slots} free={free} "
              f"({priced_out} candidates rejected as too quiet to cover fees)", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent,
                              "result": {"ts": now, "opened": len(out), "held": len(held),
                                         "priced_out": priced_out}})
        except Exception as exc:  # noqa: BLE001
            print(f"[swift.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
