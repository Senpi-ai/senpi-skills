"""GANNET — supervised scanner: the daily dip cascade.

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - returns early, after that read, outside the entry window that opens at each UTC midnight —
    the daily candles close then, and the strategy buys at the close or not at all,
  - inside the window, walks the recipe's markets IN ORDER, skipping any already held, reads each
    one's daily candles, keeps only the CLOSED ones, and asks pure `scoring.build_thesis` whether its
    RSI closed under the oversold line,
  - emits the first qualifying markets up to the open slots (one slot = the first washed-out market
    in the cascade), gated on free margin, and remembers each (market, bar) it emitted, so one daily
    close is one entry at most.

Read-only + single-pass. Emits `marginPct` (PERCENT in (0,100]) and the market's `leverage` from the
recipe; the runtime sizes the dollars, owns cooldowns/risk gates, and runs the DSL exit. No daemon,
no push_signal, no create_position. Candle values are strings keyed o/h/l/c/v with t in epoch ms —
scoring._f / closed_candles coerce.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_ALLOWED_ASSETS_DEFAULT = ["xyz:SOXL", "xyz:XYZ100", "xyz:SP500"]
_DAY = 86400
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
        print(f"[gannet.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, 0.0, []
    if not isinstance(ch, dict):
        print(f"[gannet.scan] clearinghouse: non-dict payload {str(ch)[:120]!r}", file=sys.stderr)
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
        print("[gannet.scan] read-sanity guard: margin in use but empty positions — skipping tick",
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
        print(f"[gannet.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not isinstance(md, dict):
        print(f"[gannet.scan] market_get_asset_data({coin}): non-dict payload {str(md)[:120]!r}",
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
    allowed = [str(a) for a in (inputs.get("allowedAssets") or _ALLOWED_ASSETS_DEFAULT) if a]
    margin_pct = float(inputs.get("marginPct", 30))              # PERCENT of withdrawable (0,100]
    if 0 < margin_pct < 1.0:                                      # a pasted fraction (0.30 meant 30)
        margin_pct *= 100.0
    max_slots = int(inputs.get("maxSlots", 1))
    window = float(inputs.get("entryWindowSeconds", _DEFAULT_ENTRY_WINDOW))
    min_notional = float(inputs.get("minNotionalUsd", _DEFAULT_MIN_NOTIONAL))

    account_value, withdrawable, positions = _get_account(ctx)
    if account_value <= 0:
        print("[gannet.scan] WAITING — no account value / unreadable clearinghouse", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held = {scoring.bare(h) for h in held_assets}
    open_slots = max_slots - len(held)

    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - scoring._f(v)) < _KEEP_SIGNALED}
    since_close = now % _DAY
    result = {"ts": now, "held": held_assets, "open_slots": open_slots, "scanned": 0,
              "since_close_s": int(since_close), "emitted": []}

    if open_slots <= 0:
        print(f"[gannet.scan] slots full ({len(held)}/{max_slots}) — the DSL manages the exit", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    if since_close > window and not ctx.dry_run:
        # the daily bars closed at UTC midnight; the next decision is at the next one
        _persist(ctx, signaled, result)
        return []

    candidates, blocked = [], None
    for coin in allowed:
        if scoring.bare(coin) in held:
            continue
        result["scanned"] += 1
        rows = _daily(ctx, coin)
        closed = scoring.closed_candles(rows, _DAY, now) if rows else []
        if not closed or (now - (scoring._t(closed[-1]) / 1000.0 + _DAY) > window and not ctx.dry_run):
            # Unreadable, or its latest close is missing. This market may have qualified, so nothing
            # after it in the order may take its place: the cascade stops here, nothing is marked
            # sent, and the next tick asks again.
            blocked = coin
            break
        th = scoring.build_thesis(coin, closed, inputs)
        if not th:
            continue
        key = f"{scoring.bare(coin)}|LONG|{th['signal_bar_t']}"
        if key in signaled:
            continue                                             # this close already went out
        candidates.append((key, th))

    if blocked:
        result["blocked_by"] = blocked
        print(f"[gannet.scan] WAITING on {blocked}: no current close, so nothing behind it in the "
              f"cascade is bought at this tick", file=sys.stderr)
    out = []
    remaining = withdrawable
    for key, th in scoring.cascade(candidates, open_slots):
        # The runtime opens a scanner's signals one at a time, each as marginPct of the free margin
        # left after the ones before it, so the projection shrinks the same way.
        margin = remaining * margin_pct / 100.0
        if margin * th["leverage"] < min_notional:
            print(f"[gannet.scan] free margin ${remaining:.2f} cannot fund a slot at "
                  f"{margin_pct:g}% x {th['leverage']}x — holding {th['coin']}", file=sys.stderr)
            break
        remaining -= margin
        signaled[key] = now
        result["emitted"].append({"coin": th["coin"], "rsi": th["rsi"], "leverage": th["leverage"]})
        print(f"[gannet.scan] EMIT {th['coin']} LONG {th['leverage']}x marginPct={margin_pct:g}% "
              f"| {th['reasons']}", file=sys.stderr)
        data = {k: v for k, v in {
            "rsi": th["rsi"],
            "rsiPrev": th["rsi_prev"],
            "direction": th["direction"],
            "closePx": th["close_px"],
            "signalBarTime": th["signal_bar_t"],
            "leverage": th["leverage"],
            "stopPricePct": th["stop_price_pct"],
            "reasons": th["reasons"],
            "heldAssets": held_assets,
        }.items() if v is not None}
        out.append({
            "asset": th["coin"],
            "direction": "LONG",
            "marginPct": margin_pct,            # PERCENT in (0,100] — the runtime sizes the dollars
            "leverage": th["leverage"],         # per market from the recipe; see scoring.leverage_for
            "data": data,
        })

    if not out:
        print(f"[gannet.scan] WAITING — nothing in the cascade closed under the oversold line "
              f"(scanned {result['scanned']}, held {held_assets})", file=sys.stderr)
    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[gannet.scan] WARNING: state append failed; next tick may re-emit a suppressed "
              f"signal: {exc!r}", file=sys.stderr)
