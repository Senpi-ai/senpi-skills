"""CUTTLEFISH — REBALANCE scanner (the auto-adjust half; shared by both books).

This is the piece our other basket strategies don't have: a supervised re-read
of the market that ADJUSTS the existing book, not just the next entry. Signals
emitted here feed a rule-mode CLOSE_POSITION action (the first fleet use of it)
— each signal {asset, direction, reason} closes that held position; assets
without an open position are skipped by the action (`no_open_position`), so a
stale signal is harmless.

Each tick (default 900s):
  1) Read this wallet's open positions (strategy_get_clearinghouse_state).
     No positions -> record the tide and stand by.
  2) Re-read the TIDE (BTC 1h/4h) + regime + smart-money board.
  3) Track the tide-against-this-book streak in ctx.state (anti-whipsaw).
  4) Re-score every HELD name and emit close signals per scoring.close_triggers:
     tide_flip (streak >= tideFlipConfirmTicks) | divergence_reversed (SM now
     >= 58% against a held name) | basket_refresh (every regimeRefreshHours,
     default 4h: a held name re-scoring < exitScore is a stale thesis).

Fee discipline: closes here are thesis exits, deliberately rarer than DSL price
exits — the DSL still owns every price-action exit; this owns REGIME exits.
Read-only + single-pass; the CLOSE_POSITION action performs the close.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring
from scan import _asset_data, _funding_regime, _read, sm_board

_DEFAULT_REFRESH_HOURS = 4.0


def _held_positions(ctx, side):
    """This wallet's open positions in `side` from clearinghouse (dual-path,
    #453-verified shape: data.assetPositions[].position.{coin, szi})."""
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
    now = time.time()

    st = (ctx.state.last() or {}) if ctx.state else {}
    streak = int(scoring._f(st.get("tide_against_streak"), 0))
    last_refresh = scoring._f(st.get("last_refresh_ts"), 0.0)

    held = _held_positions(ctx, side)
    if held is None:
        return []                        # clearinghouse read failed — try next tick

    # ── the re-read: tide + regime + SM board ──
    btc = _asset_data(ctx, inputs.get("tideAsset", "BTC"), funding=False)
    if not btc:
        return []
    bc = btc.get("candles", {}) or {}
    tide, tide_detail = scoring.tide_from_btc(bc.get("1h", []), bc.get("4h", []))
    regime = _funding_regime(ctx)

    streak = (streak + 1) if not scoring.tide_allows(side, tide) else 0
    due_refresh = (now - last_refresh) >= refresh_s
    out = []
    scored = {}

    if held:
        board = sm_board(ctx)
        for p in held:
            md = _asset_data(ctx, p["asset"])
            if not md:
                continue
            candles = md.get("candles", {}) or {}
            th = scoring.score_asset(p["asset"], side, candles.get("1h", []),
                                     candles.get("4h", []),
                                     md.get("asset_context", {}) or {},
                                     board.get(p["asset"].upper()), regime, inputs)
            if th:
                scored[p["asset"]] = th

        for sig in scoring.close_triggers(side, tide, streak, held, scored, inputs, due_refresh):
            out.append({
                "asset": sig["asset"],
                "direction": sig["direction"],
                "data": {"trigger": sig["trigger"], "reason": sig["reason"],
                         "tide": tide, "regime": regime or "UNKNOWN",
                         "score": scoring._f((scored.get(sig["asset"]) or {}).get("score"))},
                "signal_type": sig["trigger"],
            })
            print(f"[cuttlefish.rebalance] {side} CLOSE {sig['asset']}: "
                  f"{sig['trigger']} — {sig['reason']}", file=sys.stderr)

    result = {"ts": now, "side": side, "tide": tide, "tideDetail": tide_detail,
              "regime": regime, "held": len(held), "closes": len(out),
              "streak": streak, "refreshed": due_refresh}
    if not out:
        print(f"[cuttlefish.rebalance] {side} steady: tide={tide} held={len(held)} "
              f"streak={streak}{' (refresh pass)' if due_refresh else ''}", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"tide_against_streak": streak,
                              "last_refresh_ts": now if due_refresh else last_refresh,
                              "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[cuttlefish.rebalance] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
