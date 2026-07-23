"""GECKO — Configurable Single-Asset Confluence. "Trade the coin I name."

One user-named asset (inputs.asset, default "BTC"), one position, one supervised
scanner. Each tick:
  1) Resolve the asset + its dex from the string (xyz:* -> the HIP-3 xyz DEX, else main).
  2) Signal-dedup: if a fresh signal fired within recentSignalTtlSeconds, hold.
  3) If we ALREADY hold the asset -> emit nothing (DSL owns the exit; never stack).
  4) Else read its candles (15m/1h/4h) + funding, run bison's confluence thesis
     (trend structure 1h/4h + momentum + RSI + funding; sm=(None,0)), and emit ONE
     LONG/SHORT signal at conviction sizing when score >= minScore.

NEVER closes — the runtime DSL owns every exit; the drawdown_halt is the equity
backstop. Read-only + single-pass; marginPct is a PERCENT in (0,100]. No daemon,
no push_signal. The asset string is emitted WITH its prefix (xyz: preserved).
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    """READ-GUARD: a read error must never crash the tick. Returns the inner data
    (unwrapping a {'data': ...} envelope) or None (degrade — never raise)."""
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[gecko.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _dex_of(name):
    return "xyz" if str(name).lower().startswith("xyz:") else ""


def _asset_data(ctx, name):
    return _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": ["15m", "1h", "4h"],
        "include_funding": True, "include_order_book": False, "dex": _dex_of(name),
    }, f"market_get_asset_data({name})")


def _funding_of(md):
    """Current funding rate as a float (tolerant; 0.0 if absent)."""
    for k in ("funding", "funding_rate", "fundingRate", "current_funding"):
        v = scoring._num((md or {}).get(k))
        if v is not None:
            return v
    fh = (md or {}).get("funding") if isinstance((md or {}).get("funding"), dict) else None
    if isinstance(fh, dict):
        return scoring._f(fh.get("rate", fh.get("current")))
    return 0.0


def _venue_max_of(md):
    """Best-effort venue max leverage from asset_data (tolerant; None if absent).
    Lets gecko respect a low-leverage instrument's own cap on top of maxLeverage —
    important for the any-coin use case (e.g. a low-max xyz equity). Degrades to
    None (then only the maxLeverage input caps). Payload shape NOT live-verified —
    tries asset_context/context.{max_leverage,maxLeverage}."""
    for key in ("asset_context", "context"):
        c = (md or {}).get(key)
        if isinstance(c, dict):
            for k in ("max_leverage", "maxLeverage"):
                v = scoring._num(c.get(k))
                if v is not None and v > 0:
                    return v
    return None


def _held(ctx):
    """Set of bare (prefix-stripped, upper) coins currently held, or None if the
    clearinghouse is unreadable (caller then defers this tick — never blind-opens)."""
    d = _read(ctx, "strategy_get_clearinghouse_state",
              {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(d, dict):
        return None
    out = set()
    # dual-DEX: strategy_get_clearinghouse_state returns {"main": ..., "xyz": ...} —
    # two views of ONE cross-margined wallet, each with its own assetPositions.
    # Reading assetPositions off the TOP level silently yields NOTHING held, so a
    # scanner re-opens names it already holds (pyramiding / failed duplicate opens).
    _rows = []
    for _sec in ("main", "xyz"):
        _s = d.get(_sec)
        if isinstance(_s, dict):
            _rows.extend(_s.get("assetPositions", _s.get("asset_positions", [])) or [])
    if not _rows:  # legacy/flat shape
        _rows = d.get("assetPositions", d.get("asset_positions", [])) or []
    for e in _rows:
        pos = e.get("position", e) if isinstance(e, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        if coin and scoring._f(pos.get("szi")) != 0:
            out.add(coin.split(":", 1)[-1].upper())
    return out


def _append(ctx, recent, result):
    """Persist the dedup map + this tick's result (self-trims at state_history_max_count)."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"recent": recent, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[gecko.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)


def scan(inputs, ctx):
    asset = str(inputs.get("asset") or "BTC")
    min_score = scoring._f(inputs.get("minScore"), 6)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 7200)
    now = time.time()
    bare = asset.split(":", 1)[-1].upper()

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = dict(st.get("recent", {}) or {})

    # ── signal-dedup (defence-in-depth alongside the runtime per-asset cooldown) ──
    last = recent.get(bare)
    if last is not None and (now - last) < ttl:
        print(f"[gecko.scan] {asset} HOLD: recent signal within {ttl:g}s ttl", file=sys.stderr)
        _append(ctx, recent, {"ts": now, "asset": asset, "emitted": False, "gate": "recent_ttl"})
        return []

    # ── single-asset dedup: already holding -> emit nothing (DSL owns the exit) ──
    held = _held(ctx)
    if held is None:
        print("[gecko.scan] clearinghouse unreadable — act next tick", file=sys.stderr)
        return []                                    # no state write — retry cleanly next tick
    if bare in held:
        print(f"[gecko.scan] {asset} HOLD: already holding", file=sys.stderr)
        _append(ctx, recent, {"ts": now, "asset": asset, "emitted": False, "gate": "already_held"})
        return []

    md = _asset_data(ctx, asset)
    if not md:
        print(f"[gecko.scan] {asset} asset_data unreadable — no open this tick", file=sys.stderr)
        return []
    candles = md.get("candles", {}) or {}
    th = scoring.build_thesis(asset, candles.get("15m", []), candles.get("1h", []),
                              candles.get("4h", []), _funding_of(md), (None, 0), inputs)

    out = []
    if not th:
        print(f"[gecko.scan] {asset} HOLD: no direction / insufficient candles", file=sys.stderr)
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "no_thesis"}
    elif th["score"] < min_score:
        print(f"[gecko.scan] {asset} HOLD: score={th['score']}<{min_score:g} "
              f"{th['direction']} | {th['reasons']}", file=sys.stderr)
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "score_low",
                  "score": th["score"], "direction": th["direction"]}
    else:
        band = scoring.band_for(th["score"], inputs)
        lev, mgn = scoring.sizing_for(band, inputs, _venue_max_of(md))
        recent[bare] = now
        out = [{
            "asset": asset,                          # WITH prefix (xyz: preserved)
            "direction": th["direction"],
            "marginPct": mgn,                        # PERCENT of withdrawable — runtime sizes
            "leverage": lev,                         # conviction-tiered; runtime applies it
            "data": {"score": th["score"], "leverage": lev, "direction": th["direction"],
                     "band": band, "reasons": th["reasons"]},
        }]
        result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass",
                  "score": th["score"], "direction": th["direction"],
                  "band": band, "leverage": lev, "marginPct": mgn}
        print(f"[gecko.scan] {asset} EMIT {th['direction']}: score={th['score']} band={band} "
              f"{lev}x {mgn}% | {th['reasons']}", file=sys.stderr)

    _append(ctx, recent, result)
    return out
