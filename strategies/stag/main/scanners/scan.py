"""STAG — supervised scanner (Runtime 3.0 port of the v2 Stag Parabolic-Run Hunter).

Multi-asset whitelist. Each tick: for every whitelisted asset, read 4h candles
(needs >=200 for the SMA) + the smart-money lean (leaderboard_get_markets), score
via the pure `scoring.build_thesis` (ALL FIVE gates), collect the passers, and emit
the SINGLE highest-scoring candidate (v2 emitted candidates.sort(...)[0]) as a LONG
with a `marginPct` sizing intent + a top-level `leverage`. The runtime sizes the
dollars, owns the cooldowns/slots/dedup, and trails the WIDE parabolic_runner DSL
exit. Read-only + single-pass. No daemon, no push_signal.

Held assets are skipped (the runtime owns 1 slot; we never pyramid). Per-asset
race-window dedup lives in ctx.state (mirrors the v2 recent-signals TTL). Most ticks
return empty by design — these setups are rare."""

import sys
import time

import scoring

_DEFAULT_WHITELIST = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_TTL = 240            # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup
_DEFAULT_MAX_LEVERAGE = 5     # v2 MAX_LEVERAGE
_DEFAULT_LEVERAGE = 4         # v2 DEFAULT_LEVERAGE
_DEFAULT_MIN_SCORE = 5        # v2 DEFAULT_MIN_SCORE


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back
    the whole tick. Returns None so the per-asset degrade path (skip this asset)
    or the SM degrade path (drop the asset on a missing SM lean) applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[stag.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _fetch_candles(ctx, asset):
    """4h candles for one asset. Read-guarded; returns [] on failure/empty."""
    data = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["4h"],
        "include_funding": False,
        "include_order_book": False,
    })
    if not data or (isinstance(data, dict) and not data.get("success", True)):
        return []
    block = data.get("data", data) if isinstance(data, dict) else {}
    return (block.get("candles", {}) or {}).get("4h", []) if isinstance(block, dict) else []


def _sm_markets(ctx):
    """One leaderboard_get_markets read per tick (shared across the whitelist).
    Returns the raw markets list (or None). Read-guarded — a failure degrades
    every asset's SM gate to a fail (drop), never crashes the tick."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": 100})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return None
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    return markets if isinstance(markets, list) else None


def _sm_direction(markets, asset):
    """Port of v2 fetch_sm_direction: net smart-money lean for `asset`.
    Returns (direction, tilt_pct) — (None, 0.0) if the asset isn't found.
    `markets` is the pre-fetched list from _sm_markets."""
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
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
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


def _held_assets(ctx):
    """Read open positions off the wallet so we never re-enter a held name
    (the runtime owns 1 slot; this mirrors v2's held_assets dedup). Read-guarded:
    on a failed/corrupt read, returns ([], False) so the tick still scans but
    can't size off a bad account read — runtime sizing/slots are the backstop."""
    if not getattr(ctx, "wallet", ""):
        return set()
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return set()
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    held = set()
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if scoring._f(pos.get("szi", 0)) != 0:
                coin = str(pos.get("coin", "")).upper()
                if coin:
                    held.add(coin)
    return held


def scan(inputs, ctx):
    whitelist = inputs.get("whitelist", _DEFAULT_WHITELIST)
    min_score = int(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", 25))      # PERCENT of withdrawable (0,100], not a fraction
    max_lev = int(inputs.get("maxLeverage", _DEFAULT_MAX_LEVERAGE))
    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), max_lev)   # v2 min(leverage, MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    # cross-tick dedup map (race-window) — mirror v2 recent-signals TTL
    recent = {k: v for k, v in ((ctx.state.last() or {}).get("recent", {}) if ctx.state else {}).items()
              if (now - v) < ttl}

    held = _held_assets(ctx)
    markets = _sm_markets(ctx)   # one SM read shared across the whitelist

    candidates = []
    scanned = []
    for asset in whitelist:
        a = str(asset).upper()
        if a in held or (recent.get(a) is not None and (now - recent[a]) < ttl):
            scanned.append({"asset": a, "skipped": "held_or_recent"})
            continue
        candles = _fetch_candles(ctx, a)
        sm = _sm_direction(markets, a)
        th = scoring.build_thesis(candles, sm, inputs)
        if th and th["score"] >= min_score:
            th["coin"] = a
            candidates.append(th)
            scanned.append({"asset": a, "gate": "pass", "score": th["score"], "trend_pct": th["trend_pct"]})
        else:
            scanned.append({"asset": a, "gate": "blocked", "candles": len(candles)})

    out = []
    emitted = None
    if candidates:
        # v2: candidates.sort(key=lambda c: (score, trend_pct), reverse=True); best = [0]
        candidates.sort(key=lambda c: (c["score"], c["trend_pct"]), reverse=True)
        best = candidates[0]
        coin = best["coin"]
        out = [{
            "asset": coin,
            "direction": "LONG",                 # LONG only — parabolic crashes are too fast for shorts
            "marginPct": margin_pct,             # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,                # runtime applies/clamps to venue max
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": "LONG",
                "trendPct": best.get("trend_pct") or 0.0,
                "shortStrengthPct": best.get("short_strength_pct") or 0.0,
                "volRatio": best.get("vol_ratio") or 0.0,
                "highBarsAgo": best.get("high_bars_ago") or 0,
                "smTiltPct": best.get("sm_tilt_pct") or 0.0,
                "reasons": best["reasons"],
            },
        }]
        recent[coin] = now
        emitted = {"coin": coin, "score": best["score"], "trend_pct": best["trend_pct"],
                   "leverage": leverage, "margin_pct": margin_pct, "reasons": best["reasons"][:6]}
        print(f"[stag.scan] EMIT {coin} LONG score={best['score']} {leverage}x "
              f"trend={best['trend_pct']:+.1f}% vol={best.get('vol_ratio')} | {best['reasons']}",
              file=sys.stderr)
    else:
        print(f"[stag.scan] WAITING — no asset cleared all five parabolic gates "
              f"(whitelist={whitelist}, held={sorted(held)})", file=sys.stderr)

    # persist dedup map + this tick's scan record every tick (self-trims at
    # state_history_max_count). Read history via ctx.state.recent(n).
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": {"ts": now, "emitted": emitted, "scanned": scanned}})
        except Exception as exc:  # noqa: BLE001
            print(f"[stag.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
