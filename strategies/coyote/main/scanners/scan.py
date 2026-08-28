"""COYOTE — supervised scanner (Runtime 3.0 port of the v2 Coyote regime classifier).

Single-asset BTC positional bet whose DIRECTION is set by a macro-regime
classification. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - reads BTC 4h candles -> 7d move + annualized realized vol,
  - reads each dispersion-universe asset's 4h candles -> cross-asset dispersion
    (INFORMATIONAL ONLY; published, never gating — SKILL RULE 3),
  - classifies TREND_UP / TREND_DOWN / CHOP / UNKNOWN via pure `scoring`,
  - emits ONE BTC signal (LONG in TREND_UP, SHORT in TREND_DOWN), else nothing.

The regime view is appended to ctx.state EVERY tick (even on CHOP / UNKNOWN /
no-trade), reproducing the v2 producer's "always publish the regime" behaviour
for operator visibility.

Read-only + single-pass — emits `marginPct` (PERCENT in (0,100]) + `leverage`
at the TOP level; the runtime sizes the dollars, owns cooldowns/risk gates, and
trails the DSL exit. No daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (coyote-producer.py v1.0.1):
  - v2 stored margin as a FRACTION (config marginPct=0.25) and computed
    marginUsd = account_value * 0.25. Runtime 3.0 sizes from a PERCENT in
    (0,100], so this port emits `marginPct` = 25 (0.25 * 100). The defensive
    "<=1.0 means a pasted fraction, *100" guard is applied so an operator who
    leaves the v2-style 0.25 in inputs still gets 25%.
  - v2 leverage: min(int(config.leverage=3), MAX_LEVERAGE=5) -> clamped to 5.
    Preserved verbatim (DEFAULT_LEVERAGE=3, MAX_LEVERAGE=5).
  - v2 published the regime in EVERY tick output (incl. no-trade). Preserved:
    the regime view is written to ctx.state every tick + a one-line stderr.
  - v2 emitted exactly one signal (BTC). Preserved: scan() emits <= 1/tick.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240) -> ctx.state dedup
    map (same TTL + 4x-TTL prune semantics).
  - v2 score in the data block was a constant 5 / wire score 0.7. Score is not a
    gate in Coyote (regime IS the gate); preserved as a constant 5 in data.
  - dispersion is computed + published but NEVER enters the gate (SKILL RULE 3,
    verbatim). It only refines an operator's read of "mixed vs synchronized".
"""

import sys
import time

import scoring

# v2 defaults (coyote-producer.py / coyote-config.json)
_DEFAULT_BTC = "BTC"
_DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]   # dispersion only (informational)
_DEFAULT_TREND_LOOKBACK = 42            # 7d on 4h bars
_DEFAULT_VOL_LOOKBACK = 42              # 7d realized vol
_DEFAULT_DISPERSION_LOOKBACK = 24       # 4d dispersion
_DEFAULT_TREND_UP_THRESHOLD_PCT = 5.0
_DEFAULT_TREND_DOWN_THRESHOLD_PCT = 5.0
_DEFAULT_MAX_VOL_FOR_TREND_PCT = 80.0
_DEFAULT_MIN_VOL_FOR_CRASH_PCT = 60.0
_DEFAULT_MARGIN_PCT = 25                # PERCENT (v2 fraction 0.25 * 100)
_DEFAULT_LEVERAGE = 3
_MAX_LEVERAGE = 5                       # v2 MAX_LEVERAGE (hardcoded)
_DEFAULT_RECENT_TTL = 240              # v2 RECENT_SIGNAL_TTL_SEC


def _to_percent(v, default):
    """Defensive fraction->percent guard (dire/koala pattern). A v2 config that
    still carries marginPct as a FRACTION (0.25) would otherwise size at 0.25%.
    Any value <= 1.0 is treated as a pasted fraction and scaled *100."""
    try:
        p = float(v)
    except (TypeError, ValueError):
        return float(default)
    if p <= 0:
        return float(default)
    if p <= 1.0:
        p *= 100.0
    return p


def _get_account(ctx):
    """(account_value, [held_coin_upper]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    if not ctx.wallet:
        return 0.0, []
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[coyote.scan] clearinghouse read failed (degrade): {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    held, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0), default=0.0))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0), default=0.0)
            if szi == 0:
                continue
            coin = str(pos.get("coin", "")).upper()
            if coin:
                held.append(coin)

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing / held-asset dedup off that re-enters held
    # names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use,
                   scoring._f(_ms.get("totalMarginUsed", 0), default=0.0),
                   abs(scoring._f(_ms.get("totalNtlPos", 0), default=0.0)))
    if _use > 1.0 and not held:
        print("[coyote.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, held


def _fetch_closes(ctx, asset):
    """4h close-price series for `asset` or [] on any error. READ-GUARDED.
    Ported from v2 producer `fetch_candles` (market_get_asset_data, 4h only)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["4h"],
            "include_funding": False,
            "include_order_book": False,
        })
    except Exception as exc:  # noqa: BLE001 — degrade to empty series, never crash the tick
        print(f"[coyote.scan] market_get_asset_data({asset}) read failed (degrade): {exc!r}",
              file=sys.stderr)
        return []
    if not md:
        return []
    if isinstance(md, dict) and md.get("success") is False:
        return []
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return []
    candles = (d.get("candles", {}) or {}).get("4h", []) or []
    return scoring.closes_from_candles(candles)


