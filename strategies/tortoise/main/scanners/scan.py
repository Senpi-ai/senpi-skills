"""TORTOISE — supervised scanner: a scheduled buyer that holds ONE position per asset.

Small basket (BTC/ETH/SOL by default), NO price prediction and NO market-data
read — the "scanner" is purely a clock. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - compares the held set with the last GOOD read's: a name held then and flat
    now has EXITED (the DSL closed it) — the exit time is recorded in ctx.state,
  - filters out held names and recently-signaled names (it never adds to an
    open position — the runtime has no supported way to),
  - picks the SINGLE most-overdue eligible asset whose clock has run past the
    interval, the clock being keyed to the LATEST of that name's last buy and
    last exit (a never-bought name is always due), via the verbatim
    scoring.pick_next_dca_asset over scoring.schedule_anchor,
  - emits ONE LONG signal sized at a FIXED marginPct (no tiers),
  - records the buy timestamp + dedup timestamp back into ctx.state.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus a `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position. The DSL owns ALL exits.

2.0.0 — WHAT CHANGED AND WHY:
  - 1.x keyed the clock to the BUY time. Once a position had been held longer
    than the interval, the tick after a stop-out saw the name flat and overdue
    and bought it straight back — about 30 minutes after the exit, into the same
    move. The clock is now keyed to the last EXIT: a name that just exited waits
    a full interval before it is bought again.
  - This was never dollar-cost averaging: one position per asset, re-bought on
    the schedule after that position has exited. The card now says so.
  - marginPct pasted as a fraction (0.25 for 25%) is converted to a percent.
  - Leverage is capped at 2x (the catalog cap). The scanner reads no instrument
    list, so there is no per-name venue clamp: 2x is under every venue cap.

STATE (ctx.state, one record per tick, bounded by state_history_max_count):
  dca_history {ASSET: last BUY epoch}, exits {ASSET: last observed EXIT epoch},
  held [names held at the last GOOD read], signaled {ASSET: dedup epoch}, result.
  A degraded read (error, empty, zero equity, or the read-sanity guard) carries
  `held` forward unchanged and records no exit, so an exit that happens during
  an outage is still detected by the first good read after it.

FIDELITY NOTES vs the v2 producer (tortoise-producer.py v1.0.1): the cadence
math (seconds_since / is_dca_due / pick_next_dca_asset / next_due_seconds), the
held-name filter, the race-window dedup (TTL + 4x-TTL prune), the read-sanity
guard and the data block (producer-fixed score 5, direction LONG, reasons,
intervalSec, elapsedSec, heldAssets) are ported verbatim; only the clock's
anchor (buy -> latest of buy/exit) and the sizing numbers changed.
"""

import sys
import time

import scoring

