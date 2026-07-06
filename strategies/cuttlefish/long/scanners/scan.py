"""CUTTLEFISH — ENTRIES scanner (Runtime 3.0 supervised, shared by both books).

Each tick (15m): READ THE MARKET with the real engines —
  - instruments on BOTH dexes (pulse.py pattern): the derived universe (volume
    floor + top-N main-dex perps) AND the cross-asset pulse day classification
    ride the same two reads (24h changes from prevDayPx — no candle dependency);
  - the smart-vs-crowd COHORTS (smartmoney.py port) from a 4h-clock cache in
    ctx.state — discovery paging is heavy, so cohorts refresh every
    cohortRefreshHours, never per tick; between refreshes the cached per-coin
    biases score the book. When discovery_* is empty the composite degrades to
    the near-term board and SAYS SO (blocked/flag semantics, never silent);
  - the 4h leaderboard board once — the NEAR-TERM confirm factor (its actual
    role in those engines), plus the funding regime.

Then: stand down if the pulse day is against this book; otherwise score every
universe name in the book's side (scoring.score_asset) and emit the top-K as
conviction-banded signals (leverage clamped to venue max).

The sibling rebalance.py owns the closes (pulse_flip / divergence_reversed /
basket_refresh) via CLOSE_POSITION. Read-only + single-pass. marginPct is a
PERCENT in (0,100].
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DEFAULT_TTL = 3600

# cross-asset pulse groups (senpi-market-pulse pulse.py, trimmed to the signal
# set the stance consumes; the crypto group is the derived universe itself)
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
    except Exception as exc:  # noqa: BLE001 — degrade the factor, never the tick
        print(f"[cuttlefish.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else None


def _asset_data(ctx, asset):
    return _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": "",
    }, f"market_get_asset_data({asset})")


def _funding_regime(ctx):
    data = _read(ctx, "market_get_funding_regime", {}, "market_get_funding_regime")
    return data.get("regime") if isinstance(data, dict) else None


def _instrument_rows(ctx, dex):
    """One market_list_instruments read -> [{name, vol, price, change_pct}]
    (pulse.py-verbatim fold: quote nests under `context`; daily change from
    prevDayPx). Returns None on read failure."""
    args = {"dex": dex} if dex else {}
    data = _read(ctx, "market_list_instruments", args,
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
        ictx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        price = scoring._num(inst.get("markPx")) or scoring._num(ictx.get("markPx")) \
            or scoring._num(ictx.get("midPx"))
        prev = scoring._num(inst.get("prevDayPx")) or scoring._num(ictx.get("prevDayPx"))
        rows.append({"name": str(name), "vol": scoring._f(ictx.get("dayNtlVlm")),
                     "price": price, "change_pct": scoring.pct_change(price, prev)})
    return rows


def market_read(ctx, inputs):
    """The shared per-tick market read: (universe, pulse, changes) off the two
    instruments reads. Returns (None, None, None) when main is unreadable."""
    main_rows = _instrument_rows(ctx, "")
    if main_rows is None:
        return None, None, None
    override = inputs.get("universeOverride") or []
    universe = ([str(n) for n in override] if override
                else scoring.derive_universe(main_rows, inputs))
    changes, prices = {}, {}
    xyz_rows = _instrument_rows(ctx, "xyz") or []
    for r in main_rows + xyz_rows:
        sym = str(r["name"]).upper().replace("XYZ:", "")
        if r.get("change_pct") is not None:
            changes[sym] = r["change_pct"]
        if r.get("price") is not None:
            prices[sym] = r["price"]
    groups = dict(inputs.get("pulseGroups") or _PULSE_GROUPS)
    groups["crypto"] = list(universe)
    pulse = scoring.pulse_stance(changes, groups, vix_price=prices.get("VIX"))
    return universe, pulse, changes


def sm_board(ctx):
    """One leaderboard_get_markets read -> {TOKEN: {direction, pct}} — the 4h
    NEAR-TERM layer (kodiak-verbatim extraction). NOT the smart-money cohort."""
    data = _read(ctx, "leaderboard_get_markets", {"limit": 100}, "leaderboard_get_markets")
    if data is None:
        return {}
    markets = data.get("markets", data) if isinstance(data, dict) else data
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}
    acc = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        if not token:
            continue
        d = str(m.get("direction", "")).lower()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        rec = acc.setdefault(token, {"long": 0.0, "short": 0.0})
        if d == "long":
            rec["long"] = pct
        elif d == "short":
            rec["short"] = pct
    out = {}
    for token, rec in acc.items():
        total = rec["long"] + rec["short"]
        if total == 0:
            out[token] = {"direction": "NEUTRAL", "pct": 50}
        else:
            lr = (rec["long"] / total) * 100
            out[token] = ({"direction": "LONG", "pct": lr} if lr > 58 else
                          {"direction": "SHORT", "pct": 100 - lr} if lr < 42 else
                          {"direction": "NEUTRAL", "pct": 50})
    return out


def build_cohorts(ctx, inputs):
    """smartmoney.py build_cohorts + cohort_bias port (heavy — runs only on the
    cohortRefreshHours clock, cached in ctx.state between refreshes)."""
    cfg = inputs.get("cohorts") or {}
    smart_min = scoring._f(cfg.get("smartMinRealizedUsd"), scoring.SMART_MIN_REALIZED)
    crowd_min = scoring._f(cfg.get("crowdMinRealizedUsd"), scoring.CROWD_MIN_REALIZED)
    crowd_max = scoring._f(cfg.get("crowdMaxRealizedUsd"), scoring.CROWD_MAX_REALIZED)
    sample_cap = int(scoring._f(cfg.get("sampleCap"), 150))
    page_size = int(scoring._f(cfg.get("pageSize"), 1000))
    max_pages = int(scoring._f(cfg.get("maxPages"), 6))
    batch_size = int(scoring._f(cfg.get("stateBatch"), 50))

    def _traders_of(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("traders", "data", "results"):
                if isinstance(data.get(k), list):
                    return data[k]
        return []

    def _realized(t):
        for k in ("realizedProfitAndLoss", "realized_profit_and_loss",
                  "profit_and_loss_realized", "realizedPnl", "realized_pnl"):
            v = scoring._num((t or {}).get(k))
            if v is not None:
                return v
        return 0.0

    smart_addrs, crowd_addrs, seen = [], [], set()
    for page in range(max_pages):
        data = _read(ctx, "discovery_get_top_traders",
                     {"time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
                      "open_position_filter": False, "limit": page_size,
                      "offset": page * page_size},
                     f"discovery_get_top_traders(page {page})")
        rows = _traders_of(data)
        if not rows:
            break
        page_top = None
        for t in rows:
            if not isinstance(t, dict):
                continue
            addr = str(t.get("address") or t.get("trader_address") or t.get("wallet") or "").lower()
            if not addr or addr in seen:
                continue
            rp = _realized(t)
            page_top = rp if page_top is None else max(page_top, rp)
            if rp >= smart_min:
                if len(smart_addrs) < sample_cap:
                    smart_addrs.append(addr)
                    seen.add(addr)
            elif crowd_min <= rp <= crowd_max:
                if len(crowd_addrs) < sample_cap:
                    crowd_addrs.append(addr)
                    seen.add(addr)
        if len(smart_addrs) >= sample_cap and len(crowd_addrs) >= sample_cap:
            break
        if page_top is not None and page_top < crowd_min:
            break                        # paged past both cohorts

    def _bias_for(addrs, label):
        per = {}
        for i in range(0, len(addrs), batch_size):
            data = _read(ctx, "discovery_get_trader_state",
                         {"trader_addresses": addrs[i:i + batch_size]},
                         f"discovery_get_trader_state({label} batch {i // batch_size})")
            scoring.cohort_positions_bias(_traders_of(data), per)
        return scoring.finalize_bias(per)

    smart_per = _bias_for(smart_addrs, "smart") if smart_addrs else {}
    crowd_per = _bias_for(crowd_addrs, "crowd") if crowd_addrs else {}
    available = bool(smart_per) and bool(crowd_per)
    if not available:
        print("[cuttlefish.scan] cohorts unavailable (discovery_* empty — token scope?) "
              "— composite degrades to the near-term board and says so", file=sys.stderr)
    return {"smart": smart_per, "crowd": crowd_per, "available": available}


def cached_cohorts(ctx, inputs, st, now):
    """Cohort cache on its own clock: refresh every cohortRefreshHours (default
    4h), else reuse the copy in ctx.state. Returns (cohort, refreshed_at)."""
    refresh_s = scoring._f(inputs.get("cohortRefreshHours"), 4.0) * 3600.0
    cohort = st.get("cohort")
    refreshed_at = scoring._f(st.get("cohort_refreshed_at"), 0.0)
    if cohort and not scoring.due(now, refreshed_at, refresh_s):
        return cohort, refreshed_at
    fresh = build_cohorts(ctx, inputs)
    if fresh["available"] or not cohort:
        return fresh, now                # adopt fresh (even an unavailable first read)
    print("[cuttlefish.scan] cohort refresh failed — keeping prior cohort cache",
          file=sys.stderr)
    return cohort, refreshed_at          # keep stale-but-real over fresh-but-empty


def scan(inputs, ctx):
    side = (inputs.get("side", "LONG") or "LONG").upper()
    slots = int(scoring._f(inputs.get("maxSlots"), 4))
    min_score = scoring._f(inputs.get("minScore"), 5.5)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), _DEFAULT_TTL)
    cfg = inputs.get("cohorts") or {}
    now = time.time()

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = st.get("recent", {}) or {}

    universe, pulse, _changes = market_read(ctx, inputs)
    if universe is None:
        return []
    day = pulse.get("day")
    cohort, cohort_ts = cached_cohorts(ctx, inputs, st, now)
    regime = _funding_regime(ctx)
    board = sm_board(ctx)

    result_base = {"ts": now, "side": side, "pulseDay": day,
                   "groupAvgs": pulse.get("group_avgs"), "regime": regime,
                   "cohortsAvailable": cohort.get("available")}
    out = []
    if not scoring.pulse_allows(side, day):
        result = {**result_base, "emitted": 0, "gate": "pulse_stand_down"}
        print(f"[cuttlefish.scan] {side} book STAND DOWN: pulse day={day} "
              f"({pulse.get('groups_down')} groups down / {pulse.get('groups_up')} up)",
              file=sys.stderr)
    else:
        candidates = []
        for name in universe:
            au = str(name).upper()
            if recent.get(au) is not None and (now - recent[au]) < ttl:
                continue
            md = _asset_data(ctx, name)
            if not md:
                continue
            candles = md.get("candles", {}) or {}
            cv = scoring.cohort_view_for(name, side, cohort, cfg)
            th = scoring.score_asset(name, side, candles.get("1h", []), candles.get("4h", []),
                                     md.get("asset_context", {}) or {}, cv,
                                     board.get(au), regime, inputs)
            if not th or th.get("blocked") or th["score"] < min_score:
                continue
            venue_max = (md.get("asset_context", {}) or {}).get(
                "max_leverage", (md.get("asset_context", {}) or {}).get("maxLeverage"))
            th["_venue_max"] = venue_max
            candidates.append(th)

        candidates.sort(key=lambda t: t["score"], reverse=True)
        picks = candidates[:slots]
        for th in picks:
            band = scoring.band_for(th["score"], inputs)
            leverage, margin_pct = scoring.sizing_for(band, inputs, th.get("_venue_max"))
            au = th["asset"].upper()
            recent[au] = now
            out.append({
                "asset": th["asset"],
                "direction": side,
                "marginPct": margin_pct,      # PERCENT of withdrawable — runtime sizes it
                "leverage": leverage,
                "data": {
                    "score": th["score"], "leverage": leverage, "direction": side,
                    "band": band, "pulseDay": day or "no_read", "regime": regime or "UNKNOWN",
                    "smartBias": scoring._f(th.get("smart_bias")),
                    "divergent": bool(th.get("divergent")),
                    "cohortsAvailable": bool(th.get("cohorts_available")),
                    "nearTermPct": scoring._f(th.get("nt_pct"), 50),
                    "mom24h": scoring._f(th.get("mom24h")),
                    "components": th["components"],
                    "reasons": th["reasons"],
                },
            })
            print(f"[cuttlefish.scan] {side} EMIT {th['asset']}: score={th['score']} "
                  f"band={band} {leverage}x {margin_pct}% | {th['reasons']}", file=sys.stderr)
        result = {**result_base, "emitted": len(out), "gate": "pass",
                  "scanned": len(universe),
                  "top": [{"asset": t["asset"], "score": t["score"]} for t in candidates[:6]]}
        if not out:
            print(f"[cuttlefish.scan] {side} HOLD: no candidate >= {min_score} "
                  f"(day={day}, {len(candidates)} scored, cohorts="
                  f"{'ok' if cohort.get('available') else 'UNAVAILABLE'})", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "cohort": cohort,
                              "cohort_refreshed_at": cohort_ts, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[cuttlefish.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
