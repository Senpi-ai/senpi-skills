"""TORTOISE — supervised scanner (Runtime 3.0 port of the v2 Tortoise DCA scheduler).

Multi-asset whitelist (BTC/ETH/SOL by default), but NO price prediction and NO
market-data read — the "scanner" is purely a clock. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - reads the per-asset DCA-history cache + recent-signal dedup map from ctx.state,
  - filters out held assets and recently-signaled assets (no duplicate stacking),
  - picks the SINGLE most-overdue eligible asset past its DCA interval
    (never-DCA'd assets always win), via the pure scoring.pick_next_dca_asset,
  - emits ONE LONG signal sized at a FIXED marginPct of withdrawable (no tiers),
  - records the chosen asset's DCA timestamp + dedup timestamp back into ctx.state.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus a `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position. The DSL owns ALL exits.

FIDELITY NOTES vs the v2 producer (tortoise-producer.py v1.0.1):
  - v2 persisted the DCA-history cache to dca-history.json (read_dca_history /
    record_dca). This port stores it in ctx.state (the runtime's transactional
    history store) under "dca_history". Same {ASSET: epoch_seconds} semantics,
    same upper-case keys. ctx.state advances ONLY on a clean tick, so a failed
    tick never records a phantom DCA (strictly safer than the v2 file write).
  - v2 emitted exactly one signal (the most-overdue `best`). Preserved: scan()
    emits <= 1 signal/tick. Selection (pick_next_dca_asset) is verbatim.
  - v2 sizing: marginUsd = round(account_value * marginPct, 2) where marginPct
    was a FRACTION (0.08). This port emits `marginPct` as a PERCENT (8) and lets
    the runtime size (marginPct/100)*withdrawable. Fixed, no conviction tiers
    (DCA has no scoring — every fire is "valid by cadence"). leverage = min(
    config leverage, MAX_LEVERAGE=3) — verbatim clamp.
  - v2 filtered candidates: NOT held (no duplicate stacking) AND NOT recently
    signaled (race-window dedup). Both preserved; the held-asset filter runs off
    the read-sanity-guarded clearinghouse read (ported verbatim).
  - v2 data block: score=5 (producer-fixed), wire score=0.7 (static — DCA
    conviction comes from cadence, not scoring), leverage, direction LONG,
    reasons [dca_cadence, elapsed_Ns, interval_Ns], intervalSec, elapsedSec,
    heldAssets. Preserved (the `data.score` slot carries the v2 producer-fixed 5).
  - v2 push_signal hard-skipped if the chosen asset was already held; here that
    asset is already excluded from the candidate set, so the guard is redundant
    but kept as defence-in-depth.
"""

import sys
import time

import scoring

