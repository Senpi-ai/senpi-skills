"""GORILLA — ENTRIES scanner (Runtime 3.0 supervised, shared by both books).

At the FIRST tick after deploy this scanner READS THE MARKET with the real
machinery of the two skills it composes (engine math ported verbatim):

  - senpi-market-pulse: one market_list_instruments read per dex (main + xyz);
    24h changes come from prevDayPx on the instrument rows (no candle pulls at
    refresh — a candle 500 can't drop an asset class); cross-asset groups
    (crypto = the derived universe, semis, megacap software, crypto proxies,
    indices, commodities, macro FX) -> the pulse day classification + the
    GOLD/DXY/VIX confirmation checklist -> the STANCE.
  - senpi-smart-money: the PROVEN cohort (lifetime realized >= $1M) vs the
    CROWD ($10k..$100k) from discovery_get_top_traders pages, positions via
    discovery_get_trader_state batches -> per-coin net/gross bias ->
    divergences fill the buckets. The 4h leaderboard is only the near-term
    confirm at press time. Cohort reads are HEAVY, which is why they run only
    at thesis boundaries (48h), never per tick. discovery_* needs a
    user-scoped token; when unavailable the thesis degrades to 24h-change
    relative strength and SAYS SO in the narrative.

The derived thesis persists in ctx.state; between boundaries this scanner only
PRESSES bucket names when the tape confirms, respecting the stance's per-book
slot cap. Every thesisRefreshHours (48h) it re-derives its copy.

The sibling rebalance.py owns the closes (thesis_shift / divergence_reversed /
weekly_rebalance) via CLOSE_POSITION. Per-scanner state isolation means both
derive the thesis deterministically from the same reads on the same anchored
clock (drift bounded by one tick; documented package property).

Read-only + single-pass. marginPct is a PERCENT in (0,100].
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

# cross-asset pulse groups (senpi-market-pulse pulse.py, trimmed to the signal
# set the stance consumes; the crypto group is the derived universe itself)
_PULSE_GROUPS = {
    "semis": ["NVDA", "AMD", "AVGO", "MU", "TSM", "ASML", "MRVL", "ARM"],
    "megacap_software": ["AMZN", "MSFT", "META", "GOOGL", "AAPL", "ORCL", "PLTR"],
    "crypto_proxy": ["MSTR", "COIN", "HOOD"],
    "indices": ["SP500", "XYZ100", "JP225", "KR200", "NIFTY"],
    "commodities": ["GOLD", "SILVER", "COPPER", "BRENTOIL", "NATGAS"],
    "macro_fx": ["DXY", "JPY", "EUR", "GBP"],
}


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[gorilla.scan] {label} read failed: {exc!r}", file=sys.stderr)
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
    """One market_list_instruments read -> [{name, vol, price, change_pct}].
    pulse.py-verbatim fold: the quote nests under `context` (markPx/prevDayPx/
    dayNtlVlm); daily change from prevDayPx so it never depends on candles.
    Returns None on read failure (distinct from a genuinely empty board)."""
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


def sm_board(ctx):
    """One leaderboard_get_markets read -> {TOKEN: {direction, pct}} — the 4h
    NEAR-TERM layer (kodiak-verbatim extraction), used as press confirm and as
    the RS-fallback hard block. NOT the smart-money cohort."""
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
    """smartmoney.py build_cohorts + cohort_bias, ported to the scanner:
    page the ALL_TIME realized ranking until both cohorts are sampled, then
    batch trader_state and aggregate per-coin bias. Runs ONLY at thesis
    boundaries. Returns {smart, crowd, available}."""
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
        print("[gorilla.scan] cohorts unavailable (discovery_* empty — token scope?) "
              "— thesis degrades to RS fallback and says so", file=sys.stderr)
    return {"smart": smart_per, "crowd": crowd_per, "available": available}


def held_positions(ctx, side):
    """This wallet's open positions in `side` (dual-path, #453-verified shape)."""
    data = _read(ctx, "strategy_get_clearinghouse_state",
                 {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(data, dict):
        return None
    positions = data.get("assetPositions", data.get("asset_positions", [])) or []
    held = []
    for entry in positions:
        pos = entry.get("position", entry) if isinstance(entry, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        szi = scoring._f(pos.get("szi"))
        if not coin or szi == 0:
            continue
        direction = "LONG" if szi > 0 else "SHORT"
        if direction == side:
            held.append({"asset": coin, "direction": direction})
    return held


def refresh_thesis(ctx, inputs, now, prior):
    """Derive a fresh thesis from the full market read: derived universe +
    cross-asset pulse (both off the instruments reads) + smart-vs-crowd cohort
    divergence + funding regime. Returns (thesis, board) or (None, None) when
    the market is unreadable (keeps the prior thesis)."""
    override = inputs.get("universeOverride") or []
    main_rows = _instrument_rows(ctx, "")
    if main_rows is None:
        print("[gorilla.scan] thesis refresh aborted — instrument list unreadable; "
              "keeping prior thesis", file=sys.stderr)
        return None, None
    if override:
        universe = [str(n) for n in override]
    else:
        universe = scoring.derive_universe(main_rows, inputs)
        if len(universe) < 6:
            print(f"[gorilla.scan] thesis refresh aborted — only {len(universe)} names "
                  f"clear the volume floor; keeping prior thesis", file=sys.stderr)
            return None, None

    # cross-asset changes: main + xyz folded, XYZ: prefix stripped (pulse-verbatim)
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

    cohort = build_cohorts(ctx, inputs)
    board = sm_board(ctx)
    regime = _funding_regime(ctx)

    views = {}
    for name in universe:
        sm = board.get(str(name).upper(), {"direction": "NEUTRAL", "pct": 50})
        views[name] = {"chg": changes.get(str(name).upper()),
                       "sm_dir": sm["direction"], "sm_pct": sm["pct"]}

    thesis = scoring.derive_thesis(views, pulse, cohort, regime, inputs, now)
    print(f"[gorilla.scan] THESIS: {thesis['narrative']}", file=sys.stderr)
    return thesis, board


def scan(inputs, ctx):
    side = (inputs.get("side", "LONG") or "LONG").upper()
    refresh_s = scoring._f(inputs.get("thesisRefreshHours"), 48.0) * 3600.0
    min_score = scoring._f(inputs.get("minScore"), 5.5)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 43200)
    now = time.time()

    st = (ctx.state.last() or {}) if ctx.state else {}
    thesis = st.get("thesis")
    established_at = scoring._f(st.get("established_at"), 0.0)
    refreshed_at = scoring._f(st.get("refreshed_at"), 0.0)
    recent = st.get("recent", {}) or {}

    board = None
    # ── establish at deploy; re-derive on the 48h boundary ──
    if not thesis or scoring.due(now, refreshed_at, refresh_s):
        fresh, board = refresh_thesis(ctx, inputs, now, thesis)
        if fresh:
            thesis = fresh
            refreshed_at = now
            if established_at <= 0:
                established_at = now
    if not thesis:
        return []                        # nothing established yet and market unreadable

    out = []
    bucket = scoring.bucket_for(side, thesis)
    cap = scoring.cap_for(side, thesis)
    held = held_positions(ctx, side)
    held_names = {p["asset"].upper() for p in (held or [])}
    room = max(0, cap - len(held_names)) if held is not None else 0

    if room > 0:
        if board is None:
            board = sm_board(ctx)
        candidates = []
        for name in bucket:
            au = str(name).upper()
            if au in held_names:
                continue
            if recent.get(au) is not None and (now - recent[au]) < ttl:
                continue
            md = _asset_data(ctx, name)
            if not md:
                continue
            candles = md.get("candles", {}) or {}
            th = scoring.score_entry(name, side, candles.get("1h", []),
                                     candles.get("4h", []), board.get(au), inputs)
            if not th or th["score"] < min_score:
                continue
            venue_max = (md.get("asset_context", {}) or {}).get(
                "max_leverage", (md.get("asset_context", {}) or {}).get("maxLeverage"))
            th["_venue_max"] = venue_max
            candidates.append(th)
        candidates.sort(key=lambda t: t["score"], reverse=True)
        for th in candidates[:room]:
            band = scoring.band_for(th["score"], inputs)
            leverage, margin_pct = scoring.sizing_for(band, inputs, th.get("_venue_max"))
            recent[th["asset"].upper()] = now
            out.append({
                "asset": th["asset"],
                "direction": side,
                "marginPct": margin_pct,   # PERCENT of withdrawable — runtime sizes it
                "leverage": leverage,
                "data": {
                    "score": th["score"], "leverage": leverage, "direction": side,
                    "band": band, "stance": thesis["stance"],
                    "thesis": thesis["narrative"],
                    "pulseDay": (thesis.get("pulse") or {}).get("day") or "no_read",
                    "bucketSrc": (thesis.get("bucket_src") or {}).get(th["asset"], "unknown"),
                    "smDir": th["sm_dir"], "smPct": scoring._f(th["sm_pct"], 50),
                    "mom24h": scoring._f(th["mom24h"]),
                    "reasons": th["reasons"],
                },
            })
            print(f"[gorilla.scan] {side} EMIT {th['asset']}: score={th['score']} "
                  f"band={band} {leverage}x {margin_pct}% | {thesis['stance']} "
                  f"({(thesis.get('bucket_src') or {}).get(th['asset'], '?')})", file=sys.stderr)

    if not out:
        print(f"[gorilla.scan] {side} HOLD: stance={thesis['stance']} bucket={bucket} "
              f"held={sorted(held_names)} cap={cap}", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"thesis": thesis, "established_at": established_at,
                              "refreshed_at": refreshed_at, "recent": recent,
                              "result": {"ts": now, "side": side, "emitted": len(out),
                                         "stance": thesis["stance"],
                                         "narrative": thesis["narrative"]}})
        except Exception as exc:  # noqa: BLE001
            print(f"[gorilla.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
