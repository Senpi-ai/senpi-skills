"""ORYX — XYZ Intraday Breakout. The single supervised scanner.

Gold, crude, and the indices are the most-requested markets after crypto, and every
one of our XYZ templates is swing or position — there is no intraday one. ORYX is it:
an opening-range break on the 24/7 XYZ venue, volume-confirmed and chase-capped.

⚠️ VENUE NAMING: XYZ instruments must carry the `xyz:` prefix on BOTH the market read
and the emitted signal. A bare `GOLD` funds and then silently never trades. Names from
`market_list_instruments(dex="xyz")` already carry it; `_venue_name` re-attaches it
defensively so a bare row can never leak into a signal.

The fee gate is carried over from Swift unchanged — XYZ books are thinner than crypto
majors, so the cost floor matters MORE here, not less.

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
        print(f"[oryx.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _bare(name):
    return str(name).split(":", 1)[-1].upper()


def _venue_name(name):
    """Always address XYZ instruments by their canonical prefixed name.

    Two failure modes this closes: a BARE token (funds, then silently never trades) and
    a non-canonical `XYZ:` prefix. The TOKEN's case is preserved exactly as the venue
    gave it — HL instrument names are case-sensitive, so upper-casing blindly is its own
    silent no-trade bug.
    """
    s = str(name).strip()
    if ":" in s:
        prefix, token = s.split(":", 1)
        return f"xyz:{token}" if prefix.lower() == "xyz" else f"xyz:{s}"
    return f"xyz:{s}"


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
    """Top-N most liquid XYZ names over the volume floor. Derived live, never hardcoded."""
    data = _read(ctx, "market_list_instruments", {"dex": "xyz"}, "market_list_instruments(xyz)")
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
        rows.append({"name": _venue_name(name), "vol": scoring._f(c.get("dayNtlVlm")),
                     "venue_max": c.get("max_leverage", c.get("maxLeverage")) or inst.get("max_leverage")})
    floor = scoring._f(inputs.get("volFloorUsd"), 3_000_000)   # XYZ books are thinner than crypto
    keep = [r for r in rows if r["vol"] >= floor]
    keep.sort(key=lambda r: r["vol"], reverse=True)
    return keep[:int(scoring._f(inputs.get("maxUniverse"), 20))]


def _candles(ctx, name, inputs):
    md = _read(ctx, "market_get_asset_data", {
        "asset": _venue_name(name), "candle_intervals": ["15m"], "dex": "xyz",
        "include_funding": False, "include_order_book": False,
    }, f"market_get_asset_data({name})")
    if not md:
        return None
    return (md.get("candles", {}) or {}).get("15m", [])


def scan(inputs, ctx):
    now = time.time()
    max_slots = int(scoring._f(inputs.get("maxSlots"), 4))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 1800)

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = dict(st.get("recent", {}) or {})

    held = _held(ctx)
    if held is None:
        print("[oryx.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
        return []
    free = max_slots - len(held)

    out, priced_out = [], 0
    if free > 0:
        universe = _universe(ctx, inputs)
        if universe is None:
            print("[oryx.scan] instruments unreadable — no opens this tick", file=sys.stderr)
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
                         "expectedCapturePct": th["fees"]["expected_capture_pct"],
                         "requiredPct": th["fees"]["required_pct"]},
            })
            print(f"[oryx.scan] OPEN {th['direction']} {name}: score={th['score']} band={band} "
                  f"{lev}x {mgn}% | capture {th['fees']['expected_capture_pct']:.3f}% "
                  f"> needed {th['fees']['required_pct']:.3f}%", file=sys.stderr)

    if not out:
        print(f"[oryx.scan] no opens: held={len(held)}/{max_slots} free={free} "
              f"({priced_out} candidates rejected as too quiet to cover fees)", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent,
                              "result": {"ts": now, "opened": len(out), "held": len(held),
                                         "priced_out": priced_out}})
        except Exception as exc:  # noqa: BLE001
            print(f"[oryx.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
