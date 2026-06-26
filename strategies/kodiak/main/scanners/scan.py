"""KODIAK — supervised scanner (Runtime 3.0 port of the v2 Kodiak SOL alpha hunter).

Single-asset. Reads SOL candles (5m/15m/1h/4h) + funding/OI, a macro driver
(BTC 1h momentum), and smart-money positioning (leaderboard_get_markets); scores
via the pure `scoring.build_thesis`; and emits ONE conviction-tiered signal when
the composite clears `minScore`. Read-only + single-pass — emits a `marginPct`
intent plus a per-signal `leverage` (5/6/7 by score); the runtime sizes the dollars,
owns the cooldowns/risk gates, and trails the DSL exit. No daemon, no push_signal."""

import sys
import time

import scoring

_DEFAULT_TTL = 14400          # 240m — mirror the v2 per-asset cooldown (anti re-fire)
_DEFAULT_TIERS = [[13, 7], [11, 6], [10, 5]]


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _asset_data(ctx, asset, dex, intervals, funding):
    md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
        "asset": asset,
        "candle_intervals": intervals,
        "include_funding": funding,
        "include_order_book": False,
        "dex": dex,
    })
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else None


def _sm_for_asset(ctx, asset):
    """Port of v2 get_sol_sm_signal: net smart-money lean for `asset` from
    leaderboard_get_markets. Returns {direction, pct, traders, cc_15m} or None."""
    raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {"limit": 100})
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    markets = data.get("markets", data) if isinstance(data, dict) else data
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None

    want = asset.upper()
    long_pct = short_pct = 0.0
    traders = 0
    cc_15m = 0.0
    found = False
    for m in markets:
        if not isinstance(m, dict) or str(m.get("token", "")).upper() != want:
            continue
        found = True
        d = str(m.get("direction", "")).lower()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        tc = int(m.get("trader_count", m.get("traderCount", 0)) or 0)
        cc = scoring._f(m.get("contribution_pct_change_15m", 0))
        if d == "long":
            long_pct, cc_15m = pct, cc
            traders += tc
        elif d == "short":
            short_pct, cc_15m = pct, cc
            traders += tc
    if not found:
        return None
    total = long_pct + short_pct
    if total == 0:
        return {"direction": "NEUTRAL", "pct": 50, "traders": traders, "cc_15m": cc_15m}
    long_ratio = (long_pct / total) * 100
    if long_ratio > 58:
        return {"direction": "LONG", "pct": long_ratio, "traders": traders, "cc_15m": cc_15m}
    if long_ratio < 42:
        return {"direction": "SHORT", "pct": 100 - long_ratio, "traders": traders, "cc_15m": cc_15m}
    return {"direction": "NEUTRAL", "pct": 50, "traders": traders, "cc_15m": cc_15m}


def scan(inputs, ctx):
    asset = (inputs.get("asset", "SOL") or "SOL")
    dex = _dex_for(asset, inputs)
    macro_asset = inputs.get("macroAsset", "BTC")     # "" disables the BTC factor (e.g. xyz ports)
    min_score = float(inputs.get("minScore", 10))
    margin_pct = float(inputs.get("marginPct", 20))   # PERCENT of withdrawable (0,100], not a fraction
    tiers = inputs.get("leverageTiers", _DEFAULT_TIERS)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()
    hour = time.gmtime(now).tm_hour

    # signal-dedup (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    au = asset.upper()
    last = recent.get(au)
    if last is not None and (now - last) < ttl:
        return []

    data = _asset_data(ctx, asset, dex, ["5m", "15m", "1h", "4h"], True)
    if not data:
        return []
    candles = data.get("candles", {}) or {}
    ctx_block = data.get("asset_context", {}) or {}
    funding = scoring._f(ctx_block.get("funding", 0))
    oi = scoring._f(ctx_block.get("openInterest", 0))

    btc_mom_1h = 0.0
    if macro_asset:
        mdata = _asset_data(ctx, macro_asset, "", ["1h"], False)
        if mdata:
            btc_mom_1h = scoring.mom((mdata.get("candles", {}) or {}).get("1h", []), 1)

    sm = _sm_for_asset(ctx, asset)

    th = scoring.build_thesis(
        candles.get("5m", []), candles.get("15m", []), candles.get("1h", []), candles.get("4h", []),
        funding, oi, btc_mom_1h, sm, hour, inputs,
    )
    # ── per-tick result record → scan-results history (ctx.state, bounded by
    #    state_history_max_count). Read back with ctx.state.recent(n); persists to state.json. ──
    out = []
    if not th:
        t4, s4 = scoring.trend_structure(candles.get("4h", []))
        t1, _ = scoring.trend_structure(candles.get("1h", []))
        m15 = scoring.mom(candles.get("15m", []), 1)
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked", "score": None,
                  "direction": None, "trend4h": t4, "ts4h": round(s4, 3), "trend1h": t1,
                  "mom15m": round(m15, 3)}
        print(f"[kodiak.scan] {asset} HOLD (gate): 4h={t4} {s4:.0%} (need>=75% & directional) | "
              f"1h={t1} | 15m={m15:+.2f}%", file=sys.stderr)
    elif th["score"] < min_score:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "trend4h": th["trend_4h"], "ts4h": th["trend_strength_4h"],
                  "trend1h": th["trend_1h"], "mom15m": th["mom_15m"], "rsi": th["rsi"],
                  "reasons": th["reasons"]}
        print(f"[kodiak.scan] {asset} HOLD: score={th['score']}/{min_score:.0f} {th['direction']} | "
              f"4h={th['trend_4h']} {th['trend_strength_4h']:.0%} rsi={th['rsi']} | {th['reasons']}",
              file=sys.stderr)
    else:
        leverage = scoring.get_leverage(th["score"], tiers)
        recent[au] = now
        result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "leverage": leverage, "trend4h": th["trend_4h"],
                  "ts4h": th["trend_strength_4h"], "trend1h": th["trend_1h"], "mom15m": th["mom_15m"],
                  "rsi": th["rsi"], "reasons": th["reasons"]}
        print(f"[kodiak.scan] {asset} EMIT: score={th['score']} {th['direction']} {leverage}x | {th['reasons']}",
              file=sys.stderr)
        out = [{
            "asset": asset,
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,             # conviction-tiered (5/6/7); runtime applies it
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "trend4h": th["trend_4h"], "trendStrength4h": th["trend_strength_4h"], "trend1h": th["trend_1h"],
                "mom15mPct": th["mom_15m"], "mom1hPct": th["mom_1h"], "mom4hPct": th["mom_4h"],
                "fundingRate": th["funding"], "oiTrend": "rising" if th["oi"] > 0 else "unknown",
                "btcMom1hPct": th["btc_mom_1h"], "rsi": th["rsi"],
                "smPctOfTopTraders": th["sm_pct"], "smTraderCount": th["sm_traders"],
                "smCc15m": th["sm_cc15m"], "smAligned": th["sm_aligned"],
                "reasons": th["reasons"],
            },
        }]

    # ── persist dedup map + this tick's result EVERY tick (was emit-only); self-trims
    #    at state_history_max_count. Read the history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[kodiak.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