# v2 defaults (tortoise-producer.py / tortoise-config.json)
_DEFAULT_ASSETS = ["BTC", "ETH", "SOL"]      # v2 DEFAULT_ASSETS
_DEFAULT_INTERVAL_HOURS = 24.0               # v2 DEFAULT_INTERVAL_HOURS
_DEFAULT_MARGIN_PCT = 8.0                     # PERCENT; v2 marginPct 0.08 (FRACTION) x100
_DEFAULT_LEVERAGE = 2                         # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 3                             # v2 MAX_LEVERAGE — DCA is accumulation, not leverage
_DEFAULT_RECENT_TTL = 240                     # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup
_DIRECTION = "LONG"                           # v2 DEFAULT_DIRECTION — DCA = accumulate longs
_PRODUCER_FIXED_SCORE = 5                     # v2 data.score (producer-fixed; DCA has no scoring)


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[tortoise.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", "")})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
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


# ── ctx.state: DCA-history cache + recent-signal dedup map ──
# v2 persisted these to dca-history.json + recent-signals.json. Here both ride in
# the latest ctx.state record; we read the last record, mutate, and append a fresh
# one every tick (bounded by state_history_max_count).

def _load_state(ctx):
    """Returns (dca_history{ASSET:ts}, signaled{ASSET:ts}) from the latest record."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}, {}
    last = ctx.state.last() or {}
    hist = last.get("dca_history", {})
    sig = last.get("signaled", {})
    hist = {str(k).upper(): scoring._f(v) for k, v in hist.items()} if isinstance(hist, dict) else {}
    sig = {str(k).upper(): scoring._f(v) for k, v in sig.items()} if isinstance(sig, dict) else {}
    return hist, sig


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


def scan(inputs, ctx):
    now = time.time()
    assets = inputs.get("assets", _DEFAULT_ASSETS)
    interval_hours = float(inputs.get("intervalHours", _DEFAULT_INTERVAL_HOURS))
    interval_sec = interval_hours * 3600.0
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))   # PERCENT in (0,100]
    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)   # v2 clamp
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    history, signaled = _load_state(ctx)
    signaled = _prune_signaled(signaled, ttl, now)

    out = []
    if account_value <= 0:
        # v2: no account value -> silent ok (no signal). Still persist state so the
        # cache/dedup map survive the tick.
        result = {"ts": now, "emitted": False, "note": "no account value",
                  "held": held_assets}
        print("[tortoise.scan] WAITING — no account value", file=sys.stderr)
        _persist(ctx, history, signaled, result)
        return out

    # Filter: an asset already held is not a candidate (no duplicate stacking);
    # an asset recently signaled is in the race-window cooldown. Both verbatim v2.
    eligible = [a for a in assets
                if str(a).upper() not in held_set
                and not _was_recently_signaled(signaled, str(a), ttl, now)]

    chosen = scoring.pick_next_dca_asset(eligible, history, interval_sec, now)

    if chosen is None:
        next_due = scoring.next_due_seconds(eligible, history, interval_sec, now)
        next_due_min = round(next_due / 60.0, 1) if next_due is not None else None
        result = {"ts": now, "emitted": False, "assets": list(assets), "held": held_assets,
                  "next_due_in_min": next_due_min,
                  "note": "WAITING — no asset past its DCA interval (or all in cooldown/held)"}
        print(f"[tortoise.scan] WAITING — no asset due (next in {next_due_min}min); "
              f"assets={list(assets)} held={held_assets}", file=sys.stderr)
        _persist(ctx, history, signaled, result)
        return out

    # Defence-in-depth (redundant — chosen is already excluded from held_set).
    if chosen in held_set:
        result = {"ts": now, "emitted": False, "note": f"chosen {chosen} held — skip",
                  "held": held_assets}
        print(f"[tortoise.scan] SKIP — chosen {chosen} already held", file=sys.stderr)
        _persist(ctx, history, signaled, result)
        return out

    elapsed = scoring.seconds_since(history.get(chosen), now)
    elapsed_sec = float(elapsed or 0.0)

    # record the DCA + dedup timestamps (mirrors v2 record_dca + record_signal)
    history[chosen] = now
    signaled[chosen] = now

    reasons = ["dca_cadence", f"elapsed_{int(elapsed_sec)}s", f"interval_{int(interval_sec)}s"]
    result = {"ts": now, "emitted": True, "coin": chosen, "direction": _DIRECTION,
              "leverage": leverage, "marginPct": round(margin_pct, 4),
              "elapsed_hours": round(elapsed_sec / 3600.0, 2), "interval_hours": interval_hours,
              "held": held_assets, "reasons": reasons}
    print(f"[tortoise.scan] EMIT {chosen} {_DIRECTION} {leverage}x marginPct={margin_pct:.2f}% | "
          f"elapsed={elapsed_sec / 3600.0:.1f}h interval={interval_hours}h held={held_assets}",
          file=sys.stderr)

    out = [{
        "asset": chosen,
        "direction": _DIRECTION,
        "marginPct": margin_pct,              # PERCENT in (0,100] — runtime sizes the dollars
        "leverage": leverage,                 # 1..3; runtime applies it
        "data": {
            "score": _PRODUCER_FIXED_SCORE,   # v2 producer-fixed 5 (DCA has no scoring)
            "leverage": leverage,
            "direction": _DIRECTION,
            "reasons": reasons,
            "intervalSec": float(interval_sec),
            "elapsedSec": elapsed_sec,
            "heldAssets": held_assets,
        },
    }]

    _persist(ctx, history, signaled, result)
    return out


def _persist(ctx, history, signaled, result):
    """Persist the DCA-history cache + dedup map + this tick's result every tick;
    bounded by state_history_max_count. ctx.state advances ONLY on a clean tick,
    so a failed tick never records a phantom DCA timestamp."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"dca_history": history, "signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[tortoise.scan] WARNING: state append failed; next tick may re-DCA "
              f"or re-emit a suppressed signal: {exc!r}", file=sys.stderr)
