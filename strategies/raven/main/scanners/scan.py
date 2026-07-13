"""RAVEN — Self-Calibrating Momentum. The single supervised scanner.

A momentum book that reads its OWN realized track record and adapts. Each tick:
  1) Load the standing calibration (current_min_score, size_scale) from ctx.state.
  2) SLOW CLOCK: when the recalibration window elapses, read this wallet's own
     closed-trade history (discovery_get_trader_history), roll up win-rate /
     profit-factor / losing-streak, and ratchet the two knobs within bounded rails
     (scoring.adapt). No-op below minTrades — never tunes on noise.
  3) Read held positions; fill any FREE slots with universe candidates whose
     momentum thesis clears the CURRENT adaptive floor, sized by conviction ×
     size_scale. NEVER closes — DSL owns every exit; the runtime's drawdown_halt
     is the equity backstop.

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
        print(f"[raven.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _dex_of(name):
    return "xyz" if str(name).lower().startswith("xyz:") else ""


def _instrument_rows(ctx, dex):
    data = _read(ctx, "market_list_instruments", ({"dex": dex} if dex else {}),
                 f"market_list_instruments({dex or 'main'})")
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
        vol = scoring._f(c.get("dayNtlVlm"))
        venue = c.get("max_leverage", c.get("maxLeverage")) or inst.get("max_leverage")
        rows.append({"name": str(name), "vol": vol, "venue_max": venue, "dex": _dex_of(name)})
    return rows


def _derive_universe(ctx, inputs):
    """Live top-N liquid names, main (+ xyz iff enabled), over the vol floor."""
    main = _instrument_rows(ctx, "")
    if main is None:
        return None
    rows = list(main)
    if bool(inputs.get("includeXyz", True)):
        rows += (_instrument_rows(ctx, "xyz") or [])
    vfloor = scoring._f(inputs.get("universeVolFloorUsd"), 25_000_000)
    xfloor = scoring._f(inputs.get("xyzVolFloorUsd"), 3_000_000)
    keep = [r for r in rows if r["vol"] >= (xfloor if r["dex"] == "xyz" else vfloor)]
    keep.sort(key=lambda r: r["vol"], reverse=True)
    cap = int(scoring._f(inputs.get("maxUniverse"), 40))
    return keep[:cap]


def _asset_data(ctx, name):
    return _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": ["15m", "1h", "4h"],
        "include_funding": True, "include_order_book": False, "dex": _dex_of(name),
    }, f"market_get_asset_data({name})")


def _funding_of(md):
    """Current funding rate as a float (tolerant; 0.0 if absent)."""
    for k in ("funding", "funding_rate", "fundingRate", "current_funding"):
        v = scoring._num((md or {}).get(k))
        if v is not None:
            return v
    fh = (md or {}).get("funding") if isinstance((md or {}).get("funding"), dict) else None
    if isinstance(fh, dict):
        return scoring._f(fh.get("rate", fh.get("current")))
    return 0.0


def _held(ctx):
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


def _own_closed(ctx, inputs):
    """This wallet's own closed trades, newest-first, for self-calibration."""
    n = int(scoring._f(inputs.get("historyTrades"), 40))
    d = _read(ctx, "discovery_get_trader_history",
              {"trader_address": ctx.wallet, "limit": n,
               "sort_by": "CLOSED_TIME", "sort_direction": "DESC"},
              "discovery_get_trader_history(self)")
    if d is None:
        return None
    if isinstance(d, list):
        return d
    for k in ("closedPositions", "closed_positions", "positions", "history", "trades", "data", "results"):
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, list):
            return v
    return []


