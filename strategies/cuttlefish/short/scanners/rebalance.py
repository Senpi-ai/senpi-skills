"""CUTTLEFISH — REBALANCE scanner (the auto-adjust half; shared by both books).

The 15-minute re-read that ADJUSTS the existing book via the rule-mode
CLOSE_POSITION action (closes are idempotent — `no_open_position` skips):

  pulse_flip          — the cross-asset pulse day (real market-pulse engine,
                        not a BTC proxy) has been against this book for
                        `pulseFlipConfirmTicks` consecutive ticks -> close the
                        whole book (anti-whipsaw via the streak).
  divergence_reversed — the PROVEN cohort (smart-money engine: lifetime
                        realized >= $1M) leans >= leanThreshold against a held
                        name -> close it now. Board >= 58% fallback when
                        cohorts are unavailable — flagged in the reason.
  basket_refresh      — every regimeRefreshHours (default 4h) held names
                        re-scoring below exitScore are recycled.

Cohorts ride the same 4h-clock cache pattern as the entries scanner, but in a
SEPARATE per-scanner cache. Coherence does NOT depend on the two caches
agreeing: closes fire only on per-name DEATH below the entry bars — score <
exitScore (<< minScore) or the cohort DECISIVELY reversed (reversalThreshold >
the entry leanThreshold) — so a name entries just opened cannot be closed here
even if the caches momentarily disagree (scoring.enforce_hysteresis guards it).
Fee discipline: closes here are thesis exits; the DSL owns every price exit.
Read-only + single-pass; the CLOSE_POSITION action performs the close.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring
from scan import _asset_data, _funding_regime, _read, cached_cohorts, market_read, sm_board

_DEFAULT_REFRESH_HOURS = 4.0


def _held_positions(ctx, side):
    """This wallet's open positions in `side` (dual-path, #453-verified shape)."""
    data = _read(ctx, "strategy_get_clearinghouse_state",
                 {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(data, dict):
        return None                      # read failed — distinct from genuinely flat
    positions = data.get("assetPositions", data.get("asset_positions", [])) or []
    held = []
    for entry in positions:
        pos = entry.get("position", entry) if isinstance(entry, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        szi = scoring._f(pos.get("szi"))
        if not coin or szi == 0:
            continue
        direction = "LONG" if szi > 0 else "SHORT"
        if direction == side:
            held.append({"asset": coin, "direction": direction, "szi": szi})
    return held


def scan(inputs, ctx):
    side = (inputs.get("side", "LONG") or "LONG").upper()
    refresh_s = scoring._f(inputs.get("regimeRefreshHours"), _DEFAULT_REFRESH_HOURS) * 3600.0
    cfg = inputs.get("cohorts") or {}
    now = time.time()

    st = (ctx.state.last() or {}) if ctx.state else {}
    streak = int(scoring._f(st.get("pulse_against_streak"), 0))
    last_refresh = scoring._f(st.get("last_refresh_ts"), 0.0)

    held = _held_positions(ctx, side)
    if held is None:
        return []                        # clearinghouse read failed — try next tick

    # ── the re-read: pulse (cross-asset) + cohorts (4h cache) ──
    universe, pulse, _changes = market_read(ctx, inputs)
    if pulse is None:
        return []
    day = pulse.get("day")
    cohort, cohort_ts = cached_cohorts(ctx, inputs, st, now)

    streak = (streak + 1) if not scoring.pulse_allows(side, day) else 0
    due_refresh = (now - last_refresh) >= refresh_s
    out = []
    views = {}

    if held:
        board = sm_board(ctx)
        regime = _funding_regime(ctx)
        for p in held:
            cv = scoring.cohort_view_for(p["asset"], side, cohort, cfg)
            nt = board.get(p["asset"].upper()) or {}
            view = {"cohort": cv, "score": None,
                    "nt_dir": nt.get("direction", "NEUTRAL"),
                    "nt_pct": scoring._f(nt.get("pct"), 50)}
            if due_refresh:              # re-score only when the refresh needs it
                md = _asset_data(ctx, p["asset"])
                if md:
                    candles = md.get("candles", {}) or {}
                    th = scoring.score_asset(p["asset"], side, candles.get("1h", []),
                                             candles.get("4h", []),
                                             md.get("asset_context", {}) or {}, cv,
                                             board.get(p["asset"].upper()), regime, inputs)
                    if th:
                        view["score"] = 0.0 if th.get("blocked") else th["score"]
            views[p["asset"]] = view

        for sig in scoring.close_triggers(side, day, streak, held, views, inputs, due_refresh):
            out.append({
                "asset": sig["asset"],
                "direction": sig["direction"],
                "data": {"trigger": sig["trigger"], "reason": sig["reason"],
                         "pulseDay": day or "no_read",
                         "score": scoring._f((views.get(sig["asset"]) or {}).get("score"))},
                "signal_type": sig["trigger"],
            })
            print(f"[cuttlefish.rebalance] {side} CLOSE {sig['asset']}: "
                  f"{sig['trigger']} — {sig['reason']}", file=sys.stderr)

    result = {"ts": now, "side": side, "pulseDay": day,
              "cohortsAvailable": cohort.get("available"), "held": len(held),
              "closes": len(out), "streak": streak, "refreshed": due_refresh}
    if not out:
        print(f"[cuttlefish.rebalance] {side} steady: day={day} held={len(held)} "
              f"streak={streak}{' (refresh pass)' if due_refresh else ''}", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"pulse_against_streak": streak,
                              "last_refresh_ts": now if due_refresh else last_refresh,
                              "cohort": cohort, "cohort_refreshed_at": cohort_ts,
                              "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[cuttlefish.rebalance] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
