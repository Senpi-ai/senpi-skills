"""HARE — supervised scanner: crypto-majors (BTC/ETH) session scalper.

Per tick: check the UTC clock — OUTSIDE the configured Asia/London/US high-volume windows it
reads NOTHING and emits nothing (cheap + no fee exposure). Inside a window, for each of BTC/ETH
that is not already held and not inside its per-asset cooldown (the hard frequency cap), read
15m + 1h candles, score a sub-hourly momentum burst that clears the volume/fee bar, and emit a
conviction-sized 5-10x intent. Maker-only entry + short DSL exit live in runtime.yaml. Read-only,
single-pass. The fee guard is the whole point: session-gated, cooldown-capped, maker-only."""

import sys
import time

import scoring

_ASSETS = ["BTC", "ETH"]
_DEFAULT_WINDOWS = [
    {"name": "asia",   "start": "00:00", "end": "04:00"},
    {"name": "london", "start": "07:00", "end": "11:00"},
    {"name": "us",     "start": "13:30", "end": "17:00"},
]


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[hare.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


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
        "asset": name, "candle_intervals": ["15m", "1h"],
        "include_funding": False, "include_order_book": False,
    }, f"market_get_asset_data({name})")


def scan(inputs, ctx):
    now = time.time()
    gm = time.gmtime(now)
    minute_of_day = gm.tm_hour * 60 + gm.tm_min
    windows = inputs.get("sessionWindows") or _DEFAULT_WINDOWS
    assets = inputs.get("assets") or _ASSETS
    max_slots = int(scoring._f(inputs.get("maxSlots"), 2))
    min_score = scoring._f(inputs.get("minScore"), 4)
    cooldown = scoring._f(inputs.get("cooldownMinutes"), 45) * 60.0

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (st.get("recent") or {}).items() if (now - v) < cooldown}

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent, "result": {"ts": now}})
        except Exception as exc:  # noqa: BLE001
            print(f"[hare.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    in_sess, sess = scoring.in_session(minute_of_day, windows)
    if not in_sess:                                   # OUTSIDE a session: read nothing, emit nothing
        _persist()
        return []

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
        if recent.get(bare) is not None and (now - recent[bare]) < cooldown:   # hard frequency cap
            continue
        md = _asset_data(ctx, coin)
        if not md:
            continue
        candles = md.get("candles", {}) or {}
        th = scoring.scalp_thesis(coin, candles.get("15m", []), candles.get("1h", []), sess, inputs)
        if not th or th["score"] < min_score:
            continue
        mgn, lev = scoring.sizing(th["score"], inputs)
        out.append({
            "asset": coin, "direction": th["direction"], "marginPct": mgn, "leverage": lev,
            "data": {"score": th["score"], "direction": th["direction"], "session": sess,
                     "driftPct": th["drift"], "volRatio": th["vol_ratio"],
                     "trend1h": th["trend_1h"], "reasons": th["reasons"]},
        })
        recent[bare] = now
        free -= 1
        print(f"[hare.scan] SCALP {th['direction']} {coin} [{sess}] score={th['score']} "
              f"{lev}x {mgn}%", file=sys.stderr)

    _persist()
    return out
