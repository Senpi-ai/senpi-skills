"""GORILLA — ENTRIES scanner (Runtime 3.0 supervised, shared by both books).

At the FIRST tick after deploy this scanner performs the full market scan and
DERIVES the standing thesis (scoring.derive_thesis) — stance, long/short
buckets, plain-English narrative — and persists it in ctx.state. Between
thesis boundaries it only PRESSES: it scores this book's bucket names and
emits conviction-banded entries when the tape confirms the thesis direction,
respecting the stance's per-book active-slot cap. Every thesisRefreshHours
(default 48h from establishment) it re-derives its copy of the thesis.

The sibling rebalance.py owns the closes (thesis_shift at the 48h rethink,
weekly_rebalance at the 7d boundary) via the CLOSE_POSITION action. Each
scanner keeps its own state, so both derive the thesis from the same reads on
the same anchored clock — agreement is by determinism, not shared memory
(drift bounded by one tick interval; documented package property).

Read-only + single-pass. marginPct is a PERCENT in (0,100].
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "SUI",
                     "AVAX", "LINK", "LTC", "AAVE", "UNI", "BNB", "ARB"]


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


def sm_board(ctx):
    """One leaderboard_get_markets read -> {TOKEN: {direction, pct}} (kodiak-verbatim)."""
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


def full_market_scan(ctx, universe):
    """One pass over the universe -> (views for derive_thesis, market-data cache)."""
    views, cache = {}, {}
    board = sm_board(ctx)
    for name in universe:
        md = _asset_data(ctx, name)
        if not md:
            continue
        candles = md.get("candles", {}) or {}
        cache[name] = md
        sm = board.get(str(name).upper(), {"direction": "NEUTRAL", "pct": 50})
        views[name] = {"rs": scoring.rs_score(candles.get("1h", []), candles.get("4h", [])),
                       "sm_dir": sm["direction"], "sm_pct": sm["pct"]}
    return views, cache, board


def refresh_thesis(ctx, inputs, now, prior):
    """Derive a fresh thesis from a full market scan. Returns (thesis, cache, board)
    or (None, {}, {}) when the market read failed (keeps the prior thesis)."""
    universe = inputs.get("universe", _DEFAULT_UNIVERSE)
    views, cache, board = full_market_scan(ctx, universe)
    if len(views) < max(4, len(universe) // 2):
        print(f"[gorilla.scan] thesis refresh aborted — only {len(views)}/{len(universe)} "
              f"names readable; keeping prior thesis", file=sys.stderr)
        return None, {}, {}
    btc = cache.get(inputs.get("tideAsset", "BTC")) or {}
    bc = btc.get("candles", {}) or {}
    regime = _funding_regime(ctx)
    thesis = scoring.derive_thesis(views, bc.get("1h", []), bc.get("4h", []),
                                   regime, inputs, now)
    print(f"[gorilla.scan] THESIS: {thesis['narrative']}", file=sys.stderr)
    return thesis, cache, board


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

    cache, board = {}, None
    # ── establish at deploy; re-derive on the 48h boundary ──
    if not thesis or scoring.due(now, refreshed_at, refresh_s):
        fresh, cache, board = refresh_thesis(ctx, inputs, now, thesis)
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
            md = cache.get(name) or _asset_data(ctx, name)
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
                    "smDir": th["sm_dir"], "smPct": scoring._f(th["sm_pct"], 50),
                    "mom24h": scoring._f(th["mom24h"]),
                    "reasons": th["reasons"],
                },
            })
            print(f"[gorilla.scan] {side} EMIT {th['asset']}: score={th['score']} "
                  f"band={band} {leverage}x {margin_pct}% | {thesis['stance']}", file=sys.stderr)

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
