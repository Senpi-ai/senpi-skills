"""KITE — supervised scanner: ICT/SMC market-structure + divergence entries.

Per tick: for each name in the whitelist (BTC/ETH/SOL/FARTCOIN by default), read a higher
timeframe (4h) for the break-of-structure + impulse leg and a lower timeframe (15m) for the
retracement + divergence, run the pure SMC engine (fractal -> BOS -> 0.618 entry / 0.786 stop
-> RSI-divergence confirm), and emit an intent with the entry/stop/1R-2R-3R targets on it. The
runtime sizes, owns slots/dedup, and trails the DSL exit (a step-up ladder approximating the
33/33/33 scale-out until native partial take-profit ships). Read-only, single-pass, long/short."""

import sys
import time

import scoring

_ASSETS = ["BTC", "ETH", "SOL", "FARTCOIN"]


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[kite.scan] {label} read failed: {exc!r}", file=sys.stderr)
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
        "asset": name, "candle_intervals": ["4h", "15m"],
        "include_funding": False, "include_order_book": False, "dex": _dex_of(name),
    }, f"market_get_asset_data({name})")


def scan(inputs, ctx):
    now = time.time()
    assets = inputs.get("assets") or _ASSETS
    max_slots = int(scoring._f(inputs.get("maxSlots"), 4))
    min_score = scoring._f(inputs.get("minScore"), 4)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 14400)

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (st.get("recent") or {}).items() if (now - v) < ttl}

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent, "result": {"ts": now}})
        except Exception as exc:  # noqa: BLE001
            print(f"[kite.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    held = _held(ctx)
    if held is None:
        return []
    free = max_slots - len(held)
    if free <= 0:
        _persist()
        return []

    out = []
    for coin in assets:
        if free <= 0:
            break
        bare = str(coin).split(":", 1)[-1].upper()
        if bare in held:
            continue
        if recent.get(bare) is not None and (now - recent[bare]) < ttl:
            continue
        md = _asset_data(ctx, coin)
        if not md:
            continue
        candles = md.get("candles", {}) or {}
        th = scoring.smc_thesis(coin, candles.get("4h", []), candles.get("15m", []), inputs)
        if not th or th["score"] < min_score:
            continue
        mgn, lev = scoring.sizing(th["score"], inputs)
        out.append({
            "asset": coin, "direction": th["direction"], "marginPct": mgn, "leverage": lev,
            "data": {"score": th["score"], "direction": th["direction"],
                     "entry": round(th["entry"], 8), "stop786": round(th["stop"], 8),
                     "targets": th["targets"], "riskPct": th["risk_pct"],
                     "divergence": th["divergence"], "reasons": th["reasons"]},
        })
        recent[bare] = now
        free -= 1
        print(f"[kite.scan] SMC {th['direction']} {coin} score={th['score']} "
              f"entry={th['entry']:.6g} stop={th['stop']:.6g} {lev}x {mgn}%", file=sys.stderr)

    _persist()
    return out
