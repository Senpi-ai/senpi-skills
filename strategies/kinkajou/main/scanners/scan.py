"""KINKAJOU — supervised scanner: leveraged big tech when the Nasdaq is healthy, the index after a washout.

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - returns early, after that read, when the slot is full or outside the entry window that opens at
    each UTC-midnight daily close — the strategy buys at the close or not at all,
  - inside the window, reads the Nasdaq 100's daily + weekly candles and asks pure
    `scoring.nasdaq_state` whether this close is a washout, healthy, or risk; on a healthy close it
    reads each single name's candles and picks `scoring.strongest`,
  - emits at most one LONG, gated on free margin, and remembers the (market, bar) it emitted.

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
_DEFAULT_INDEX = "xyz:XYZ100"
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
        print(f"[kinkajou.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, 0.0, []
    if not isinstance(ch, dict):
        print(f"[kinkajou.scan] clearinghouse: non-dict payload {str(ch)[:120]!r}", file=sys.stderr)
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
        print("[kinkajou.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, 0.0, []
    return account_value, withdrawable, positions


def _candles(ctx, coin):
    """{'1d': [...], '1w': [...]} for `coin`, or None. Read-guarded, type-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": ["1d", "1w"],
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[kinkajou.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not isinstance(md, dict):
        print(f"[kinkajou.scan] market_get_asset_data({coin}): non-dict payload {str(md)[:120]!r}",
              file=sys.stderr)
        return None
    if md.get("success") is False:
        return None
    d = md.get("data", md)
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {})
    return candles if isinstance(candles, dict) else None


def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    sig = (ctx.state.last() or {}).get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def scan(inputs, ctx):
    now = time.time()
    index = str(inputs.get("indexAsset") or _DEFAULT_INDEX)
    names = [str(a) for a in (inputs.get("singleNames") or []) if a]
    margin_pct = float(inputs.get("marginPct", 20))              # PERCENT of withdrawable (0,100]
    if 0 < margin_pct < 1.0:                                      # a pasted fraction (0.20 meant 20)
        margin_pct *= 100.0
    window = float(inputs.get("entryWindowSeconds", _DEFAULT_ENTRY_WINDOW))
    min_notional = float(inputs.get("minNotionalUsd", _DEFAULT_MIN_NOTIONAL))

    account_value, withdrawable, positions = _get_account(ctx)
    if account_value <= 0:
        print("[kinkajou.scan] WAITING — no account value / unreadable clearinghouse", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - scoring._f(v)) < _KEEP_SIGNALED}
    since_close = now % _DAY
    result = {"ts": now, "held": held_assets, "since_close_s": int(since_close), "emitted": []}
    if held_assets:
        print(f"[kinkajou.scan] holding {held_assets} — the DSL manages the exit", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    if since_close > window and not ctx.dry_run:
        _persist(ctx, signaled, result)              # the next decision is at the next daily close
        return []

    md = _candles(ctx, index)
    if not md:
        _persist(ctx, signaled, result)
        return []
    daily = scoring.closed_candles(md.get("1d"), _DAY, now)
    weekly = scoring.closed_candles(md.get("1w"), scoring.WEEK, now)
    if not daily or (now - (scoring._t(daily[-1]) / 1000.0 + _DAY) > window and not ctx.dry_run):
        _persist(ctx, signaled, result)              # a stale close is not bought late
        return []
    bar_t = int(scoring._t(daily[-1]))
    decision_close = bar_t / 1000.0 + _DAY
    state, rsi, avg = scoring.nasdaq_state(daily, weekly, inputs)
    result.update({"state": state, "nasdaq_rsi": None if rsi is None else round(rsi, 2)})
    if state in ("healthy", "risk") and not scoring.weekly_current(weekly, decision_close):
        # "healthy" and "risk" rest on the 40-week average; a lagging weekly feed would read last
        # week's. Nothing is marked sent; the next tick reads again. (A washout needs only the RSI.)
        print(f"[kinkajou.scan] WAITING — {index}'s weekly candles are not current", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    pick = None
    if state == "washout":
        pick = {"coin": index, "signal_bar_t": bar_t, "momentum": None}
    elif state == "healthy":
        measures = []
        for coin in names:
            cm = _candles(ctx, coin)
            d = scoring.closed_candles(cm.get("1d"), _DAY, now) if cm else []
            w = scoring.closed_candles(cm.get("1w"), scoring.WEEK, now) if cm else []
            if not d or int(scoring._t(d[-1])) != bar_t or not scoring.weekly_current(w, decision_close):
                # Unreadable, or not current at the Nasdaq's close. It may be the strongest name, so
                # no other name is bought in its place; nothing is marked sent and the next tick,
                # inside the same entry hour, reads again.
                print(f"[kinkajou.scan] WAITING on {coin}: no current close, so no name is ranked at "
                      f"this tick", file=sys.stderr)
                _persist(ctx, signaled, result)
                return []
            m = scoring.measure(coin, d, w, inputs)
            if m is None:
                if len(w) >= max(int(inputs.get("trendWeeks", 40)), int(inputs.get("momentumWeeks", 13))):
                    # Old enough to rank, yet unmeasurable at this close (e.g. no weekly close near its
                    # 13-week reference). It may be the strongest name, so no other name is bought in
                    # its place; nothing is marked sent and the next tick reads again.
                    print(f"[kinkajou.scan] WAITING on {coin}: it has the history to be ranked but cannot "
                          f"be measured at this close", file=sys.stderr)
                    _persist(ctx, signaled, result)
                    return []
                continue                               # too little history: not a candidate yet
            measures.append(m)
        pick = scoring.strongest(measures)
    if not pick:
        print(f"[kinkajou.scan] WAITING — Nasdaq {state} (RSI {rsi}); nothing to buy at this close", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    coin = pick["coin"]
    key = f"{scoring.bare(coin)}|LONG|{pick['signal_bar_t']}"
    lev = scoring.leverage_for(coin, inputs)
    if key in signaled or withdrawable * margin_pct / 100.0 * lev < min_notional:
        _persist(ctx, signaled, result)              # already sent, or cannot fund the minimum
        return []
    stop_roe = scoring._f(inputs.get("stopRoePct", 30), 30.0)
    reasons = [f"Nasdaq 100 {state}: RSI {rsi:.1f}" + ("" if avg is None else f", above its 40-week average {avg:.4g}")]
    if pick.get("momentum") is not None:
        reasons.append(f"{coin}: the strongest single name, 13-week momentum {pick['momentum'] * 100:+.1f}%")
    reasons.append(f"{lev}x: the {stop_roe:g}% ROE stop is {stop_roe / lev:.1f}% of price")
    signaled[key] = now
    result["emitted"].append({"coin": coin, "state": state})
    print(f"[kinkajou.scan] EMIT {coin} LONG {lev}x marginPct={margin_pct:g}% | {reasons}", file=sys.stderr)
    data = {"state": state, "direction": "LONG", "nasdaqRsi": round(rsi, 2), "signalBarTime": pick["signal_bar_t"],
            "leverage": lev, "stopPricePct": round(stop_roe / lev, 2), "reasons": reasons}
    if pick.get("momentum") is not None:
        data["momentumPct"] = round(pick["momentum"] * 100, 2)
    _persist(ctx, signaled, result)
    return [{"asset": coin, "direction": "LONG", "marginPct": margin_pct, "leverage": lev, "data": data}]


def _persist(ctx, signaled, result):
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[kinkajou.scan] WARNING: state append failed; next tick may re-emit a suppressed "
              f"signal: {exc!r}", file=sys.stderr)
