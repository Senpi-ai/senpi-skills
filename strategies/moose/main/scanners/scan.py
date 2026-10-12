"""MOOSE — supervised scanner: EMA crossover (or Donchian breakout) trend follower on BTC/ETH/SOL.

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - for every whitelisted coin not already held and not signalled this bar, fetches 4h + 1d candles,
    keeps only the CLOSED ones, and asks pure `scoring.build_thesis` whether a fresh trigger fired
    with the daily trend behind it,
  - emits the freshest candidates first, one per coin, up to the open slots — gated on free margin so
    a second signal in the same tick is never sized off money the first one just used,
  - remembers which (coin, side, bar) it has already emitted, so one cross is one signal.

Read-only + single-pass. Emits `marginPct` (PERCENT in (0,100]) and a `leverage` the scoring chose
from the coin's own volatility; the runtime sizes the dollars, owns cooldowns/risk gates, and trails
the DSL exit. No daemon, no push_signal, no create_position. Candle values are strings keyed
o/h/l/c/v with t in epoch ms — scoring._f / closed_candles coerce.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_ALLOWED_ASSETS_DEFAULT = ["BTC", "ETH", "SOL"]
_SIGNAL_INTERVAL = "4h"
_SIGNAL_SECONDS = 14400
_REGIME_INTERVAL = "1d"
_REGIME_SECONDS = 86400
_DEFAULT_RECENT_TTL = 14400     # one 4h bar: never signal the same coin twice inside a bar
_DEFAULT_MIN_NOTIONAL = 12.0    # the venue's bumped minimum notional — below it an open cannot fund


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
        print(f"[moose.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, 0.0, []
    if not isinstance(ch, dict):
        print(f"[moose.scan] clearinghouse: non-dict payload {str(ch)[:120]!r}", file=sys.stderr)
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
        print("[moose.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, 0.0, []
    return account_value, withdrawable, positions


def _candles(ctx, coin):
    """{'4h': [...], '1d': [...]} for `coin`, or None. Read-guarded, type-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": [_SIGNAL_INTERVAL, _REGIME_INTERVAL],
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[moose.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not isinstance(md, dict):
        print(f"[moose.scan] market_get_asset_data({coin}): non-dict payload {str(md)[:120]!r}",
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
    allowed = [str(a) for a in (inputs.get("allowedAssets") or _ALLOWED_ASSETS_DEFAULT) if a]
    min_score = float(inputs.get("minScore", 2))
    margin_pct = float(inputs.get("marginPct", 30))              # PERCENT of withdrawable (0,100]
    if 0 < margin_pct < 1.0:                                      # a pasted fraction (0.30 meant 30)
        margin_pct *= 100.0
    max_slots = int(inputs.get("maxSlots", 3))
    max_bars = int(inputs.get("maxBarsSinceSignal", 2))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    min_notional = float(inputs.get("minNotionalUsd", _DEFAULT_MIN_NOTIONAL))

    account_value, withdrawable, positions = _get_account(ctx)
    if account_value <= 0:
        print("[moose.scan] WAITING — no account value / unreadable clearinghouse", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held = {h.upper() for h in held_assets}
    open_slots = max_slots - len(held)

    # one cross is one signal: keys are COIN|SIDE|signal-bar-open-ms, kept long enough to outlive
    # the freshness window; plus a per-coin TTL so a coin is never re-signalled inside one bar
    keep_for = max(ttl * 4, (max_bars + 2) * _SIGNAL_SECONDS)
    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - scoring._f(v)) < keep_for}
    last_by_coin = {}
    for k, v in signaled.items():
        c = k.split("|", 1)[0]
        last_by_coin[c] = max(last_by_coin.get(c, 0.0), scoring._f(v))

    result = {"ts": now, "held": held_assets, "open_slots": open_slots, "scanned": 0, "emitted": []}
    if open_slots <= 0:
        print(f"[moose.scan] slots full ({len(held)}/{max_slots}) — the DSL manages exits", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []

    candidates = []
    for coin in allowed:
        cu = coin.upper()
        if cu in held:
            continue
        if (now - last_by_coin.get(cu, 0.0)) < ttl:
            continue
        result["scanned"] += 1
        md = _candles(ctx, coin)
        if not md:
            continue
        c4h = scoring.closed_candles(md.get(_SIGNAL_INTERVAL, []), _SIGNAL_SECONDS, now)
        c1d = scoring.closed_candles(md.get(_REGIME_INTERVAL, []), _REGIME_SECONDS, now)
        th = scoring.build_thesis(coin, c4h, c1d, inputs)
        if not th:
            continue
        key = f"{cu}|{th['direction']}|{int(th['signal_bar_t'])}"
        if key in signaled:
            continue                                             # this cross already went out
        if th["score"] < min_score:
            print(f"[moose.scan] {coin} {th['direction']} score {th['score']} < minScore {min_score:g}",
                  file=sys.stderr)
            continue
        candidates.append((key, th))

    # freshest trigger first, then the wider cross/breakout
    candidates.sort(key=lambda kt: (kt[1]["bars_since_signal"], -kt[1]["score"]))

    out = []
    remaining = withdrawable
    for key, th in candidates:
        if len(out) >= open_slots:
            break
        margin = remaining * margin_pct / 100.0
        if margin * th["leverage"] < min_notional:
            print(f"[moose.scan] free margin ${remaining:.2f} cannot fund another slot at "
                  f"{margin_pct:g}% x {th['leverage']}x — holding {th['coin']}", file=sys.stderr)
            break
        remaining -= margin
        signaled[key] = now
        result["emitted"].append({"coin": th["coin"], "direction": th["direction"],
                                  "score": th["score"], "leverage": th["leverage"]})
        print(f"[moose.scan] EMIT {th['coin']} {th['direction']} {th['leverage']}x marginPct={margin_pct:g}% "
              f"score={th['score']} | {th['reasons']}", file=sys.stderr)
        data = {k: v for k, v in {
            "score": th["score"],
            "direction": th["direction"],
            "detector": th["detector"],
            "signalBarTime": th["signal_bar_t"],
            "barsSinceSignal": th["bars_since_signal"],
            "emaFast": th.get("ema_fast"),
            "emaSlow": th.get("ema_slow"),
            "emaSpreadPct": th.get("ema_spread_pct"),
            "channelHigh": th.get("channel_high"),
            "channelLow": th.get("channel_low"),
            "breakoutPct": th.get("breakout_pct"),
            "dailySma": th["daily_sma"],
            "dailyRegime": th["daily_regime"],
            "atrPct": th["atr_pct"],
            "stopPricePct": th["stop_price_pct"],
            "leverage": th["leverage"],
            "reasons": th["reasons"],
            "heldAssets": held_assets,
        }.items() if v is not None}
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,            # PERCENT in (0,100] — the runtime sizes the dollars
            "leverage": th["leverage"],         # chosen from the coin's ATR; see scoring.leverage_for
            "data": data,
        })

    if not out:
        print(f"[moose.scan] WAITING — no fresh trigger with the daily trend behind it "
              f"(scanned {result['scanned']}, held {held_assets})", file=sys.stderr)
    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[moose.scan] WARNING: state append failed; next tick may re-emit a suppressed "
              f"signal: {exc!r}", file=sys.stderr)
