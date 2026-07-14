"""ARMADILLO — Capital-Preservation / Low-Vol. The single supervised scanner.

The "sleep at night" book: trades ONLY the most liquid majors, at LOW leverage
(1-2x), small per-trade margin, with a HIGH conviction bar (few, high-quality
entries), low turnover, and a tight drawdown circuit-breaker. Its job is to NOT
lose money, not to maximize. Each tick:
  1) Read held positions (free slots = maxSlots - held).
  2) If any slot is free, derive the MAJORS-ONLY universe (main DEX, HIGH volume
     floor, top-N most-liquid), and for each non-held / non-recently-signaled
     candidate score the bison momentum thesis and gate on a HIGH `minScore`.
  3) Emit at most (free) opens, sized LOW (band → 1-2x, 4-8% margin, hard-capped).
  NEVER closes — the DSL owns every exit (tight), and the runtime's drawdown_halt
  is the equity backstop.

Read-only + single-pass. marginPct is a PERCENT in (0,100]. No daemon, no
push_signal, no create_position — the runtime sizes the dollars and trails exits.

NOTE: MCP payload shapes (market_list_instruments / market_get_asset_data /
strategy_get_clearinghouse_state) were NOT live-verified when this was written
(auth token invalid). Every read is tolerant (tries the spellings the corpus uses)
and degrades to "no opens this tick" rather than raising — a capital-preservation
book must fail CLOSED (do nothing), never fail into a bad trade.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[armadillo.scan] {label} read failed: {exc!r}", file=sys.stderr)
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
    """Live top-N MOST-LIQUID names over a HIGH volume floor — majors only.

    Capital preservation trades only the deepest books, so this is main-DEX only
    (includeXyz defaults OFF) with a HIGH vol floor and a small cap (top 8). The
    high floor + small cap IS the 'majors only' filter: sorting the whole live
    board by 24h notional and keeping the top few leaves BTC/ETH/SOL/HYPE-class
    names and nothing thin."""
    main = _instrument_rows(ctx, "")
    if main is None:
        return None
    rows = list(main)
    if bool(inputs.get("includeXyz", False)):          # default OFF — majors only
        rows += (_instrument_rows(ctx, "xyz") or [])
    vfloor = scoring._f(inputs.get("universeVolFloorUsd"), 50_000_000)
    xfloor = scoring._f(inputs.get("xyzVolFloorUsd"), vfloor)
    keep = [r for r in rows if r["vol"] >= (xfloor if r["dex"] == "xyz" else vfloor)]
    keep.sort(key=lambda r: r["vol"], reverse=True)
    cap = int(scoring._f(inputs.get("maxUniverse"), 8))
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
    # dual-shape: {assetPositions:[...]} OR sub-DEX sections {main:{assetPositions}, xyz:{...}}
    buckets = []
    if isinstance(d.get("assetPositions"), list) or isinstance(d.get("asset_positions"), list):
        buckets.append(d)
    for section in ("main", "xyz"):
        s = d.get(section)
        if isinstance(s, dict):
            buckets.append(s)
    for b in buckets:
        for e in b.get("assetPositions", b.get("asset_positions", [])) or []:
            pos = e.get("position", e) if isinstance(e, dict) else {}
            coin = str(pos.get("coin", "")).strip()
            if coin and scoring._f(pos.get("szi")) != 0:
                out.add(coin.split(":", 1)[-1].upper())
    return out


def scan(inputs, ctx):
    now = time.time()
    max_slots = int(scoring._f(inputs.get("maxSlots"), 4))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 43200)
    min_score = scoring._f(inputs.get("minScore"), 11)

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = dict(st.get("recent", {}) or {})

    held = _held(ctx)
    if held is None:
        print("[armadillo.scan] clearinghouse unreadable — no opens this tick (fail closed)",
              file=sys.stderr)
        return []                                        # fail CLOSED — never trade on a bad read
    free = max_slots - len(held)

    out = []
    if free > 0:
        universe = _derive_universe(ctx, inputs)
        if universe is None:
            print("[armadillo.scan] instruments unreadable — no opens this tick", file=sys.stderr)
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
                    continue                             # low turnover: long TTL dedup
                looked += 1
                md = _asset_data(ctx, name)
                if not md:
                    continue
                candles = md.get("candles", {}) or {}
                th = scoring.build_thesis(name, candles.get("15m", []), candles.get("1h", []),
                                          candles.get("4h", []), _funding_of(md), (None, 0), inputs)
                if not th or th["score"] < min_score:     # HIGH bar — only the strongest setups
                    continue
                band = scoring.band_for(th["score"], inputs)
                lev, mgn = scoring.sizing_for(band, inputs, row.get("venue_max"))
                recent[bare] = now
                free -= 1
                out.append({
                    "asset": name, "direction": th["direction"], "marginPct": mgn, "leverage": lev,
                    "data": {"score": th["score"], "leverage": lev, "direction": th["direction"],
                             "band": band, "reasons": th["reasons"]},
                })
                print(f"[armadillo.scan] OPEN {th['direction']} {name}: score={th['score']} "
                      f"band={band} {lev}x {mgn}% (min {min_score:g})", file=sys.stderr)

    if not out:
        print(f"[armadillo.scan] no opens: held={len(held)}/{max_slots} free={free} "
              f"min={min_score:g}", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({
                "recent": recent,
                "result": {"ts": now, "opened": len(out), "held": len(held), "min_score": min_score},
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[armadillo.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
