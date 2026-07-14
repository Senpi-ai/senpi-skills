"""ANT — Funding Harvester. The single supervised scanner.

The best Senpi-achievable cash-and-carry: perps-only, so it harvests funding by
SHORTING perps that pay positive funding (longs pay shorts), gated so it only fades
an EXHAUSTED long crowd — never a name still ripping. Directional (not delta-neutral;
Senpi can't place the spot hedge — see ant/NOTES.md). Each tick:
  1) Rank the liquid universe by real open interest (volume shortlist → asset_data
     OI) and take the top-N by OI.
  2) For each, read funding (current + recent history) + candles; keep names whose
     annualized funding ≥ targetApr, has persisted, and whose tape shows exhaustion.
  3) Short the best-scoring, up to free slots. NEVER closes — the DSL owns exits;
     a 24h hard-timeout forces the daily rotation / funding-decay drop-off.

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
        print(f"[ant.scan] {label} read failed: {exc!r}", file=sys.stderr)
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
        rows.append({"name": str(name), "vol": scoring._f(c.get("dayNtlVlm")),
                     "venue_max": c.get("max_leverage", c.get("maxLeverage")), "dex": _dex_of(name)})
    return rows


def _asset_data(ctx, name):
    return _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": ["1h", "4h"],
        "include_funding": True, "include_order_book": False, "dex": _dex_of(name),
    }, f"market_get_asset_data({name})")


def _oi_of(md):
    ctx_ = (md or {}).get("asset_context") or (md or {}).get("assetContext") or {}
    oi = scoring._num(ctx_.get("openInterest") or ctx_.get("open_interest"))
    mark = scoring._num(ctx_.get("markPx") or ctx_.get("midPx"))
    if oi is None:
        return 0.0
    return oi * mark if (mark and oi < 1e7) else oi   # coin-units × price → USD; else already USD-ish


def _funding(ctx, name):
    """This asset's funding row from market_get_funding_history — the call + parse are
    ported from pangolin (a LIVE strategy): args are `{"asset": <bare>}` ONLY (the tool
    has no `dex` param), and the payload is double-nested `data.data = [{asset,
    annualized_pct, funding_direction, persistence_hours, trend}, ...]`. Returns that
    row dict for this asset, or None. (`_read` already unwraps the outer `data`.)"""
    bare = str(name).split(":", 1)[-1]
    d = _read(ctx, "market_get_funding_history", {"asset": bare},
              f"market_get_funding_history({bare})")
    rows = d.get("data") if isinstance(d, dict) else d
    if not isinstance(rows, list) or not rows:
        return None
    up = bare.upper()
    for row in rows:
        if isinstance(row, dict) and str(row.get("asset", "")).split(":", 1)[-1].upper() == up:
            return row
    return rows[0] if isinstance(rows[0], dict) else None   # asset-filtered call → single row


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


def _top_by_oi(ctx, inputs):
    """Volume shortlist → asset_data OI → the top-N names by real open interest.
    Returns [(name, oi_usd, venue_max, md)] or None if instruments unreadable."""
    main = _instrument_rows(ctx, "")
    if main is None:
        return None
    rows = list(main)
    if bool(inputs.get("includeXyz", False)):
        rows += (_instrument_rows(ctx, "xyz") or [])
    vfloor = scoring._f(inputs.get("universeVolFloorUsd"), 25_000_000)
    rows = [r for r in rows if r["vol"] >= vfloor]
    rows.sort(key=lambda r: r["vol"], reverse=True)
    shortlist = rows[: int(scoring._f(inputs.get("shortlistByVol"), 20))]

    scored = []
    for r in shortlist:
        md = _asset_data(ctx, r["name"])
        if not md:
            continue
        scored.append((r["name"], _oi_of(md), r.get("venue_max"), md))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[: int(scoring._f(inputs.get("topOiCount"), 10))]


def scan(inputs, ctx):
    now = time.time()
    max_slots = int(scoring._f(inputs.get("maxSlots"), 5))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 21600)

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = dict(st.get("recent", {}) or {})

    held = _held(ctx)
    if held is None:
        return []
    free = max_slots - len(held)

    out = []
    if free > 0:
        top = _top_by_oi(ctx, inputs)
        if top is None:
            print("[ant.scan] instruments unreadable — no opens this tick", file=sys.stderr)
        else:
            cands = []
            for name, oi, venue_max, md in top:
                bare = str(name).split(":", 1)[-1].upper()
                if bare in held or (recent.get(bare) and (now - scoring._f(recent[bare])) < ttl):
                    continue
                funding = _funding(ctx, name)
                candles = md.get("candles", {}) or {}
                th = scoring.build_signal(name, funding, oi,
                                          candles.get("1h", []), candles.get("4h", []), inputs)
                if th:
                    cands.append((th, venue_max))
            cands.sort(key=lambda c: c[0]["score"], reverse=True)
            for th, venue_max in cands:
                if free <= 0:
                    break
                bare = str(th["coin"]).split(":", 1)[-1].upper()
                band = scoring.band_for(th["score"], inputs)
                lev, mgn = scoring.sizing_for(band, inputs, venue_max)
                recent[bare] = now
                free -= 1
                out.append({
                    "asset": th["coin"], "direction": "SHORT", "marginPct": mgn, "leverage": lev,
                    "data": {"score": th["score"], "leverage": lev, "direction": "SHORT",
                             "band": band, "fundingApr": th["apr"], "oiUsd": round(th["oi_usd"]),
                             "reasons": th["reasons"]},
                })
                print(f"[ant.scan] SHORT {th['coin']}: apr={th['apr']:.0f}% score={th['score']} "
                      f"band={band} {lev}x {mgn}% | {', '.join(th['reasons'][:3])}", file=sys.stderr)

    if not out:
        print(f"[ant.scan] no shorts: held={len(held)}/{max_slots} free={free}", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent,
                              "result": {"ts": now, "opened": len(out), "held": len(held)}})
        except Exception as exc:  # noqa: BLE001
            print(f"[ant.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
