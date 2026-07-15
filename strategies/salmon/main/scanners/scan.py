"""SALMON — RSI Mean-Reversion / Dip-Buyer. The single supervised scanner.

Buys oversold BOUNCES and sells overbought FADES on liquid crypto majors. Each tick:
  1) Read held positions; compute FREE slots (never re-enters a held name).
  2) Derive the live MAIN-DEX universe (vol floor + top-N by 24h notional volume;
     XYZ excluded — mean-reversion is cleanest on liquid crypto majors).
  3) For each non-held, non-recently-signaled candidate, fetch 1h candles and run
     the pure `scoring.oversold_bounce` CROSS detector. Emit a conviction-banded,
     capped signal for every confirmed cross (LONG on an oversold cross-up, SHORT
     on an overbought cross-down), sized by band. NEVER closes — the DSL owns every
     exit (tight, because reversions fail fast).

Read-only + single-pass — emits `marginPct` (a PERCENT in (0,100]) + `leverage`
intents; the runtime sizes the dollars, owns cooldowns/risk gates, and trails the
DSL exit. No daemon, no push_signal, no create_position.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[salmon.scan] {label} read failed: {exc!r}", file=sys.stderr)
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
    """Live top-N liquid MAIN-DEX names over the vol floor. XYZ is excluded by
    default (includeXyz defaults False) — mean-reversion is cleanest on liquid
    crypto majors; an operator can still opt XYZ in explicitly."""
    main = _instrument_rows(ctx, "")
    if main is None:
        return None
    rows = list(main)
    if bool(inputs.get("includeXyz", False)):
        rows += (_instrument_rows(ctx, "xyz") or [])
    vfloor = scoring._f(inputs.get("universeVolFloorUsd"), 30_000_000)
    xfloor = scoring._f(inputs.get("xyzVolFloorUsd"), 3_000_000)
    keep = [r for r in rows if r["vol"] >= (xfloor if r["dex"] == "xyz" else vfloor)]
    keep.sort(key=lambda r: r["vol"], reverse=True)
    cap = int(scoring._f(inputs.get("maxUniverse"), 25))
    return keep[:cap]


def _asset_data(ctx, name):
    return _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": ["1h"],
        "include_funding": False, "include_order_book": False, "dex": _dex_of(name),
    }, f"market_get_asset_data({name})")


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


def scan(inputs, ctx):
    now = time.time()
    max_slots = int(scoring._f(inputs.get("maxSlots"), 5))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 14400)

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = dict(st.get("recent", {}) or {})

    held = _held(ctx)
    if held is None:
        return []                                    # clearinghouse unreadable — act next tick
    free = max_slots - len(held)

    out = []
    if free > 0:
        universe = _derive_universe(ctx, inputs)
        if universe is None:
            print("[salmon.scan] instruments unreadable — no opens this tick", file=sys.stderr)
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
                candles_1h = (md.get("candles", {}) or {}).get("1h", [])
                sig = scoring.oversold_bounce(candles_1h, inputs)
                if not sig:
                    continue
                band = scoring.band_for(sig["score"], inputs)
                lev, mgn = scoring.sizing_for(band, inputs, row.get("venue_max"))
                recent[bare] = now
                free -= 1
                out.append({
                    "asset": name, "direction": sig["direction"], "marginPct": mgn, "leverage": lev,
                    "data": {"score": sig["score"], "leverage": lev, "direction": sig["direction"],
                             "band": band, "rsi": sig["rsi"], "reasons": sig["reasons"]},
                })
                print(f"[salmon.scan] OPEN {sig['direction']} {name}: score={sig['score']} "
                      f"band={band} rsi={sig['rsi']} {lev}x {mgn}% | {sig['reasons'][:4]}",
                      file=sys.stderr)

    if not out:
        print(f"[salmon.scan] no opens: held={len(held)}/{max_slots} free={free}", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({
                "recent": recent,
                "result": {"ts": now, "opened": len(out), "held": len(held), "free": free},
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[salmon.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
