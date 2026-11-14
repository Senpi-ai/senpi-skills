"""MUSTANG — supervised scanner: the Nasdaq long-term rider.

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - returns early, after that read, when the one slot is full or outside the entry window that opens
    at each UTC-midnight daily close — the strategy buys at the close or not at all,
  - inside the window, reads the regime market's daily + weekly candles and the trade market's daily
    candles (one read when they are the same market), keeps only CLOSED bars, waits unless the
    regime's latest daily bar is the traded close and its weekly series is current, and asks pure
    `scoring.regime` / `scoring.build_thesis` whether this close is an entry,
  - emits at most one LONG, gated on free margin, and remembers the (market, bar) it emitted, so one
    daily close is one entry at most.

Read-only + single-pass. Emits `marginPct` (PERCENT in (0,100]) and `leverage` from the recipe; the
runtime sizes the dollars, owns cooldowns/risk gates, and runs the DSL exit. No daemon, no
push_signal, no create_position. Candle values are strings keyed o/h/l/c/v with t in epoch ms —
scoring._f / closed_candles coerce.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DAY = 86400
_DEFAULT_TRADE = "xyz:XYZ100"
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
        print(f"[mustang.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, 0.0, []
    if not isinstance(ch, dict):
        print(f"[mustang.scan] clearinghouse: non-dict payload {str(ch)[:120]!r}", file=sys.stderr)
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
        print("[mustang.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, 0.0, []
    return account_value, withdrawable, positions


def _candles(ctx, coin, intervals):
    """{interval: [rows]} for `coin`, or None. Read-guarded, type-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": list(intervals),
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[mustang.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not isinstance(md, dict):
        print(f"[mustang.scan] market_get_asset_data({coin}): non-dict payload {str(md)[:120]!r}",
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
    allowed = [str(a) for a in (inputs.get("allowedAssets") or [_DEFAULT_TRADE]) if a]
    trade = allowed[0]
    regime_asset = str(inputs.get("regimeAsset") or trade)
    margin_pct = float(inputs.get("marginPct", 30))              # PERCENT of withdrawable (0,100]
    if 0 < margin_pct < 1.0:                                      # a pasted fraction (0.30 meant 30)
        margin_pct *= 100.0
    window = float(inputs.get("entryWindowSeconds", _DEFAULT_ENTRY_WINDOW))
    min_notional = float(inputs.get("minNotionalUsd", _DEFAULT_MIN_NOTIONAL))

    account_value, withdrawable, positions = _get_account(ctx)
    if account_value <= 0:
        print("[mustang.scan] WAITING — no account value / unreadable clearinghouse", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - scoring._f(v)) < _KEEP_SIGNALED}
    since_close = now % _DAY
    result = {"ts": now, "held": held_assets, "since_close_s": int(since_close), "emitted": []}

    if held_assets:
        print(f"[mustang.scan] holding {held_assets} — the DSL manages the exit", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    if since_close > window and not ctx.dry_run:
        _persist(ctx, signaled, result)              # the next decision is at the next daily close
        return []

    same = scoring.bare(regime_asset) == scoring.bare(trade)
    reg_c = _candles(ctx, regime_asset, ["1d", "1w"])
    trade_c = reg_c if same else _candles(ctx, trade, ["1d"])
    if not reg_c or not trade_c:
        _persist(ctx, signaled, result)
        return []
    reg_daily = scoring.closed_candles(reg_c.get("1d"), _DAY, now)
    reg_weekly = scoring.closed_candles(reg_c.get("1w"), scoring.WEEK, now)
    trade_daily = scoring.closed_candles(trade_c.get("1d"), _DAY, now)
    if not reg_daily or not trade_daily:
        _persist(ctx, signaled, result)
        return []
    # The regime is read at the traded close or not at all: the regime market's latest daily bar must
    # be the traded market's, and its weekly series must include the last weekly close. A row the feed
    # dropped would otherwise hand the decision yesterday's trend; nothing is marked sent, so the next
    # tick reads again.
    decision_close = scoring._t(trade_daily[-1]) / 1000.0 + _DAY
    if (scoring._t(reg_daily[-1]) != scoring._t(trade_daily[-1]) or not reg_weekly
            or decision_close - (scoring._t(reg_weekly[-1]) / 1000.0 + scoring.WEEK) >= scoring.WEEK):
        print(f"[mustang.scan] WAITING — {regime_asset}'s candles are not current at {trade}'s latest "
              f"close; the regime is read only at that close", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    reg = scoring.regime(reg_daily, reg_weekly, inputs)
    if reg is None:
        print(f"[mustang.scan] WAITING — {regime_asset} has {len(reg_weekly)} closed weekly candles, "
              f"fewer than regimeWeeks={inputs.get('regimeWeeks', 40)}", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    result["regime_up"] = reg["up"]
    if not trade_daily:
        _persist(ctx, signaled, result)
        return []
    bar_close = scoring._t(trade_daily[-1]) / 1000.0 + _DAY
    if now - bar_close > window and not ctx.dry_run:
        _persist(ctx, signaled, result)              # a stale close is not bought late
        return []
    th = scoring.build_thesis(trade, trade_daily, reg, inputs)
    if not th:
        print(f"[mustang.scan] WAITING — regime {'up' if reg['up'] else 'down'}, no entry at this close",
              file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    key = f"{scoring.bare(trade)}|LONG|{th['signal_bar_t']}"
    if key in signaled:
        _persist(ctx, signaled, result)              # this close already went out
        return []
    if withdrawable * margin_pct / 100.0 * th["leverage"] < min_notional:
        print(f"[mustang.scan] free margin ${withdrawable:.2f} cannot fund {margin_pct:g}% x "
              f"{th['leverage']}x — holding", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []

    signaled[key] = now
    result["emitted"].append({"coin": th["coin"], "leg": th["leg"], "rsi": th["rsi"]})
    print(f"[mustang.scan] EMIT {th['coin']} LONG {th['leverage']}x marginPct={margin_pct:g}% "
          f"leg={th['leg']} | {th['reasons']}", file=sys.stderr)
    out = [{
        "asset": th["coin"],
        "direction": "LONG",
        "marginPct": margin_pct,            # PERCENT in (0,100] — the runtime sizes the dollars
        "leverage": th["leverage"],
        "data": {
            "leg": th["leg"],
            "direction": th["direction"],
            "rsi": th["rsi"],
            "regimeUp": th["regime_up"],
            "regimeClose": th["regime_close"],
            "regimeAverage": th["regime_average"],
            "signalBarTime": th["signal_bar_t"],
            "leverage": th["leverage"],
            "stopPricePct": th["stop_price_pct"],
            "reasons": th["reasons"],
        },
    }]
    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[mustang.scan] WARNING: state append failed; next tick may re-emit a suppressed "
              f"signal: {exc!r}", file=sys.stderr)
