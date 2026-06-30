"""BARNACLE — supervised scanner (net-new Runtime 3.0 index-inclusion front-runner).

Config-driven EVENT universe — there is NO MCP feed for index-rebalance events, so
the operator supplies them in `inputs.events` as a list of:
    {asset, effectiveDate (ISO date / ISO datetime / epoch), side: "add"|"delete", index}
The scan trades ONLY the names in `inputs.events`. The default events list is an
EXAMPLE operators replace with the live rebalance schedule.

Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - for each well-formed, not-yet-past, not-held, not-recently-signaled event whose
    asset resolves on HL xyz: reads market_get_asset_data (dex="xyz", READ-GUARDED),
    computes hours-to-effective-date,
  - ADD -> LONG / DELETE -> SHORT (direction comes from the EVENT side, not price),
  - emits ONLY while `now < effectiveDate - exitBufferHours` (default 24h) AND the
    passive-anticipation signature confirms (4h trend in-direction + rising volume),
  - sizes marginPct scaled UP by proximity to the date (closer = larger, base ->
    max), leverage clamped to [1,5] and to the instrument venue max,
  - once `now >= effectiveDate - exitBufferHours` it emits NOTHING for that event —
    "sell into the inclusion": the DSL/exit owns the close before the rebalance.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) + `leverage`; the
runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit. No
daemon, no push_signal, no create_position. Every ctx.senpi_mcp.call_tool is
read-guarded (degrade, never crash the whole tick).
"""

import sys
import time

import scoring


