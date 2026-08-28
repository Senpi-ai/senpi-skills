"""WEAVER — Range Harvester. The single supervised scanner.

Most templates go quiet when nothing is trending, which is exactly when users ask "why
isn't it firing?". WEAVER is the one built for that market: it works a well-contained
band, buying the floor and selling the ceiling.

The thing that kills a harvester is a trend — gridding a market that has started to run
is how you average into a loss — so the efficiency-ratio veto is a HARD gate, not a
score component. The fee gate is carried over from Swift because a harvester trades
often and its edge per trade is small.

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
        print(f"[weaver.scan] {label} read failed: {exc!r}", file=sys.stderr)
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
        "asset": name, "candle_intervals": ["1h"],
        "include_funding": False, "include_order_book": False,
    }, f"market_get_asset_data({name})")
    if not md:
        return None
    return (md.get("candles", {}) or {}).get("1h", [])


def scan(inputs, ctx):
    now = time.time()
    max_slots = int(scoring._f(inputs.get("maxSlots"), 4))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 1800)

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = dict(st.get("recent", {}) or {})

    held = _held(ctx)
    if held is None:
        print("[weaver.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
        return []
    free = max_slots - len(held)

    out, priced_out = [], 0
    if free > 0:
        universe = _universe(ctx, inputs)
        if universe is None:
            print("[weaver.scan] instruments unreadable — no opens this tick", file=sys.stderr)
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
            candles = _candles(ctx, name, inputs)
            if not candles:
                continue
            th = scoring.build_thesis(name, candles, inputs)
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
                         "rangePos": th["range_pos"], "widthPct": th["width_pct"],
                         "efficiencyRatio": th["efficiency_ratio"],
                         "expectedCapturePct": th["fees"]["expected_capture_pct"],
                         "requiredPct": th["fees"]["required_pct"]},
            })
            print(f"[weaver.scan] OPEN {th['direction']} {name}: score={th['score']} band={band} "
                  f"{lev}x {mgn}% | at {th['range_pos'] * 100:.0f}% of a {th['width_pct']:.1f}% band "
                  f"(ER {th['efficiency_ratio']:.2f}) | "
                  f"capture {th['fees']['expected_capture_pct']:.3f}% "
                  f"> needed {th['fees']['required_pct']:.3f}%", file=sys.stderr)

    if not out:
        print(f"[weaver.scan] no opens: held={len(held)}/{max_slots} free={free} "
              f"({priced_out} candidates trending, mid-range, or too quiet to cover fees)",
              file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent,
                              "result": {"ts": now, "opened": len(out), "held": len(held),
                                         "priced_out": priced_out}})
        except Exception as exc:  # noqa: BLE001
            print(f"[weaver.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
