"""DIP LADDER — supervised scanner: one rung of a BTC/ETH dip ladder, bought at the daily close.

All six rung instances (strategies/squirrel/rung1-3 and strategies/chipmunk/rung1-3) run this file and
its scoring.py byte for byte (each package's tests/test_engine_parity.py pins it). Every difference is
an INPUT in the rung's runtime.yaml: `depthPct` (how far under the 60-day high the rung buys) and
`regimeWeeks` (0 = no trend gate, squirrel; 40 = buy only above the 40-week average, chipmunk).

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - returns early, after that read, when every slot is full or outside the entry window that opens at
    each UTC-midnight daily close — the ladder buys at the close or not at all,
  - inside the window, reads each coin the wallet does not hold (its daily candles, plus its weekly
    candles when the trend gate is on, on the coin's own dex), keeps only CLOSED bars, checks that
    every series it reads is current at this close, and asks pure `scoring.measure` whether the close
    sits `depthPct` or more under the 60-day high (and above the 40-week average, gate on),
  - emits ONE qualifier per tick: the first, in recipe order, it can fund from this tick's balance
    read, skipping one it cannot fund without stopping. The next goes out on the next tick, five
    minutes later, sized off a fresh read. It remembers each (coin, bar) it emitted, so one daily
    close is one entry per coin at most; an open the runtime skips is not retried at that close.

Freshness. A coin whose candles cannot be read, whose latest closed daily bar is not this close, or
(gate on) whose weekly series is missing its newest close, is not decided at this tick: nothing is
emitted or marked for it, and the next tick reads it again. While the wallet has a slot for every coin
it does not hold, that coin waiting takes nothing from another. When it has fewer open slots than such
coins, a coin that cannot be read could lose its slot to the coins after it in the recipe order, so
those are not bought at this tick either (the walk stops at it). A coin with too little history to
measure is not a candidate and blocks nothing.

Sizing. Every position is `marginPct` percent of the wallet's ACCOUNT value, an equal clip per coin.
The runtime sizes a signal as a percent of the free margin when it opens it, and an open it skips
(the per-asset cooldown, a risk gate) commits nothing. So the scanner sends one signal per tick, as
the percent of the free margin it just read that equals the clip; a skipped open never enlarges the
next. Read-only + single-pass; the runtime sizes the dollars, owns cooldowns/risk gates, and runs the
DSL exit. No daemon, no push_signal, no create_position. Candle values are strings keyed o/h/l/c/v
with t in epoch ms — scoring._f / closed_candles coerce.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_ALLOWED_ASSETS_DEFAULT = ["BTC", "ETH"]
_DAY = scoring.DAY
_DEFAULT_ENTRY_WINDOW = 3600    # buy within the first hour after the daily close, or wait for the next
_DEFAULT_MIN_NOTIONAL = 12.0    # the venue's bumped minimum notional — below it an open cannot fund
_KEEP_SIGNALED = 4 * _DAY       # how long a (coin, bar) key is remembered
_HEADROOM = 0.9                 # commit at most 90% of the free margin in one open
_LOG = "[dip-ladder.scan]"


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
        print(f"{_LOG} clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, 0.0, []
    if not isinstance(ch, dict):
        print(f"{_LOG} clearinghouse: non-dict payload {str(ch)[:120]!r}", file=sys.stderr)
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
        print(f"{_LOG} read-sanity guard: margin in use but empty positions — skipping tick", file=sys.stderr)
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
        print(f"{_LOG} market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not isinstance(md, dict):
        print(f"{_LOG} market_get_asset_data({coin}): non-dict payload {str(md)[:120]!r}", file=sys.stderr)
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


def _pct(v, d):
    p = scoring._f(v if v is not None else d, d)
    return p * 100.0 if 0 < p < 1.0 else p                        # a pasted fraction (0.25 meant 25)


def scan(inputs, ctx):
    now = time.time()
    allowed = [str(a) for a in (inputs.get("allowedAssets") or _ALLOWED_ASSETS_DEFAULT) if a]
    clip_pct = _pct(inputs.get("marginPct"), 25)                  # PERCENT of ACCOUNT value per position
    lev = scoring.leverage(inputs)
    max_slots = int(inputs.get("maxSlots", len(allowed)))
    window = float(inputs.get("entryWindowSeconds", _DEFAULT_ENTRY_WINDOW))
    min_notional = float(inputs.get("minNotionalUsd", _DEFAULT_MIN_NOTIONAL))
    gated = scoring.regime_weeks(inputs) > 0
    intervals = ["1d", "1w"] if gated else ["1d"]

    account_value, withdrawable, positions = _get_account(ctx)
    if account_value <= 0:
        print(f"{_LOG} WAITING — no account value / unreadable clearinghouse", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held = {scoring.bare(h) for h in held_assets}
    open_slots = max_slots - len(held)
    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - scoring._f(v)) < _KEEP_SIGNALED}
    since_close = now % _DAY
    result = {"ts": now, "held": held_assets, "open_slots": open_slots, "since_close_s": int(since_close),
              "measured": {}, "waiting_on": [], "unfunded": [], "emitted": []}

    if open_slots <= 0:
        print(f"{_LOG} slots full ({len(held)}/{max_slots}) — the DSL manages the exits", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []
    if since_close > window and not ctx.dry_run:
        _persist(ctx, signaled, result)              # the next decision is at the next daily close
        return []

    unheld = [c for c in allowed if scoring.bare(c) not in held]
    contested = len(unheld) > open_slots             # more coins to decide than slots to put them in
    candidates, blocked = [], None
    for coin in unheld:
        md = _candles(ctx, coin, intervals)
        daily = scoring.closed_candles(md.get("1d"), _DAY, now) if md else []
        weekly = scoring.closed_candles(md.get("1w"), scoring.WEEK, now) if (md and gated) else []
        bar_close = scoring._t(daily[-1]) / 1000.0 + _DAY if daily else None
        stale = bar_close is None or (now - bar_close > window and not ctx.dry_run)
        if not stale and scoring.prior_high(daily, scoring._f(inputs.get("highLookbackDays", 60), 60.0)) is None:
            print(f"{_LOG} {coin}: {len(daily)} closed daily candles, too little history to measure — "
                  f"not a candidate", file=sys.stderr)
            continue
        if not stale and gated and not scoring.weekly_current(weekly, bar_close):
            stale = True                             # the average would be last week's
        if stale:
            # Unreadable, or not current at this close: nothing is decided or marked for it, and the
            # next tick reads it again. With fewer open slots than coins to decide, the coins after it
            # could take the slot it may have qualified for, so the walk stops here.
            result["waiting_on"].append(coin)
            print(f"{_LOG} WAITING on {coin}: no current close in every series it reads", file=sys.stderr)
            if contested:
                blocked = coin
                break
            continue
        m = scoring.measure(coin, daily, weekly, inputs)
        if m is None:
            print(f"{_LOG} {coin}: {len(weekly)} closed weekly candles, no complete run of "
                  f"{scoring.regime_weeks(inputs)} consecutive weeks for the trend gate (too new, or a week "
                  f"missing from the feed) — not a candidate", file=sys.stderr)
            continue
        result["measured"][coin] = {k: m[k] for k in ("close_px", "high_px", "trigger_px", "regime_average", "qualifies")}
        if not m["qualifies"]:
            continue
        key = f"{scoring.bare(coin)}|LONG|{m['signal_bar_t']}"
        if key in signaled:
            continue                                  # this close already went out
        candidates.append((key, m))

    if blocked:
        result["blocked_by"] = blocked
        print(f"{_LOG} WAITING on {blocked}: no coin after it in the order is bought at this tick",
              file=sys.stderr)
    out = []
    clip = account_value * clip_pct / 100.0           # every position is clip_pct of the ACCOUNT
    allowance = withdrawable * _HEADROOM
    for key, m in candidates:
        th = scoring.thesis(m, inputs)
        if clip * th["leverage"] < min_notional or clip > allowance:
            result["unfunded"].append(th["coin"])
            print(f"{_LOG} cannot fund {th['coin']}: a ${clip:.2f} clip at {th['leverage']}x with "
                  f"${allowance:.2f} to commit — skipping it, not the coins after it", file=sys.stderr)
            continue
        emit_pct = round(min(100.0, 100.0 * clip / withdrawable), 4)   # of the free margin this tick read
        signaled[key] = now
        result["emitted"].append({"coin": th["coin"], "clip": round(clip, 2)})
        print(f"{_LOG} EMIT {th['coin']} LONG {th['leverage']}x clip ${clip:.2f} (marginPct={emit_pct:g}%) "
              f"| {th['reasons']}", file=sys.stderr)
        data = {k: v for k, v in {
            "direction": "LONG",
            "depthPct": th["depth_pct"],
            "closePx": th["close_px"],
            "highPx": th["high_px"],
            "triggerPx": round(th["trigger_px"], 8),
            "belowHighPct": th["below_high_pct"],
            "regimeAverage": th["regime_average"],
            "signalBarTime": th["signal_bar_t"],
            "leverage": th["leverage"],
            "stopPricePct": th["stop_price_pct"],
            "clipUsd": round(clip, 2),
            "reasons": th["reasons"],
            "heldAssets": held_assets,
        }.items() if v is not None}
        out.append({
            "asset": th["coin"],
            "direction": "LONG",
            "marginPct": emit_pct,              # PERCENT of free margin in (0,100] that equals the clip
            "leverage": th["leverage"],
            "data": data,
        })
        break                                          # one per tick: the next is sized off the next tick's read
    if not out:
        print(f"{_LOG} WAITING — no coin to buy at this close (measured {sorted(result['measured'])}, "
              f"held {held_assets})", file=sys.stderr)
    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"{_LOG} WARNING: state append failed; next tick may re-emit a suppressed signal: {exc!r}",
              file=sys.stderr)