_DEFAULT_ASSETS = ["BTC", "ETH", "SOL"]      # v2 DEFAULT_ASSETS
_DEFAULT_INTERVAL_HOURS = 24.0               # v2 DEFAULT_INTERVAL_HOURS
_DEFAULT_MARGIN_PCT = 25.0                    # PERCENT; 3 slots x 25% = 75% of the wallet
_DEFAULT_LEVERAGE = 2                         # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 2                             # the catalog cap (leverage_max 2) — under every venue cap
_DEFAULT_RECENT_TTL = 240                     # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup
_DIRECTION = "LONG"                           # v2 DEFAULT_DIRECTION — a buyer, never a seller
_PRODUCER_FIXED_SCORE = 5                     # v2 data.score (producer-fixed; Tortoise has no scoring)


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick).
    Every degraded path returns (0.0, []) and the caller treats account_value <= 0
    as "no read this tick": no exit detection, no emit."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[tortoise.scan] clearinghouse read failed (degrade): {exc!r}", file=sys.stderr)
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
            positions.append({"coin": pos.get("coin", "")})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-name filter off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[tortoise.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── ctx.state: buy history + exit history + last-good held set + dedup map ──
# v2 persisted the buy history and the dedup map to JSON files. Here everything
# rides in the latest ctx.state record; we read the last record, mutate, and
# append a fresh one every tick (bounded by state_history_max_count).

def _ts_map(raw):
    return {str(k).upper(): scoring._f(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _load_state(ctx):
    """Returns (buys{ASSET:ts}, exits{ASSET:ts}, held_at_last_good_read[list],
    signaled{ASSET:ts}) from the latest record. `held` falls back to the 1.x
    record's `result.held`, so an upgrade mid-hold still sees that hold end."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}, {}, [], {}
    last = ctx.state.last() or {}
    held = last.get("held")
    if not isinstance(held, list):
        held = (last.get("result") or {}).get("held") if isinstance(last.get("result"), dict) else None
    held = [str(h) for h in held if h] if isinstance(held, list) else []
    return _ts_map(last.get("dca_history", {})), _ts_map(last.get("exits", {})), held, _ts_map(last.get("signaled", {}))


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    """Verbatim from v2 was_recently_signaled (TTL window check)."""
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def _anchor_kind(asset, history, exits):
    """Which event the chosen name's clock ran from: never_bought / last_exit / last_buy."""
    buy, exit_ = history.get(asset), exits.get(asset)
    if buy is None and exit_ is None:
        return "never_bought"
    if exit_ is not None and (buy is None or exit_ >= buy):
        return "last_exit"
    return "last_buy"


def scan(inputs, ctx):
    now = time.time()
    assets = inputs.get("assets", _DEFAULT_ASSETS)
    interval_hours = float(inputs.get("intervalHours", _DEFAULT_INTERVAL_HOURS))
    interval_sec = interval_hours * 3600.0
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))   # PERCENT in (0,100]
    if 0 < margin_pct < 1.0:
        # A pasted fraction (0.25 meant 25%) would size the slot ~100x too small; 1 is a legal 1%.
        print(f"[tortoise.scan] marginPct={margin_pct} looks like a fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0
    leverage = max(1, min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE))   # package cap 2x
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    history, exits, prev_held, signaled = _load_state(ctx)
    signaled = _prune_signaled(signaled, ttl, now)

    account_value, positions = _get_account(ctx)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    out = []
    if account_value <= 0:
        # No read this tick (error, empty, zero equity, or the read-sanity guard): v2 went
        # silent here. Carry the last good held set forward and record no exit, so a close
        # that happens during an outage is detected by the first good read after it.
        result = {"ts": now, "emitted": False, "note": "no account value (read degraded or zero equity)",
                  "held": list(prev_held)}
        print("[tortoise.scan] WAITING — no account value (read degraded or zero equity)", file=sys.stderr)
        _persist(ctx, history, exits, prev_held, signaled, result)
        return out

    # ── 2.0.0: a name held at the last good read and flat now has exited. Its clock restarts here.
    exits, exited = scoring.detect_exits(prev_held, held_set, exits, now)
    if exited:
        print(f"[tortoise.scan] EXIT observed for {exited} — re-buy no sooner than "
              f"{interval_hours}h from now", file=sys.stderr)

    # Filter: a held name is not a candidate (one position per asset, never added to);
    # a recently signaled name is in the race-window cooldown. Both verbatim v2.
    eligible = [a for a in assets
                if str(a).upper() not in held_set
                and not _was_recently_signaled(signaled, str(a), ttl, now)]

    anchors = scoring.schedule_anchor(history, exits)
    chosen = scoring.pick_next_dca_asset(eligible, anchors, interval_sec, now)

    if chosen is None:
        next_due = scoring.next_due_seconds(eligible, anchors, interval_sec, now)
        next_due_min = round(next_due / 60.0, 1) if next_due is not None else None
        result = {"ts": now, "emitted": False, "assets": list(assets), "held": held_assets,
                  "next_due_in_min": next_due_min,
                  "note": "WAITING — no asset past its interval since its last buy/exit (or all held/in cooldown)"}
        print(f"[tortoise.scan] WAITING — no asset due (next in {next_due_min}min); "
              f"assets={list(assets)} held={held_assets}", file=sys.stderr)
        _persist(ctx, history, exits, held_assets, signaled, result)
        return out

    # Defence-in-depth (redundant — chosen is already excluded from held_set).
    if chosen in held_set:
        result = {"ts": now, "emitted": False, "note": f"chosen {chosen} held — skip",
                  "held": held_assets}
        print(f"[tortoise.scan] SKIP — chosen {chosen} already held", file=sys.stderr)
        _persist(ctx, history, exits, held_assets, signaled, result)
        return out

    kind = _anchor_kind(chosen, history, exits)
    elapsed = scoring.seconds_since(anchors.get(chosen), now)
    elapsed_sec = float(elapsed or 0.0)

    # record the buy + dedup timestamps (mirrors v2 record_dca + record_signal)
    history[chosen] = now
    signaled[chosen] = now

    reasons = ["scheduled_buy", f"clock_{kind}", f"elapsed_{int(elapsed_sec)}s", f"interval_{int(interval_sec)}s"]
    result = {"ts": now, "emitted": True, "coin": chosen, "direction": _DIRECTION,
              "leverage": leverage, "marginPct": round(margin_pct, 4), "anchor": kind,
              "elapsed_hours": round(elapsed_sec / 3600.0, 2), "interval_hours": interval_hours,
              "held": held_assets, "reasons": reasons}
    print(f"[tortoise.scan] EMIT {chosen} {_DIRECTION} {leverage}x marginPct={margin_pct:.2f}% | "
          f"clock={kind} elapsed={elapsed_sec / 3600.0:.1f}h interval={interval_hours}h held={held_assets}",
          file=sys.stderr)

    out = [{
        "asset": chosen,
        "direction": _DIRECTION,
        "marginPct": margin_pct,              # PERCENT in (0,100] — runtime sizes the dollars
        "leverage": leverage,                 # 1..2; runtime applies it
        "data": {
            "score": _PRODUCER_FIXED_SCORE,   # v2 producer-fixed 5 (Tortoise has no scoring)
            "leverage": leverage,
            "direction": _DIRECTION,
            "reasons": reasons,
            "anchor": kind,
            "intervalSec": float(interval_sec),
            "elapsedSec": elapsed_sec,
            "heldAssets": held_assets,
        },
    }]

    _persist(ctx, history, exits, held_assets, signaled, result)
    return out


def _persist(ctx, history, exits, held, signaled, result):
    """Persist the buy history + exit history + last-good held set + dedup map +
    this tick's result every tick; bounded by state_history_max_count. ctx.state
    advances ONLY on a clean tick, so a failed tick never records a phantom buy."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"dca_history": history, "exits": exits, "held": list(held),
                          "signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[tortoise.scan] WARNING: state append failed; next tick may re-buy "
              f"or re-emit a suppressed signal: {exc!r}", file=sys.stderr)