def _recalibrate(ctx, inputs, state, now):
    """Read own history → adapt the knobs. Returns (min_score, size_scale, stats, note)."""
    closed = _own_closed(ctx, inputs)
    if closed is None:                       # read failed — keep current calibration
        cur_min = scoring._f(state.get("current_min_score"), scoring._f(inputs.get("initialMinScore"), 8))
        cur_scale = scoring._f(state.get("size_scale"), 1.0)
        return cur_min, cur_scale, {"n": -1}, "history unreadable — holding calibration"
    stats = scoring.track_record(closed, scoring._f(inputs.get("historyTrades"), 40))
    if stats.get("n", 0) == 0:
        print("[raven.scan] WARNING: 0 closed trades parsed from history — holding "
              "(verify discovery_get_trader_history payload shape)", file=sys.stderr)
    min_score, size_scale, note = scoring.adapt(stats, state, inputs)
    return min_score, size_scale, stats, note


def scan(inputs, ctx):
    now = time.time()
    recal_s = scoring._f(inputs.get("recalibrationHours"), 12.0) * 3600.0
    max_slots = int(scoring._f(inputs.get("maxSlots"), 6))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 21600)

    st = (ctx.state.last() or {}) if ctx.state else {}
    floor = scoring._f(inputs.get("initialMinScore"), 8)
    min_score = scoring._f(st.get("current_min_score"), floor)
    size_scale = scoring._f(st.get("size_scale"), 1.0)
    last_recal = scoring._f(st.get("last_recal"), 0.0)
    recent = dict(st.get("recent", {}) or {})
    stats = st.get("stats", {}) or {}

    # ── SLOW CLOCK: self-calibrate only when due (or first tick) ──
    if last_recal == 0.0 or (now - last_recal) >= recal_s:
        min_score, size_scale, stats, note = _recalibrate(ctx, inputs, st, now)
        last_recal = now
        print(f"[raven.scan] SELF-TUNE: {note} | floor now {min_score:g}, size ×{size_scale:.2f}",
              file=sys.stderr)

    held = _held(ctx)
    if held is None:
        return []                                    # clearinghouse unreadable — act next tick
    free = max_slots - len(held)

    out = []
    if free > 0:
        universe = _derive_universe(ctx, inputs)
        if universe is None:
            print("[raven.scan] instruments unreadable — no opens this tick", file=sys.stderr)
        else:
            looked = 0
            for row in universe:
                if free <= 0 or looked >= max(12, free * 4):
                    break
                name = row["name"]
                bare = str(name).split(":", 1)[-1].upper()
                if bare in held:
                    continue
                if recent.get(bare) is not None and (now - recent[bare]) < ttl:
                    continue
                looked += 1
                md = _asset_data(ctx, name)
                if not md:
                    continue
                candles = md.get("candles", {}) or {}
                th = scoring.build_thesis(name, candles.get("15m", []), candles.get("1h", []),
                                          candles.get("4h", []), _funding_of(md), (None, 0), inputs)
                if not th or th["score"] < min_score:
                    continue
                band = scoring.band_for(th["score"], inputs)
                lev, mgn = scoring.sizing_for(band, size_scale, inputs, row.get("venue_max"))
                recent[bare] = now
                free -= 1
                out.append({
                    "asset": name, "direction": th["direction"], "marginPct": mgn, "leverage": lev,
                    "data": {"score": th["score"], "leverage": lev, "direction": th["direction"],
                             "band": band, "sizeScale": round(size_scale, 3),
                             "minScore": round(min_score, 3), "reasons": th["reasons"]},
                })
                print(f"[raven.scan] OPEN {th['direction']} {name}: score={th['score']} "
                      f"band={band} {lev}x {mgn}% (floor {min_score:g}, size ×{size_scale:.2f})",
                      file=sys.stderr)

    if not out:
        print(f"[raven.scan] no opens: held={len(held)}/{max_slots} free={free} "
              f"floor={min_score:g} size=×{size_scale:.2f} n={stats.get('n', '?')}", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({
                "current_min_score": min_score, "size_scale": size_scale,
                "last_recal": last_recal, "recent": recent, "stats": stats,
                "result": {"ts": now, "opened": len(out), "held": len(held),
                           "floor": min_score, "size_scale": size_scale},
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[raven.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