# ── ctx.state: recent-signal dedup (port of v2 recent-signals.json) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    btc_asset = inputs.get("btcAsset", _DEFAULT_BTC)
    universe = inputs.get("dispersionUniverse", _DEFAULT_UNIVERSE)
    trend_lb = int(inputs.get("trendLookbackBars", _DEFAULT_TREND_LOOKBACK))
    vol_lb = int(inputs.get("volLookbackBars", _DEFAULT_VOL_LOOKBACK))
    disp_lb = int(inputs.get("dispersionLookbackBars", _DEFAULT_DISPERSION_LOOKBACK))
    trend_up_th = float(inputs.get("trendUpThresholdPct", _DEFAULT_TREND_UP_THRESHOLD_PCT))
    trend_dn_th = float(inputs.get("trendDownThresholdPct", _DEFAULT_TREND_DOWN_THRESHOLD_PCT))
    max_vol_trend = float(inputs.get("maxVolForTrendPct", _DEFAULT_MAX_VOL_FOR_TREND_PCT))
    min_vol_crash = float(inputs.get("minVolForCrashPct", _DEFAULT_MIN_VOL_FOR_CRASH_PCT))
    margin_pct = _to_percent(inputs.get("marginPct", _DEFAULT_MARGIN_PCT), _DEFAULT_MARGIN_PCT)
    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, held = _get_account(ctx)
    held_set = {h.upper() for h in held}
    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # v2 produced no signal (and skipped scoring) when account value was 0.
    if account_value <= 0:
        result = {"ts": now, "emitted": False, "regime": None,
                  "note": "no account value", "held": held}
        print("[coyote.scan] WAITING — no account value", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []

    # ── BTC trend + realized vol ──
    btc_closes = _fetch_closes(ctx, btc_asset)
    btc_move = scoring.pct_move(btc_closes, trend_lb)
    btc_vol = scoring.realized_vol_pct(btc_closes, vol_lb)

    # ── cross-asset dispersion (INFORMATIONAL ONLY — never gates; SKILL RULE 3) ──
    returns_by_asset = {}
    for a in universe:
        closes = btc_closes if a == btc_asset else _fetch_closes(ctx, a)
        returns_by_asset[a] = scoring.pct_move(closes, disp_lb)
    disp = scoring.dispersion_pct(returns_by_asset)

    regime = scoring.classify_regime(btc_move, btc_vol, trend_up_th, trend_dn_th,
                                     max_vol_trend, min_vol_crash)
    direction = scoring.regime_to_direction(regime)

    out = []
    btc_u = btc_asset.upper()

    if direction is None:
        # CHOP / UNKNOWN — publish the regime view, no trade (verbatim v2 behaviour).
        result = {"ts": now, "emitted": False, "regime": regime,
                  "btc7dMovePct": round(btc_move, 2) if btc_move is not None else None,
                  "realizedVolPct": round(btc_vol, 1) if btc_vol is not None else None,
                  "dispersionPct": round(disp, 2) if disp is not None else None,
                  "held": held}
        print(f"[coyote.scan] WAITING — REGIME={regime} (no positional expression); "
              f"btc7d={result['btc7dMovePct']}% vol={result['realizedVolPct']}% "
              f"disp={result['dispersionPct']} held={held}", file=sys.stderr)
    elif btc_u in held_set or _was_recently_signaled(signaled, btc_asset, ttl, now):
        result = {"ts": now, "emitted": False, "regime": regime, "direction": direction,
                  "btc7dMovePct": round(btc_move, 2), "realizedVolPct": round(btc_vol, 1),
                  "dispersionPct": round(disp, 2) if disp is not None else None,
                  "note": "BTC already held or recently signaled", "held": held}
        print(f"[coyote.scan] WAITING — REGIME={regime} {direction} but BTC held/recently-signaled; "
              f"held={held}", file=sys.stderr)
    else:
        signaled[btc_u] = now
        result = {"ts": now, "emitted": True, "regime": regime, "direction": direction,
                  "btc7dMovePct": round(btc_move, 2), "realizedVolPct": round(btc_vol, 1),
                  "dispersionPct": round(disp, 2) if disp is not None else None,
                  "leverage": leverage, "marginPct": round(margin_pct, 4), "held": held}
        print(f"[coyote.scan] EMIT {btc_asset} {direction} REGIME={regime} "
              f"btc7d={btc_move:+.1f}% vol={btc_vol:.0f}% disp="
              f"{round(disp, 2) if disp is not None else None} {leverage}x "
              f"marginPct={margin_pct:.2f}%", file=sys.stderr)
        out = [{
            "asset": btc_asset,
            "direction": direction,
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 1..5; runtime applies it
            "data": {
                "score": 5,                   # constant (v2 data score=5; regime IS the gate)
                "leverage": float(leverage),
                "marginPct": round(margin_pct, 4),
                "direction": direction,
                "reasons": [f"BTC regime {'trending up' if regime == 'TREND_UP' else 'trending down'}", f"BTC {btc_move:+.1f}% over 7 days", f"volatility {btc_vol:.0f}% annualized"],
                "regime": regime,
                "btc7dMovePct": float(btc_move),
                "realizedVolPct": float(btc_vol),
                "dispersionPct": float(disp) if disp is not None else 0.0,
                "heldAssets": held,
            },
        }]

    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    """Append the dedup map + this tick's regime view EVERY tick; bounded by
    state_history_max_count. Read back via ctx.state.recent(n)."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[coyote.scan] WARNING: state append failed; next tick may re-emit "
              f"a suppressed signal: {exc!r}", file=sys.stderr)
