"""CUTTLEFISH — ENTRIES scanner (Runtime 3.0 supervised, shared by both books).

Each tick: read the TIDE (BTC 1h/4h), the market-wide funding regime, and the
smart-money board once; then score every universe name in this book's mandated
`side` (market-pulse context x smart-money divergence core, pure scoring.py) and
emit the top-K candidates as conviction-banded signals (leverage clamped to the
asset's live venue max). The runtime sizes the dollars, owns cooldowns / daily
caps / drawdown halts, and trails the DSL exit.

The sibling rebalance.py owns the AUTO-ADJUST side (CLOSE_POSITION on tide flip
/ SM reversal / X-hourly basket refresh) — this scanner only ever opens.

Read-only + single-pass. marginPct is a PERCENT in (0,100].
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DEFAULT_TTL = 3600
_DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE",
                     "SUI", "AVAX", "LINK", "LTC", "AAVE", "UNI"]


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade the factor, never the tick
        print(f"[cuttlefish.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else None


def _asset_data(ctx, asset, funding=True):
    return _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "include_funding": funding,
        "include_order_book": False,
        "dex": "",
    }, f"market_get_asset_data({asset})")


def _funding_regime(ctx):
    data = _read(ctx, "market_get_funding_regime", {}, "market_get_funding_regime")
    return data.get("regime") if isinstance(data, dict) else None


def sm_board(ctx):
    """One leaderboard_get_markets read -> {TOKEN: {direction, pct, cc_15m}}
    (kodiak-verbatim per-token extraction, done once for the whole universe)."""
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
        cc = scoring._f(m.get("contribution_pct_change_15m", 0))
        rec = acc.setdefault(token, {"long": 0.0, "short": 0.0, "cc_15m": cc})
        if d == "long":
            rec["long"] = pct
        elif d == "short":
            rec["short"] = pct
    out = {}
    for token, rec in acc.items():
        total = rec["long"] + rec["short"]
        if total == 0:
            out[token] = {"direction": "NEUTRAL", "pct": 50, "cc_15m": rec["cc_15m"]}
            continue
        long_ratio = (rec["long"] / total) * 100
        if long_ratio > 58:
            out[token] = {"direction": "LONG", "pct": long_ratio, "cc_15m": rec["cc_15m"]}
        elif long_ratio < 42:
            out[token] = {"direction": "SHORT", "pct": 100 - long_ratio, "cc_15m": rec["cc_15m"]}
        else:
            out[token] = {"direction": "NEUTRAL", "pct": 50, "cc_15m": rec["cc_15m"]}
    return out


def scan(inputs, ctx):
    side = (inputs.get("side", "LONG") or "LONG").upper()
    universe = inputs.get("universe", _DEFAULT_UNIVERSE)
    slots = int(scoring._f(inputs.get("maxSlots"), 4))
    min_score = scoring._f(inputs.get("minScore"), 5.5)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), _DEFAULT_TTL)
    now = time.time()

    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    # ── TIDE + regime + SM board — the shared market read (3 MCP calls) ──
    btc = _asset_data(ctx, inputs.get("tideAsset", "BTC"), funding=False)
    if not btc:
        return []
    btc_candles = btc.get("candles", {}) or {}
    tide, tide_detail = scoring.tide_from_btc(btc_candles.get("1h", []), btc_candles.get("4h", []))
    regime = _funding_regime(ctx)
    board = sm_board(ctx)

    result_base = {"ts": now, "side": side, "tide": tide, "tideDetail": tide_detail,
                   "regime": regime}
    out = []
    if not scoring.tide_allows(side, tide):
        result = {**result_base, "emitted": 0, "gate": "tide_stand_down"}
        print(f"[cuttlefish.scan] {side} book STAND DOWN: tide={tide} ({tide_detail})",
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
            th = scoring.score_asset(name, side, candles.get("1h", []), candles.get("4h", []),
                                     md.get("asset_context", {}) or {}, board.get(au),
                                     regime, inputs)
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
                    "band": band, "tide": tide, "regime": regime or "UNKNOWN",
                    "smDir": th.get("sm_dir") or "NEUTRAL",
                    "smPct": scoring._f(th.get("sm_pct"), 50),
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
                  f"(tide={tide}, {len(candidates)} scored)", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[cuttlefish.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
