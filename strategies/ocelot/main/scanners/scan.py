"""OCELOT — supervised scanner: tech when tech leads the trend basket, chips after a crash, else cash.

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - returns early, after that read, when the slot is full or outside the entry window that opens at
    each UTC-midnight daily close — the strategy buys at the close or not at all,
  - inside the window, reads the Nasdaq 100's daily candles and, unless it crashed, the four trend
    basket markets' (each read on the xyz dex), keeps only CLOSED bars, and asks pure
    `scoring.decide` which (if any) entry this close is,
  - emits at most one LONG — the chip fund after a crash, the Nasdaq 100 when it leads — gated on
    free margin, and remembers the (market, bar) it emitted, so one daily close is one entry at most.

Read-only + single-pass. Emits `marginPct` (PERCENT in (0,100]) and the market's `leverage` from the
recipe; the runtime sizes the dollars, owns cooldowns/risk gates, and runs the DSL exit. No daemon, no
push_signal, no create_position. Candle values are strings keyed o/h/l/c/v with t in epoch ms —
scoring._f / closed_candles coerce.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DAY = 86400
_DEFAULT_TECH = "xyz:XYZ100"
_DEFAULT_CRASH = "xyz:SOXL"
_DEFAULT_BASKET = ["xyz:GOLD", "xyz:CL", "xyz:EUR", "xyz:JPY"]
_DEFAULT_ENTRY_WINDOW = 3600    # buy within the first hour after the daily close, or wait for the next
_DEFAULT_MIN_NOTIONAL = 12.0    # the venue's bumped minimum notional — below it an open cannot fund
_KEEP_SIGNALED = 4 * _DAY       # how long a (market, bar) key is remembered


def _dex_for(asset):
    """HIP-3 (`xyz:`) assets must pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, withdrawable, [position dicts]) from strategy_get_clearinghouse_state.

    Equity via max() across the main/xyz views (two views of ONE cross-margined wallet — summing
    double-counts the shared free balance), positions enumerated across both. Read-guarded: a read
    error, a non-dict payload, or margin-in-use with an empty position list (the corrupt-read
    shape) all return a zero account so the tick emits nothing rather than mis-size or pyramid."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the tick
        print(f"[ocelot.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, 0.0, []
    if not isinstance(ch, dict):
        print(f"[ocelot.scan] clearinghouse: non-dict payload {str(ch)[:120]!r}", file=sys.stderr)
        return 0.0, 0.0, []
    data = ch.get("data", ch)
    if not isinstance(data, dict):
        return 0.0, 0.0, []
    positions, account_value, withdrawable, used = [], 0.0, 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s.get("marginSummary"), dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        withdrawable = max(withdrawable, scoring._f(s.get("withdrawable", 0)))
        used = max(used, scoring._f(ms.get("totalMarginUsed", 0)), abs(scoring._f(ms.get("totalNtlPos", 0))))
        for ap in s.get("assetPositions", []) or []:
            if not isinstance(ap, dict):
                continue
            pos = ap.get("position", ap)
            if not isinstance(pos, dict) or scoring._f(pos.get("szi", 0)) == 0:
                continue
            positions.append({"coin": str(pos.get("coin", "")), "margin": scoring._f(pos.get("marginUsed", 0))})
    if used > 1.0 and not positions:
        print("[ocelot.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, 0.0, []
    return account_value, withdrawable, positions


def _daily(ctx, coin):
    """The daily candle list for `coin`, or None. Read-guarded, type-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": ["1d"],
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[ocelot.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not isinstance(md, dict):
        print(f"[ocelot.scan] market_get_asset_data({coin}): non-dict payload {str(md)[:120]!r}",
              file=sys.stderr)
        return None
    if md.get("success") is False:
        return None
    d = md.get("data", md)
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {})
    if not isinstance(candles, dict):
        return None
    rows = candles.get("1d")
    return rows if isinstance(rows, list) else None


def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    sig = (ctx.state.last() or {}).get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def scan(inputs, ctx):
    now = time.time()
    tech = str(inputs.get("techAsset") or _DEFAULT_TECH)
    crash_asset = str(inputs.get("crashAsset") or _DEFAULT_CRASH)
    basket = [str(a) for a in (inputs.get("basketAssets") or _DEFAULT_BASKET) if a]
    period = int(inputs.get("rsiPeriod", 10))
    margin_pct = float(inputs.get("marginPct", 20))              # PERCENT of withdrawable (0,100]
    if 0 < margin_pct < 1.0:                                      # a pasted fraction (0.20 meant 20)
        margin_pct *= 100.0
    window = float(inputs.get("entryWindowSeconds", _DEFAULT_ENTRY_WINDOW))
    min_notional = float(inputs.get("minNotionalUsd", _DEFAULT_MIN_NOTIONAL))

    account_value, withdrawable, positions = _get_account(ctx)
    if account_value <= 0:
        print("[ocelot.scan] WAITING — no account value / unreadable clearinghouse", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - scoring._f(v)) < _KEEP_SIGNALED}
    since_close = now % _DAY
    result = {"ts": now, "held": held_assets, "since_close_s": int(since_close), "emitted": []}
    if held_assets:
        print(f"[ocelot.scan] holding {held_assets} — the DSL manages the exit", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    if since_close > window and not ctx.dry_run:
        _persist(ctx, signaled, result)              # the next decision is at the next daily close
        return []

    tech_rows = _daily(ctx, tech)
    tech_closed = scoring.closed_candles(tech_rows, _DAY, now) if tech_rows else []
    if not tech_closed:
        _persist(ctx, signaled, result)
        return []
    bar_t = int(scoring._t(tech_closed[-1]))
    if now - (bar_t / 1000.0 + _DAY) > window and not ctx.dry_run:
        _persist(ctx, signaled, result)              # a stale close is not bought late
        return []
    tech_rsi = scoring.last_rsi(tech_closed, period)
    basket_rsis = []
    if tech_rsi is not None and tech_rsi >= scoring._f(inputs.get("crashRsi", 30), 30.0):
        for coin in basket:                          # the yardstick is only needed when there was no crash
            rows = _daily(ctx, coin)
            closed = scoring.closed_candles(rows, _DAY, now) if rows else []
            # A basket market counts only at the Nasdaq's own close. A series whose newest row is
            # missing reads as no reading, and decide() then allows no tech entry; nothing is marked
            # sent, so the next tick reads again. (A crash entry never needs the basket.)
            current = bool(closed) and int(scoring._t(closed[-1])) == bar_t
            basket_rsis.append(scoring.last_rsi(closed, period) if current else None)
    leg, yard = scoring.decide(tech_rsi, basket_rsis, inputs)
    result.update({"tech_rsi": None if tech_rsi is None else round(tech_rsi, 2),
                   "yardstick": None if yard is None else round(yard, 2), "leg": leg})
    if leg is None:
        print(f"[ocelot.scan] WAITING — Nasdaq RSI {tech_rsi} vs trend basket {yard}: no entry", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    coin = crash_asset if leg == "crash" else tech
    key = f"{scoring.bare(coin)}|LONG|{bar_t}"
    if key in signaled:
        _persist(ctx, signaled, result)              # this close already went out
        return []
    lev = scoring.leverage_for(coin, inputs)
    if withdrawable * margin_pct / 100.0 * lev < min_notional:
        print(f"[ocelot.scan] free margin ${withdrawable:.2f} cannot fund {margin_pct:g}% x {lev}x — holding",
              file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    stop_roe = scoring._f(inputs.get("stopRoePct", 30), 30.0)
    reasons = ([f"Nasdaq 100 RSI({period}) {tech_rsi:.1f} under the crash line: buy the chip fund"] if leg == "crash" else
               [f"Nasdaq 100 RSI({period}) {tech_rsi:.1f} above the trend basket's {yard:.1f} and not overheated"])
    reasons.append(f"{lev}x: the {stop_roe:g}% ROE stop is {stop_roe / lev:.1f}% of price")
    signaled[key] = now
    result["emitted"].append({"coin": coin, "leg": leg})
    print(f"[ocelot.scan] EMIT {coin} LONG {lev}x marginPct={margin_pct:g}% leg={leg} | {reasons}", file=sys.stderr)
    data = {k: v for k, v in {
        "leg": leg, "direction": "LONG", "techRsi": round(tech_rsi, 2),
        "yardstick": None if yard is None else round(yard, 2), "signalBarTime": bar_t, "leverage": lev,
        "stopPricePct": round(stop_roe / lev, 2), "reasons": reasons,
    }.items() if v is not None}
    _persist(ctx, signaled, result)
    return [{"asset": coin, "direction": "LONG", "marginPct": margin_pct, "leverage": lev, "data": data}]


def _persist(ctx, signaled, result):
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[ocelot.scan] WARNING: state append failed; next tick may re-emit a suppressed "
              f"signal: {exc!r}", file=sys.stderr)
