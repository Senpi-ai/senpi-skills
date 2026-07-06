"""GORILLA — RETHINK + REBALANCE scanner (the close half; shared by both books).

Deliberately LOW-TURNOVER: between boundaries every tick is a cheap clock check
that emits nothing (the DSL owns all price exits in the meantime). Signals here
feed a rule-mode CLOSE_POSITION action; closes are idempotent (assets without
an open position skip as `no_open_position`).

  48h RETHINK  (thesisRefreshHours, anchored at establishment): re-derive the
      thesis from the full market read (pulse + cohorts); close held names the
      PROVEN cohort has flipped against (`divergence_reversed`) and names that
      left this book's bucket (`thesis_shift`, reason carries old->new stance).
  7d REBALANCE (rebalanceDays, same anchor): re-score held names within the
      standing thesis; close names below exitScore -> `weekly_rebalance`.

State (own copy — per-scanner isolation): thesis, established_at,
last_rethink, last_rebalance. First tick establishes if the entries scanner
hasn't visibly run yet (determinism keeps the two copies aligned; drift is
bounded by one tick interval).
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring
from scan import _asset_data, held_positions, refresh_thesis, sm_board


def scan(inputs, ctx):
    side = (inputs.get("side", "LONG") or "LONG").upper()
    rethink_s = scoring._f(inputs.get("thesisRefreshHours"), 48.0) * 3600.0
    rebalance_s = scoring._f(inputs.get("rebalanceDays"), 7.0) * 86400.0
    now = time.time()

    st = (ctx.state.last() or {}) if ctx.state else {}
    thesis = st.get("thesis")
    established_at = scoring._f(st.get("established_at"), 0.0)
    last_rethink = scoring._f(st.get("last_rethink"), 0.0)
    last_rebalance = scoring._f(st.get("last_rebalance"), 0.0)

    # ── establish once (first tick after deploy) ──
    if not thesis:
        fresh, _board = refresh_thesis(ctx, inputs, now, None)
        if fresh:
            thesis = fresh
            established_at = last_rethink = last_rebalance = now
        if ctx.state is not None:
            try:
                ctx.state.append({"thesis": thesis, "established_at": established_at,
                                  "last_rethink": last_rethink,
                                  "last_rebalance": last_rebalance,
                                  "result": {"ts": now, "side": side,
                                             "phase": "established" if thesis else "unreadable"}})
            except Exception as exc:  # noqa: BLE001
                print(f"[gorilla.rebalance] WARNING: state append failed: {exc!r}",
                      file=sys.stderr)
        return []

    thesis_due = scoring.due(now, last_rethink, rethink_s)
    rebalance_due = scoring.due(now, last_rebalance, rebalance_s)
    if not thesis_due and not rebalance_due:
        # cheap tick — nothing re-decides between boundaries
        return []

    held = held_positions(ctx, side)
    if held is None:
        return []                        # clearinghouse unreadable — retry next tick

    out = []
    new_thesis, scored = None, {}
    if thesis_due:
        new_thesis, _board = refresh_thesis(ctx, inputs, now, thesis)

    if rebalance_due and held:
        board = sm_board(ctx)
        for p in held:
            md = _asset_data(ctx, p["asset"])
            if not md:
                continue
            candles = md.get("candles", {}) or {}
            th = scoring.score_entry(p["asset"], side, candles.get("1h", []),
                                     candles.get("4h", []),
                                     board.get(p["asset"].upper()), inputs)
            if th:
                scored[p["asset"]] = th

    for sig in scoring.close_triggers(side, held, new_thesis, thesis, scored, inputs,
                                      thesis_due and new_thesis is not None, rebalance_due):
        out.append({
            "asset": sig["asset"],
            "direction": sig["direction"],
            "data": {"trigger": sig["trigger"], "reason": sig["reason"],
                     "stance": (new_thesis or thesis).get("stance", "?"),
                     "score": scoring._f((scored.get(sig["asset"]) or {}).get("score"))},
            "signal_type": sig["trigger"],
        })
        print(f"[gorilla.rebalance] {side} CLOSE {sig['asset']}: "
              f"{sig['trigger']} — {sig['reason']}", file=sys.stderr)

    if thesis_due and new_thesis:
        thesis = new_thesis
        last_rethink = now
    if rebalance_due:
        last_rebalance = now

    if not out:
        print(f"[gorilla.rebalance] {side} boundary pass: rethink={thesis_due} "
              f"rebalance={rebalance_due} held={len(held)} — no closes", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"thesis": thesis, "established_at": established_at,
                              "last_rethink": last_rethink, "last_rebalance": last_rebalance,
                              "result": {"ts": now, "side": side, "closes": len(out),
                                         "rethink": thesis_due, "rebalance": rebalance_due,
                                         "narrative": thesis.get("narrative", "")}})
        except Exception as exc:  # noqa: BLE001
            print(f"[gorilla.rebalance] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