# net-new defaults (an EXAMPLE events list — operators replace with the live schedule)
_DEFAULT_EVENTS = [
    {"asset": "xyz:SPCX", "effectiveDate": "2026-07-07",
     "side": "add", "index": "russell+ndx100"},  # validated live vs HL xyz meta 2026-06-30
]
_DEFAULT_EXIT_BUFFER_HOURS = 24      # stop emitting (sell into the inclusion) this long before the date
_DEFAULT_WINDOW_HOURS = 168          # 7d tradeable anticipation window before the date
_DEFAULT_MARGIN_BASE = 12            # PERCENT at window start
_DEFAULT_MARGIN_MAX = 20             # PERCENT near the exit buffer
_DEFAULT_LEVERAGE = 4                # clamped to [1,5] and venue max
_DEFAULT_MIN_VOL_TREND = 10          # 4h volume-trend floor for confirmation
_DEFAULT_RECENT_TTL = 1800           # 30m signal-dedup (don't re-fire while in flight)
_LEV_MIN = 1
_LEV_MAX = 5


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Includes the read-sanity guard (margin in use
    + empty positions -> skip tick) to avoid re-entering held names off a corrupt
    clearinghouse read."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[barnacle.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring._f(pos.get("marginUsed", 0))})

    # read-sanity guard: a corrupt clearinghouse read can report margin/notional IN
    # USE while returning an EMPTY positions list; sizing or running the held-asset
    # dedup off that re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[barnacle.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _asset_data(ctx, coin, dex):
    """{candles_1h, candles_4h, venue_max} for `coin` or None. READ-GUARDED.
    A failed read (transient/permission/not-listed) returns None -> the event is
    skipped this tick rather than crashing the whole scan."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[barnacle.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    # venue max leverage if the read surfaces it (asset_context / top-level); else None
    venue_max = None
    actx = d.get("asset_context", d) if isinstance(d, dict) else {}
    if isinstance(actx, dict):
        venue_max = actx.get("max_leverage", d.get("max_leverage"))
    return {
        "candles_1h": candles.get("1h", []) or [],
        "candles_4h": candles.get("4h", []) or [],
        "venue_max": venue_max,
    }


# ── ctx.state: recent-signal dedup ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (fleet-standard bound)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    events = inputs.get("events", _DEFAULT_EVENTS)
    exit_buffer_h = float(inputs.get("exitBufferHours", _DEFAULT_EXIT_BUFFER_HOURS))
    window_h = float(inputs.get("windowHours", _DEFAULT_WINDOW_HOURS))
    margin_base = float(inputs.get("marginPctBase", _DEFAULT_MARGIN_BASE))
    margin_max = float(inputs.get("marginPctMax", _DEFAULT_MARGIN_MAX))
    lev_cfg = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    min_vol_trend = float(inputs.get("minVolTrendPct", _DEFAULT_MIN_VOL_TREND))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # marginPct PERCENT in (0,100]. Defensive fraction guard (fleet pattern): a
    # value <= 1.0 is a pasted FRACTION (e.g. 0.12) -> *100.
    if margin_base <= 1.0:
        margin_base *= 100
    if margin_max <= 1.0:
        margin_max *= 100

    if not isinstance(events, list) or not events:
        print("[barnacle.scan] WAITING — no events in inputs.events (operator supplies "
              "the rebalance schedule)", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"signaled": _load_signaled(ctx),
                                  "result": {"ts": now, "emitted": False, "note": "no events configured"}})
            except Exception as exc:  # noqa: BLE001
                print(f"[barnacle.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    candidates = []
    scanned = 0
    skipped = 0
    for raw_ev in events:
        ev = scoring.normalize_event(raw_ev)
        if ev is None:
            skipped += 1
            print(f"[barnacle.scan] SKIP malformed event {raw_ev!r}", file=sys.stderr)
            continue

        coin = ev["asset"]
        cu = coin.upper()
        eff = ev["effectiveEpoch"]
        side = ev["side"]
        direction = scoring.direction_for_side(side)
        hours_to_eff = (eff - now) / 3600.0

        # past-date -> the rebalance has happened; nothing to front-run
        if hours_to_eff <= 0:
            skipped += 1
            print(f"[barnacle.scan] SKIP past-date event {coin} (effective {eff:.0f}, "
                  f"{hours_to_eff:.1f}h)", file=sys.stderr)
            continue

        # SELL INTO THE INCLUSION: once inside the exit buffer, emit nothing for this
        # event — the DSL/exit owns the close before the rebalance completes.
        if hours_to_eff <= exit_buffer_h:
            print(f"[barnacle.scan] HOLD/EXIT-WINDOW {coin} {direction} — within exit buffer "
                  f"({hours_to_eff:.1f}h <= {exit_buffer_h:.0f}h); no new entry", file=sys.stderr)
            continue

        # outside the tradeable anticipation window -> too early
        if hours_to_eff > window_h:
            print(f"[barnacle.scan] EARLY {coin} {direction} — {hours_to_eff:.1f}h to date "
                  f"(> window {window_h:.0f}h)", file=sys.stderr)
            continue

        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue

        scanned += 1
        md = _asset_data(ctx, coin, _dex_for(coin, inputs))
        if not md:
            continue

        ok, reasons, detail = scoring.anticipation_confirms(
            direction, md["candles_4h"], md["candles_1h"], min_vol_trend)
        if not ok:
            print(f"[barnacle.scan] NO-CONFIRM {coin} {direction} — {reasons}", file=sys.stderr)
            continue

        margin_pct = scoring.proximity_margin_pct(
            hours_to_eff, exit_buffer_h, margin_base, margin_max, window_h)
        leverage = scoring.clamp_leverage(lev_cfg, _LEV_MIN, _LEV_MAX, md.get("venue_max"))

        candidates.append({
            "coin": coin, "direction": direction, "side": side,
            "index": ev["index"], "effectiveEpoch": eff,
            "hoursToEffective": round(hours_to_eff, 2),
            "marginPct": round(margin_pct, 4), "leverage": leverage,
            "reasons": reasons, "detail": detail,
        })

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "skipped": skipped, "emitted": False,
                  "held": held_assets, "note": "WAITING (no confirmed in-window event)"}
        print(f"[barnacle.scan] WAITING — no confirmed in-window index event; "
              f"scanned={scanned} skipped={skipped} held={held_assets}", file=sys.stderr)
    else:
        # emit the MOST IMMINENT confirmed event (smallest hours-to-date = strongest
        # forced-flow proximity). One signal/tick keeps the book focused.
        candidates.sort(key=lambda c: c["hoursToEffective"])
        best = candidates[0]
        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "skipped": skipped, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"], "side": best["side"],
                  "index": best["index"], "hoursToEffective": best["hoursToEffective"],
                  "marginPct": best["marginPct"], "leverage": best["leverage"],
                  "held": held_assets, "reasons": best["reasons"]}
        print(f"[barnacle.scan] EMIT {best['coin']} {best['direction']} ({best['side']} "
              f"{best['index']}) {best['leverage']}x marginPct={best['marginPct']:.2f}% "
              f"{best['hoursToEffective']:.1f}h-to-date | {best['reasons']}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": best["marginPct"],   # PERCENT in (0,100] — runtime sizes (marginPct/100)*withdrawable
            "leverage": best["leverage"],     # clamped [1,5] + venue max; runtime applies it
            "data": {
                "side": best["side"],
                "index": best["index"],
                "direction": best["direction"],
                "leverage": best["leverage"],
                "hoursToEffective": best["hoursToEffective"],
                "effectiveEpoch": best["effectiveEpoch"],
                "trend4h": best["detail"].get("trend4h"),
                "trend4hStrength": best["detail"].get("trend4hStrength"),
                "trend1h": best["detail"].get("trend1h"),
                "volTrend4hPct": best["detail"].get("volTrend4hPct"),
                "reasons": best["reasons"],
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result EVERY tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[barnacle.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
