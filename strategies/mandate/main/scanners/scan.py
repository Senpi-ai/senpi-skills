"""MANDATE — supervised scanner: build & hold a diversified, no-leverage, capital-
preservation basket.

Per tick (slow cadence): read this wallet's open positions (dual-DEX), count them per
asset class, and if a slot is free AND its class is under the per-class cap, walk the
curated whitelist (crypto blue chips, equity indices + blue-chip names, metals, energy),
apply a multi-timeframe LONG quality gate and a fee hurdle (expected move must clear
round-trip cost), and emit a capped, NO-LEVERAGE allocation. Long-only, rotate-by-attrition
(DSL is the only exit — a preservation book is held and rebalanced, never force-closed).
Read-only, single-pass. Being inactive is a valid outcome."""

import sys
import time

import scoring

_DEFAULT_CLASSES = {
    "crypto":  ["BTC", "ETH", "SOL", "HYPE"],
    "equity":  ["xyz:SP500", "xyz:XYZ100", "xyz:NVDA", "xyz:AAPL"],
    "metals":  ["xyz:GOLD", "xyz:SILVER"],
    "energy":  ["xyz:BRENTOIL"],
}


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[mandate.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _dex_of(name):
    return "xyz" if str(name).lower().startswith("xyz:") else ""


def _held(ctx):
    """Bare-uppercase coins with an open position (dual-DEX main+xyz), or None on failure."""
    d = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet}, "clearinghouse")
    if not isinstance(d, dict):
        return None
    rows = []
    for sec in ("main", "xyz"):
        s = d.get(sec)
        if isinstance(s, dict):
            rows.extend(s.get("assetPositions", s.get("asset_positions", [])) or [])
    if not rows:
        rows = d.get("assetPositions", d.get("asset_positions", [])) or []
    out = set()
    for e in rows:
        pos = e.get("position", e) if isinstance(e, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        if coin and scoring._f(pos.get("szi")) != 0:
            out.add(coin.split(":", 1)[-1].upper())
    return out


def _asset_data(ctx, name):
    return _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": ["4h", "1d"],
        "include_funding": False, "include_order_book": False, "dex": _dex_of(name),
    }, f"market_get_asset_data({name})")


def scan(inputs, ctx):
    now = time.time()
    classes = inputs.get("classes") or _DEFAULT_CLASSES
    max_slots = int(scoring._f(inputs.get("maxSlots"), 8))
    max_per_class = int(scoring._f(inputs.get("maxPerClass"), 2))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 43200)

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (st.get("recent") or {}).items() if (now - v) < ttl}

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent, "result": {"ts": now}})
        except Exception as exc:  # noqa: BLE001
            print(f"[mandate.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    held = _held(ctx)
    if held is None:
        return []
    class_count = {}
    for h in held:
        cls = scoring.class_of(h, classes)
        class_count[cls] = class_count.get(cls, 0) + 1
    free = max_slots - len(held)
    if free <= 0:
        _persist()
        return []

    scored = []
    for cls, coins in classes.items():
        if class_count.get(cls, 0) >= max_per_class:
            continue
        for coin in coins:
            bare = str(coin).split(":", 1)[-1].upper()
            if bare in held:
                continue
            if recent.get(bare) is not None and (now - recent[bare]) < ttl:
                continue
            md = _asset_data(ctx, coin)
            if not md:
                continue
            candles = md.get("candles", {}) or {}
            ok, score, strength, reasons = scoring.quality(
                candles.get("4h", []), candles.get("1d", []), inputs)
            if not ok:
                continue
            edge = scoring.atr_pct(candles.get("4h", []), int(scoring._f(inputs.get("atrBars"), 14)))
            if not scoring.passes_fee_hurdle(edge, inputs):
                continue
            scored.append({"coin": coin, "cls": cls, "score": score, "edge": edge,
                           "reasons": reasons + [f"expected move {edge:.2f}% clears fee hurdle"]})

    scored.sort(key=lambda s: s["score"], reverse=True)
    out = []
    for s in scored:
        if free <= 0:
            break
        if class_count.get(s["cls"], 0) >= max_per_class:      # re-check as we fill (diversification)
            continue
        bare = str(s["coin"]).split(":", 1)[-1].upper()
        out.append({
            "asset": s["coin"], "direction": "LONG",
            "marginPct": scoring.alloc_pct(s["score"], inputs), "leverage": 1,   # NO leverage
            "data": {"score": s["score"], "direction": "LONG", "assetClass": s["cls"],
                     "edgePct": round(s["edge"], 3), "reasons": s["reasons"]},
        })
        recent[bare] = now
        class_count[s["cls"]] = class_count.get(s["cls"], 0) + 1
        free -= 1
        print(f"[mandate.scan] ADD {s['coin']} ({s['cls']}) score={s['score']} "
              f"alloc={out[-1]['marginPct']}% 1x", file=sys.stderr)

    if not out:
        print(f"[mandate.scan] no add: held={len(held)}/{max_slots} — being inactive is valid",
              file=sys.stderr)
    _persist()
    return out
