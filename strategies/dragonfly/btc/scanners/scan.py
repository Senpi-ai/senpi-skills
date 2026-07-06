"""DRAGONFLY — supervised scanner (Runtime 3.0, user-spec port).

Single-asset instance of a two-leg BTC+HYPE composite-momentum book. Reads the
asset's candles (1h/4h) + funding + order book + OI velocity, the PAIR asset's
candles (correlation / gap / divergence factors), the market-wide funding
regime, smart-money lean (leaderboard_get_markets), and — when enabled — BTC
cross-asset flows; scores via the pure `scoring.compute` (directional 0-10
composite, LONG >= 7 / SHORT <= 4); and emits ONE conviction-banded signal.

Read-only + single-pass. Emits per-signal `leverage` + `marginPct` (banded
apex/good/base, scaled by the session modifier); the runtime sizes the dollars,
owns cooldowns / daily caps / drawdown halts, and trails the DSL exit.

LEARNING LEDGER: every tick appends {result: {components,...}} to ctx.state —
the review surface for the weekly weight-retune loop (senpi-improve-trades).
Weights are live-editable in runtime.yaml `inputs.weights`; there is NO
in-process weight mutation (the source spec's learner.py) by design.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DEFAULT_TTL = 1800


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade the factor, never the tick
        print(f"[dragonfly.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else None


def _asset_data(ctx, asset, dex, funding=False, order_book=False):
    return _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "include_funding": funding,
        "include_order_book": order_book,
        "dex": dex,
    }, f"market_get_asset_data({asset})")


def _funding_regime(ctx):
    data = _read(ctx, "market_get_funding_regime", {}, "market_get_funding_regime")
    if not isinstance(data, dict):
        return None, 0.0
    return data.get("regime"), scoring._f(data.get("regime_duration_hours"))


def _sm_for_asset(ctx, asset):
    """Net smart-money lean for `asset` from leaderboard_get_markets (kodiak port).
    Returns {direction, pct, traders, cc_15m} or None."""
    data = _read(ctx, "leaderboard_get_markets", {"limit": 100}, "leaderboard_get_markets")
    if data is None:
        return None
    markets = data.get("markets", data) if isinstance(data, dict) else data
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None
    want = asset.upper()
    long_pct = short_pct = 0.0
    traders, cc_15m, found = 0, 0.0, False
    for m in markets:
        if not isinstance(m, dict) or str(m.get("token", "")).upper() != want:
            continue
        found = True
        d = str(m.get("direction", "")).lower()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders += int(m.get("trader_count", m.get("traderCount", 0)) or 0)
        cc_15m = scoring._f(m.get("contribution_pct_change_15m", 0))
        if d == "long":
            long_pct = pct
        elif d == "short":
            short_pct = pct
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


def _oi_1h(asset_data):
    """Flat key oi_velocity.oi_change_pct_1h ONLY — the nested form is the known
    silent-None bug (reference_cobra_antipattern)."""
    oi_vel = asset_data.get("oi_velocity")
    if not isinstance(oi_vel, dict):
        return None
    val = oi_vel.get("oi_change_pct_1h")
    return None if val is None else scoring._f(val)


def scan(inputs, ctx):
    asset = inputs.get("asset", "BTC")
    pair = inputs.get("pairAsset", "")
    dex = inputs.get("dex", "")
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), _DEFAULT_TTL)
    use_flow = bool(inputs.get("useCrossFlow", False))
    now = time.time()

    # dedup (defence-in-depth alongside the runtime's cooldown gates)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    au = asset.upper()
    if recent.get(au) is not None and (now - recent[au]) < ttl:
        return []

    data = _asset_data(ctx, asset, dex, funding=True, order_book=True)
    if not data:
        return []
    candles = data.get("candles", {}) or {}
    ctx_block = data.get("asset_context", {}) or {}
    order_book = data.get("order_book", data.get("orderBook", {})) or {}

    pair_candles = {}
    if pair:
        pdata = _asset_data(ctx, pair, "")
        if pdata:
            pair_candles = pdata.get("candles", {}) or {}

    regime, regime_hours = _funding_regime(ctx)
    sm = _sm_for_asset(ctx, asset)
    flow = None
    if use_flow:
        flow = _read(ctx, "market_get_cross_asset_flows",
                     {"leader_asset": inputs.get("flowLeader", "BTC")},
                     "market_get_cross_asset_flows")

    view = {
        "asset": asset,
        "c1": candles.get("1h", []), "c4": candles.get("4h", []),
        "pair_c1": pair_candles.get("1h", []), "pair_c4": pair_candles.get("4h", []),
        "oi_1h": _oi_1h(data),
        "funding": scoring._f(ctx_block.get("funding")),
        "premium": scoring._f(ctx_block.get("premium")),
        "spread_pct": scoring.spread_pct_from_book(order_book),
        "regime": regime, "regime_hours": regime_hours,
        "sm": sm, "flow": flow,
        "min_leader_move_pct": inputs.get("minLeaderMovePct", 2.0),
        "min_follow_rate": inputs.get("minFollowRate", 0.8),
    }

    th = scoring.compute(view, inputs, ts=now)

    out = []
    if th is None:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked",
                  "score": None, "direction": None}
        print(f"[dragonfly.scan] {asset} HOLD (gate: candles/spread)", file=sys.stderr)
    elif not th["direction"]:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "pass",
                  "score": th["score"], "direction": None,
                  "components": th["components"]}
        print(f"[dragonfly.scan] {asset} HOLD: score={th['score']} (need >= "
              f"{inputs.get('longThreshold', 7)} or <= {inputs.get('shortThreshold', 4)}) "
              f"| {th['reasons'][:3]}", file=sys.stderr)
    else:
        recent[au] = now
        result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass",
                  "score": th["score"], "direction": th["direction"], "band": th["band"],
                  "leverage": th["leverage"], "marginPct": th["margin_pct"],
                  "components": th["components"]}
        print(f"[dragonfly.scan] {asset} EMIT: {th['direction']} score={th['score']} "
              f"band={th['band']} {th['leverage']}x margin={th['margin_pct']}% | "
              f"{th['reasons']}", file=sys.stderr)
        out = [{
            "asset": asset,
            "direction": th["direction"],
            "marginPct": th["margin_pct"],     # PERCENT of withdrawable — runtime sizes it
            "leverage": th["leverage"],
            "data": {
                "score": th["score"], "leverage": th["leverage"], "direction": th["direction"],
                "band": th["band"], "strength": th["strength"],
                "sessionFactor": th["session_factor"], "sessionLabel": th["session_label"],
                "components": th["components"],
                "regime": regime or "UNKNOWN",
                "smPct": scoring._f((sm or {}).get("pct"), 50),
                "premiumPct": round(view["premium"] * 100, 5),
                "oiChange1h": view["oi_1h"] if view["oi_1h"] is not None else 0.0,
                "reasons": th["reasons"],
            },
        }]

    # learning ledger — persists dedup + per-tick components every tick
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[dragonfly.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
