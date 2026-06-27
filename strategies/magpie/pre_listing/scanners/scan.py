"""MAGPIE · PRE-LISTING book — supervised scanner (Runtime 3.0 port of the v2
Magpie PRE-LISTING leg).

DYNAMIC UNIVERSE. Each tick it discovers IPOPs (pre-IPO perpetuals) off the LIVE
market_list_instruments read by their funding signature (scoring.ipop_passes_universe:
name startswith 'xyz:', not delisted, |funding| <= cap, max_leverage <= cap,
dayNtlVlm >= floor), then for each LIVE IPOP scores the pre-listing trend (4h
structure + 1h + Smart-Money) via the pure scoring module and emits a marginPct
intent + a per-signal leverage clamped to that instrument's venue max. Read-only,
single-pass. NO name is ever emitted that is not present in the live universe read.

EVERY ctx.senpi_mcp.call_tool is read-guarded (degrade, never crash): a transient/
permission error on the instrument-discovery read, a candle read, or the
smart-money read returns the empty/None degrade path — the tick emits fewer (or
zero) signals rather than rolling back the whole tick.

Today the live universe typically has ZERO IPOPs (the marquee names have already
converted to STANDARD equity perps); the book correctly emits nothing until
trade.xyz lists the next pre-IPO perpetual — that is the event it waits for."""

import sys
import time

import scoring

_DEFAULT_TTL = 720   # 12m signal-dedup: don't re-fire an IPOP while a signal is in flight


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error must NOT roll back the tick.
    Returns None so the caller's degrade path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[magpie.pre_listing.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _fetch_ipop_universe(ctx, config):
    """Discover the LIVE IPOP universe off market_list_instruments(dex='xyz').
    Validates every candidate against the live read via scoring.ipop_passes_universe
    — only instruments that actually exist (and clear the funding/leverage/volume
    signature) are returned. Degrades to [] on a failed read."""
    raw = _read(ctx, "market_list_instruments", {"dex": "xyz"})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return []
    universe = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        funding_abs = abs(float(scoring._f(ctx_block, "funding")))
        vol_usd = float(scoring._f(ctx_block, "dayNtlVlm"))
        max_lev = inst.get("max_leverage", 999)
        if not scoring.ipop_passes_universe(
                name, inst.get("is_delisted", False), funding_abs, max_lev, vol_usd, config):
            continue
        universe.append({"name": name, "max_leverage": int(scoring._f({"v": max_lev}, "v", default=5) or 5),
                         "funding": funding_abs, "vol_usd": vol_usd})
    return universe


def _fetch_candles(ctx, asset):
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "dex": "xyz",
        "include_funding": False,
        "include_order_book": False,
    })
    if not md or (isinstance(md, dict) and not md.get("success", True)):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    return (d.get("candles", {}) or {}) if isinstance(d, dict) else {}


def _fetch_sm_direction(ctx, asset):
    """Net smart-money lean for `asset` from leaderboard_get_markets (USER-SCOPE
    auth). Returns (direction|None, tilt_pct). None direction triggers the
    sparse-pre-listing trend-only fallback in scoring."""
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return None, 0.0
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != asset.upper():
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m, "pct_of_top_traders_gain", "longPct")
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


def scan(inputs, ctx):
    min_score = int(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 12))   # PERCENT of withdrawable (0,100], not a fraction
    max_lev = int(inputs.get("maxLeverage", 3))       # IPOP discovery-bounds regime
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    universe = _fetch_ipop_universe(ctx, inputs)
    ipop_names = [u["name"] for u in universe]

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent, "result": {
                "ts": now, "ipop_universe": ipop_names, "emitted": [c["asset"] for c in out]}})
        except Exception as exc:  # noqa: BLE001
            print(f"[magpie.pre_listing.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    out = []
    if not universe:
        # No IPOPs in the live universe — nothing matches the funding signature.
        # This is the steady state; the book waits for the next pre-IPO listing.
        print("[magpie.pre_listing.scan] WAITING — no IPOPs in live xyz universe "
              "(no instrument matches the pre-listing funding signature)", file=sys.stderr)
        _persist()
        return out

    for inst in universe:
        coin = inst["name"]
        cu = coin.upper()
        last = recent.get(cu)
        if last is not None and (now - last) < ttl:        # signal-dedup
            continue
        candles = _fetch_candles(ctx, coin)
        if not candles:
            continue
        c1 = candles.get("1h", []) or []
        c4 = candles.get("4h", []) or []
        sm_dir, sm_tilt = _fetch_sm_direction(ctx, coin)
        th = scoring.build_thesis_pre_listing(coin, c1, c4, sm_dir, sm_tilt, inputs)
        if not th or th["score"] < min_score:
            continue
        leverage = scoring.clamp_leverage(max_lev, inst["max_leverage"])
        if leverage <= 0:
            continue
        out.append({
            "asset": coin,                    # an xyz: name straight from the live universe read
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,             # per-signal, clamped to this instrument's venue max
            "data": {
                "score": th["score"], "direction": th["direction"], "leverage": leverage,
                "ipopFlag": True, "trend4h": th.get("trend4h"), "smTilt": round(th.get("sm_tilt", 0.0), 1),
                "reasons": th["reasons"],
            },
        })
        recent[cu] = now
        print(f"[magpie.pre_listing.scan] EMIT {coin} {th['direction']} {leverage}x "
              f"score={th['score']} | {th['reasons']}", file=sys.stderr)

    _persist()
    return out
