"""REGIME ALLOCATOR — the single supervised scanner. Shared VERBATIM by Chimp
(daily) and Gorilla (weekly); cadence + DSL come from runtime.yaml inputs.

One scanner, one OPEN_POSITION action, no CLOSE. Each tick:
  1) Load the standing POSTURE from ctx.state.
  2) If it's stale (recalibrationHours elapsed) or unset, run the FULL market
     read — main + xyz instruments -> pulse day classification + dispersion,
     funding regime, smart-money cohorts (degrading to the leaderboard board) —
     and rebuild the posture. This is the ONLY expensive read, and it runs on
     the slow clock, never nonstop.
  3) Read this wallet's open positions; fill any FREE slots with the posture's
     ranked candidates that the tape still confirms — conviction-banded, DSL
     attached by the runtime. NEVER close anything: prior-regime positions
     retire on their own DSL (rotate-by-attrition).

Read-only + single-pass. marginPct is a PERCENT in (0,100]. No daemon.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

# cross-asset pulse groups (market-pulse pulse.py; crypto = the derived main pool)
_PULSE_GROUPS = {
    "semis": ["NVDA", "AMD", "AVGO", "MU", "TSM", "ASML", "MRVL", "ARM"],
    "megacap_software": ["AMZN", "MSFT", "META", "GOOGL", "AAPL", "ORCL", "PLTR"],
    "crypto_proxy": ["MSTR", "COIN", "HOOD"],
    "indices": ["SP500", "XYZ100", "JP225", "KR200"],
    "commodities": ["GOLD", "SILVER", "COPPER", "BRENTOIL", "NATGAS"],
    "macro_fx": ["DXY", "JPY", "EUR", "GBP"],
}


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[allocator.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else None


def _dex_of(name):
    return "xyz" if str(name).lower().startswith("xyz:") else ""


def _instrument_rows(ctx, dex):
    """One market_list_instruments read -> [{name, vol, change_pct, price}]
    (pulse.py fold: quote under context; daily change from prevDayPx)."""
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
        price = scoring._num(inst.get("markPx")) or scoring._num(c.get("markPx")) or scoring._num(c.get("midPx"))
        prev = scoring._num(inst.get("prevDayPx")) or scoring._num(c.get("prevDayPx"))
        rows.append({"name": str(name), "vol": scoring._f(c.get("dayNtlVlm")),
                     "price": price, "change_pct": scoring.pct_change(price, prev)})
    return rows


def _asset_data(ctx, name):
    return _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": ["1h", "4h"],
        "include_funding": False, "include_order_book": False, "dex": _dex_of(name),
    }, f"market_get_asset_data({name})")


def _funding_regime(ctx):
    d = _read(ctx, "market_get_funding_regime", {}, "market_get_funding_regime")
    return d.get("regime") if isinstance(d, dict) else None


def _board(ctx):
    """leaderboard_get_markets -> {TOKEN: {direction, pct}} (near-term lean)."""
    d = _read(ctx, "leaderboard_get_markets", {"limit": 100}, "leaderboard_get_markets")
    if d is None:
        return {}
    markets = d.get("markets", d) if isinstance(d, dict) else d
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}
    acc = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        tok = str(m.get("token", "")).upper()
        if not tok:
            continue
        d2 = str(m.get("direction", "")).lower()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        rec = acc.setdefault(tok, {"long": 0.0, "short": 0.0})
        if d2 == "long":
            rec["long"] = pct
        elif d2 == "short":
            rec["short"] = pct
    out = {}
    for tok, rec in acc.items():
        tot = rec["long"] + rec["short"]
        if tot == 0:
            out[tok] = {"direction": "NEUTRAL", "pct": 50}
        else:
            lr = rec["long"] / tot * 100
            out[tok] = ({"direction": "LONG", "pct": lr} if lr > 58 else
                        {"direction": "SHORT", "pct": 100 - lr} if lr < 42 else
                        {"direction": "NEUTRAL", "pct": 50})
    return out


def _cohorts(ctx, inputs):
    """Proven (>=$1M realized) vs crowd ($10k-100k) bias — smart-money enrichment.
    Degrades to {available: False} when discovery_* is empty (token scope)."""
    if not bool(inputs.get("useCohorts", True)):
        return {"available": False, "smart": {}, "crowd": {}}
    cfg = inputs.get("cohorts") or {}
    smin = scoring._f(cfg.get("smartMinRealizedUsd"), 1_000_000)
    cmin = scoring._f(cfg.get("crowdMinRealizedUsd"), 10_000)
    cmax = scoring._f(cfg.get("crowdMaxRealizedUsd"), 100_000)
    cap = int(scoring._f(cfg.get("sampleCap"), 150))
    psize = int(scoring._f(cfg.get("pageSize"), 1000))
    pages = int(scoring._f(cfg.get("maxPages"), 6))
    batch = int(scoring._f(cfg.get("stateBatch"), 50))

    def traders_of(d):
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            for k in ("traders", "data", "results"):
                if isinstance(d.get(k), list):
                    return d[k]
        return []

    def realized(t):
        for k in ("realizedProfitAndLoss", "realized_profit_and_loss",
                  "profit_and_loss_realized", "realizedPnl", "realized_pnl"):
            v = scoring._num((t or {}).get(k))
            if v is not None:
                return v
        return 0.0

    smart, crowd, seen = [], [], set()
    for page in range(pages):
        d = _read(ctx, "discovery_get_top_traders",
                  {"time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
                   "open_position_filter": False, "limit": psize, "offset": page * psize},
                  f"discovery_get_top_traders(p{page})")
        rows = traders_of(d)
        if not rows:
            break
        top = None
        for t in rows:
            if not isinstance(t, dict):
                continue
            a = str(t.get("address") or t.get("trader_address") or t.get("wallet") or "").lower()
            if not a or a in seen:
                continue
            rp = realized(t)
            top = rp if top is None else max(top, rp)
            if rp >= smin and len(smart) < cap:
                smart.append(a); seen.add(a)
            elif cmin <= rp <= cmax and len(crowd) < cap:
                crowd.append(a); seen.add(a)
        if len(smart) >= cap and len(crowd) >= cap:
            break
        if top is not None and top < cmin:
            break

    def bias(addrs, label):
        per = {}
        for i in range(0, len(addrs), batch):
            d = _read(ctx, "discovery_get_trader_state",
                      {"trader_addresses": addrs[i:i + batch]},
                      f"discovery_get_trader_state({label} b{i // batch})")
            scoring.cohort_positions_bias(traders_of(d), per)
        return scoring.finalize_bias(per)

    sp = bias(smart, "smart") if smart else {}
    cp = bias(crowd, "crowd") if crowd else {}
    avail = bool(sp) and bool(cp)
    if not avail:
        print("[allocator.scan] cohorts unavailable (discovery_* empty) — using board lean",
              file=sys.stderr)
    return {"available": avail, "smart": sp, "crowd": cp}


def _held(ctx):
    """Bare-uppercase set of coins with an open position, or None on read failure."""
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


def _rebuild_posture(ctx, inputs, now):
    main_rows = _instrument_rows(ctx, "")
    if main_rows is None:
        print("[allocator.scan] instruments unreadable — keeping prior posture", file=sys.stderr)
        return None
    xyz_rows = _instrument_rows(ctx, "xyz") or []
    pool = scoring.derive_universe(main_rows, xyz_rows, inputs)
    if len(pool) < 6:
        print(f"[allocator.scan] only {len(pool)} names over the vol floor — keeping prior posture",
              file=sys.stderr)
        return None
    changes, prices = {}, {}
    for r in main_rows + xyz_rows:
        sym = str(r["name"]).upper().replace("XYZ:", "")
        if r.get("change_pct") is not None:
            changes[sym] = r["change_pct"]
        if r.get("price") is not None:
            prices[sym] = r["price"]
    groups = dict(inputs.get("pulseGroups") or _PULSE_GROUPS)
    groups["crypto"] = [i["name"] for i in pool if i["dex"] == ""]
    pulse = scoring.pulse_stance(changes, groups, vix_price=prices.get("VIX"))
    cohort = _cohorts(ctx, inputs)
    board = _board(ctx)
    regime = _funding_regime(ctx)
    posture = scoring.build_posture(pool, pulse, regime, cohort, board, inputs, now)
    print(f"[allocator.scan] POSTURE: {posture['narrative']}", file=sys.stderr)
    return posture


def scan(inputs, ctx):
    now = time.time()
    recal_s = scoring._f(inputs.get("recalibrationHours"), 24.0) * 3600.0
    max_slots = int(scoring._f(inputs.get("maxSlots"), 8))
    min_score = scoring._f(inputs.get("minScore"), 5.5)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 43200)

    st = (ctx.state.last() or {}) if ctx.state else {}
    posture = st.get("posture")
    last_recal = scoring._f(st.get("last_recal"), 0.0)
    recent = st.get("recent", {}) or {}
    board = None

    # ── SLOW CLOCK: rebuild the posture only when due (or first tick) ──
    if posture is None or scoring.due(now, last_recal, recal_s):
        fresh = _rebuild_posture(ctx, inputs, now)
        if fresh:
            posture, last_recal = fresh, now
    if posture is None:
        return []

    held = _held(ctx)
    if held is None:
        return []                                # clearinghouse unreadable — act next tick
    free = max_slots - len(held)

    out = []
    if free > 0:
        if board is None:
            board = _board(ctx)                  # cheap near-term lean for tape confirm
        cohort = {"available": posture.get("cohorts_available", False)}  # bias lives in board on topup
        candidates = ([(n, "LONG") for n in posture.get("longs", [])] +
                      [(n, "SHORT") for n in posture.get("shorts", [])])
        looked = 0
        for name, side in candidates:
            if free <= 0 or looked >= max(12, free * 4):
                break
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
            th = scoring.score_candidate(name, side, candles.get("1h", []),
                                         candles.get("4h", []), cohort, board, inputs)
            if not th or th["score"] < min_score:
                continue
            band = scoring.band_for(th["score"], inputs)
            venue = (md.get("asset_context", {}) or {}).get(
                "max_leverage", (md.get("asset_context", {}) or {}).get("maxLeverage"))
            lev, mgn = scoring.sizing_for(band, posture.get("size_scale", 1.0), inputs, venue)
            recent[bare] = now
            free -= 1
            out.append({
                "asset": name, "direction": side, "marginPct": mgn, "leverage": lev,
                "data": {"score": th["score"], "leverage": lev, "direction": side,
                         "band": band, "stance": posture["stance"], "mode": posture["mode"],
                         "posture": posture["narrative"], "sizeScale": posture.get("size_scale", 1.0),
                         "regime": posture.get("regime", "UNKNOWN"),
                         "lean": th["lean"], "mom24h": th["mom24h"], "reasons": th["reasons"]},
            })
            print(f"[allocator.scan] OPEN {side} {name}: score={th['score']} band={band} "
                  f"{lev}x {mgn}% | {posture['stance']}", file=sys.stderr)

    if not out:
        print(f"[allocator.scan] no opens: stance={posture['stance']} held={len(held)}/"
              f"{max_slots} free={free}", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"posture": posture, "last_recal": last_recal, "recent": recent,
                              "result": {"ts": now, "stance": posture["stance"],
                                         "opened": len(out), "held": len(held),
                                         "narrative": posture["narrative"]}})
        except Exception as exc:  # noqa: BLE001
            print(f"[allocator.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
