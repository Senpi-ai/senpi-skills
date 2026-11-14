"""KUDU — supervised scanner: the quality basket, timed by the Nasdaq.

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - returns early, after that read, outside the entry window that opens at each UTC-midnight daily
    close, or when every slot is full,
  - inside the window, reads the Nasdaq 100's daily candles (the only market data the rule needs),
    keeps the CLOSED bars, and asks pure `scoring.plan` which names to buy at this close: every
    basket name not held unless the Nasdaq is overheated, plus the Nasdaq 100 itself after a washout,
  - emits them gated on free margin (one balance read; the tick's total allowance decrements),
    skipping a leg it cannot fund without stopping, and remembers each (market, bar) it emitted, so
    one daily close is one entry per market at most.

Sizing: every position is its leg's percent of ACCOUNT value (4% a name, 12% the washout leg), so the
ten names are equal clips whatever day they were bought, and a stopped-out name can be re-bought at
the minimum budget. The runtime opens a scanner's signals one at a time, each as a percent of the
free margin left after the ones before it, so for each position in order the scanner emits the
percent of THAT remaining free margin that equals the clip. Read-only +
single-pass; the runtime sizes the dollars, owns cooldowns/risk gates, and runs the DSL exit. No daemon, no
push_signal, no create_position. Candle values are strings keyed o/h/l/c/v with t in epoch ms —
scoring._f / closed_candles coerce.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DAY = 86400
_DEFAULT_TIMING = "xyz:XYZ100"
_DEFAULT_ENTRY_WINDOW = 3600    # buy within the first hour after the daily close, or wait for the next
_DEFAULT_MIN_NOTIONAL = 12.0    # the venue's bumped minimum notional — below it an open cannot fund
_KEEP_SIGNALED = 4 * _DAY       # how long a (market, bar) key is remembered
_HEADROOM = 0.9                 # commit at most 90% of free margin in one tick


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
        print(f"[kudu.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, 0.0, []
    if not isinstance(ch, dict):
        print(f"[kudu.scan] clearinghouse: non-dict payload {str(ch)[:120]!r}", file=sys.stderr)
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
        print("[kudu.scan] read-sanity guard: margin in use but empty positions — skipping tick",
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
        print(f"[kudu.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not isinstance(md, dict):
        print(f"[kudu.scan] market_get_asset_data({coin}): non-dict payload {str(md)[:120]!r}",
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


def _pct(v, d):
    p = float(v if v is not None else d)
    return p * 100.0 if 0 < p < 1.0 else p                        # a pasted fraction (0.05 meant 5)


def scan(inputs, ctx):
    now = time.time()
    timing = str(inputs.get("timingAsset") or _DEFAULT_TIMING)
    washout_asset = str(inputs.get("washoutAsset") or timing)
    basket = [str(a) for a in (inputs.get("basketAssets") or []) if a]
    period = int(inputs.get("rsiPeriod", 10))
    max_slots = int(inputs.get("maxSlots", len(basket) + 1))
    window = float(inputs.get("entryWindowSeconds", _DEFAULT_ENTRY_WINDOW))
    min_notional = float(inputs.get("minNotionalUsd", _DEFAULT_MIN_NOTIONAL))
    legs = {"basket": (_pct(inputs.get("marginPct"), 4), int(inputs.get("basketLeverage", 2))),
            "washout": (_pct(inputs.get("washoutMarginPct"), 12), int(inputs.get("washoutLeverage", 3)))}

    account_value, withdrawable, positions = _get_account(ctx)
    if account_value <= 0:
        print("[kudu.scan] WAITING — no account value / unreadable clearinghouse", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    open_slots = max_slots - len({scoring.bare(h) for h in held_assets})
    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - scoring._f(v)) < _KEEP_SIGNALED}
    since_close = now % _DAY
    result = {"ts": now, "held": held_assets, "open_slots": open_slots, "since_close_s": int(since_close), "emitted": []}
    if open_slots <= 0:
        _persist(ctx, signaled, result)
        return []
    if since_close > window and not ctx.dry_run:
        _persist(ctx, signaled, result)              # the next decision is at the next daily close
        return []

    rows = _daily(ctx, timing)
    closed = scoring.closed_candles(rows, _DAY, now) if rows else []
    if not closed:
        _persist(ctx, signaled, result)
        return []
    bar_t = int(scoring._t(closed[-1]))
    if now - (bar_t / 1000.0 + _DAY) > window and not ctx.dry_run:
        _persist(ctx, signaled, result)              # a stale close is not bought late
        return []
    rsi = scoring.last_rsi(closed, period)
    result["nasdaq_rsi"] = None if rsi is None else round(rsi, 2)
    out = []
    free = withdrawable                               # the free margin the runtime will size the next signal off
    allowance = withdrawable * _HEADROOM
    for asset, leg in scoring.plan(rsi, basket, washout_asset, held_assets, open_slots, inputs):
        key = f"{scoring.bare(asset)}|LONG|{bar_t}"
        if key in signaled:
            continue                                  # this close already went out
        pct, lev = legs[leg]
        clip = account_value * pct / 100.0            # every position is its leg's percent of the ACCOUNT
        if clip * lev < min_notional or clip > allowance:
            print(f"[kudu.scan] cannot fund {asset}: a ${clip:.2f} clip at {lev}x with ${allowance:.2f} "
                  f"to commit — skipping it, not the names after it", file=sys.stderr)
            continue
        emit_pct = round(min(100.0, 100.0 * clip / free), 4)   # of the free margin left when it opens
        free -= clip
        allowance -= clip
        signaled[key] = now
        result["emitted"].append({"coin": asset, "leg": leg})
        out.append({"asset": asset, "direction": "LONG", "marginPct": emit_pct, "leverage": lev, "data": {
            "leg": leg, "direction": "LONG", "nasdaqRsi": round(rsi, 2), "signalBarTime": bar_t,
            "leverage": lev, "reasons": [f"Nasdaq 100 RSI({period}) {rsi:.1f} at the close: {leg} entry"]}})
    if out:
        print(f"[kudu.scan] EMIT {[(s['asset'], s['data']['leg']) for s in out]} (Nasdaq RSI {rsi:.1f})", file=sys.stderr)
    else:
        print(f"[kudu.scan] WAITING — Nasdaq RSI {rsi} (held {held_assets})", file=sys.stderr)
    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[kudu.scan] WARNING: state append failed; next tick may re-emit a suppressed "
              f"signal: {exc!r}", file=sys.stderr)
