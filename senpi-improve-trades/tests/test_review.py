#!/usr/bin/env python3
"""Offline engine test — runs review.run() against a recorded MCP fixture (no network).

The fixture reproduces the core scenarios the skill must get right:
  - SOL long: price FELL after the exit → holding would have lost → the exit BEAT holding (and it was a
    real DSL exit: a SL_TRIGGERED ratchet record at tier 2).
  - ETH long: price ROSE after the exit → holding would have made more → the exit was WORSE.
  - BTC SHORT: price FELL after the exit → a short GAINS when price falls, so holding would have made
    more → WORSE. This guards the direction sign for a short.
  - HYPE: a big mover the book never held → a book-vs-market GAP.
  - fail-open: a missing source degrades a field, not the whole run.

    python3 -m pytest senpi-improve-trades/tests/   # or: python3 tests/test_review.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import review  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "review_fixture.json")
REGISTRY_DIR = os.path.join(HERE, "fixtures", "registry")   # holds installed_runtimes.json (kodiak)
KODIAK_WALLET = "0xKODIAK00000000000000000000000000000kdk"
# now just after the last fixture closeTime; a 30d window covers all three trades
NOW_MS = 1782800100000
WINDOW_DAYS = 30


def _result(want_market=True, last_n=None):
    with open(FIXTURE) as f:
        client = review._FixtureClient(json.load(f))
    old = os.environ.get("SENPI_STATE_DIR")
    os.environ["SENPI_STATE_DIR"] = REGISTRY_DIR
    try:
        return review.run(client, window_days=WINDOW_DAYS, last_n=last_n,
                          want_market=want_market, now_ms=NOW_MS)
    finally:
        if old is None:
            os.environ.pop("SENPI_STATE_DIR", None)
        else:
            os.environ["SENPI_STATE_DIR"] = old


def _by_asset(res):
    return {t["asset"]: t for t in res["trades"]}


def test_all_three_trades_collected_and_tagged_reconstructed():
    res = _result()
    assert res["meta"]["trade_count"] == 3
    assert all(t["source"] == "reconstructed" for t in res["trades"])   # source boundary tag


def test_if_held_and_since_exit_compute_for_a_long():
    """SOL long: exit 150, price now 130 → since_exit -13.33%; notional 3×150=450, long → if_held
    450×(130-150)/150 = -60. Holding would have LOST → the exit BEAT holding."""
    sol = _by_asset(_result())["SOL"]
    assert sol["price_since_exit_pct"] == -13.33
    assert sol["if_held_delta_usd"] == -60.0
    assert sol["exit_vs_hold"] == "beat"


def test_exit_worse_when_price_ran_after_a_long_exit():
    """ETH long: exit 2000, price now 2200 → +10%; notional 0.5×2000=1000, long → if_held +100.
    Holding would have made more → the exit was WORSE."""
    eth = _by_asset(_result())["ETH"]
    assert eth["price_since_exit_pct"] == 10.0
    assert eth["if_held_delta_usd"] == 100.0
    assert eth["exit_vs_hold"] == "worse"


def test_short_sign_is_direction_adjusted():
    """THE short-sign guard. BTC SHORT: exit 100000, price now 95000 → price fell 5%. A short GAINS when
    price falls, so the counterfactual FLIPS sign: notional 0.05×100000=5000, if_held = 5000×+0.05 =
    +250 (NOT -250). Holding would have made more → WORSE."""
    btc = _by_asset(_result())["BTC"]
    assert btc["direction"] == "short"
    assert btc["price_since_exit_pct"] == -5.0        # raw price move is down
    assert btc["if_held_delta_usd"] == 250.0          # but the SHORT counterfactual is POSITIVE
    assert btc["exit_vs_hold"] == "worse"


def test_timing_summary_counts_beat_vs_worse():
    """PROCESS-framed aggregate: 1 exit beat holding (SOL), 2 were worse (ETH, BTC). Realized total 340;
    if_all_reclosed_now is the honest counterfactual aggregate (-60+100+250 = 290), CONTEXT not a
    projection."""
    ts = _result()["timing_summary"]
    assert ts["trade_count"] == 3
    assert ts["exits_beat_holding"] == 1
    assert ts["exits_worse"] == 2
    assert ts["exits_flat"] == 0
    assert ts["realized_pnl_total"] == 340.0
    assert ts["if_all_reclosed_now_total"] == 290.0


def test_no_forward_projection_fields_exist():
    """Guardrail 3 structural guard: the engine NEVER emits a $/week or forward-projection field. Only
    realized totals + engine counterfactuals are present."""
    ts = _result()["timing_summary"]
    keys = " ".join(ts.keys()).lower()
    for banned in ("per_week", "weekly", "projected", "forecast", "expected_gain", "per_day"):
        assert banned not in keys


def test_exit_reason_maps_sl_triggered_to_tier():
    """Authoritative exit attribution: SOL's SL_TRIGGERED ratchet record → terminal SL_TRIGGERED,
    tier_reached = currentTierIndex (2), high_water_roe (41.0). This tells the narrator WHICH DSL lever
    to tune."""
    sol = _by_asset(_result())["SOL"]
    er = sol["exit_reason"]
    assert er["terminal"] == "SL_TRIGGERED"
    assert er["tier_reached"] == 2
    assert er["high_water_roe"] == 41.0


def test_exit_reason_unknown_when_no_terminal_record():
    """Honest sourcing: BTC has no ratchet record → terminal UNKNOWN, never guessed. (ETH has an ACTIVE
    record, which is not a terminal exit for a closed trade → also UNKNOWN.)"""
    by = _by_asset(_result())
    assert by["BTC"]["exit_reason"]["terminal"] == "UNKNOWN"
    assert by["ETH"]["exit_reason"]["terminal"] == "UNKNOWN"


def test_book_vs_market_gap_surfaces_unheld_mover():
    """'What did I miss': HYPE is the biggest mover (+18% 4h) and the book never held it → it appears in
    gaps. SOL/ETH/BTC were held → NOT gaps. Dominant-direction dedup keeps ONE HYPE entry."""
    bvm = _result()["book_vs_market"]
    movers = {m["asset"]: m for m in bvm["top_movers"]}
    assert "HYPE" in movers and movers["HYPE"]["pct"] == 18.0     # dominant-direction entry, one per token
    gap_assets = {g["asset"] for g in bvm["gaps"]}
    assert "HYPE" in gap_assets
    assert "SOL" not in gap_assets and "ETH" not in gap_assets and "BTC" not in gap_assets
    assert bvm["window"] == "4h"


def test_participation_alignment_flags():
    """participation records whether the book was on the RIGHT side of a mover it held. ETH: held long,
    ETH moved +9% → aligned True. SOL: held long, SOL moved -8.5% → aligned False (wrong side)."""
    part = {p["asset"]: p for p in _result()["book_vs_market"]["participation"]}
    assert part["ETH"]["held"] is True and part["ETH"]["aligned"] is True
    assert part["SOL"]["held"] is True and part["SOL"]["aligned"] is False


def test_strategy_read_carries_mandate_and_dsl_lever():
    """Per-strategy read: mandate from the deployed runtime.yaml registry (kodiak), DSL ladder attached
    (the fix lever), realized PnL as evidence — 3 closed trades summing to 340."""
    strat = {s["label"]: s for s in _result()["strategies"]}["kodiak"]
    assert isinstance(strat["mandate"], str) and "KODIAK" in strat["mandate"]
    assert strat["dsl"]["hard_stop_roe_pct"] == -15.0      # the hard-stop lever
    assert strat["dsl"]["arm_at_roe_pct"] == 10            # the arm-at lever
    assert strat["closed_trade_count"] == 3
    assert strat["realized_pnl"] == 340.0


def test_last_n_caps_trade_count():
    """--last N keeps only the N most-recent closed trades (by close time). last_n=1 → only BTC (latest)."""
    res = _result(last_n=1)
    assert res["meta"]["trade_count"] == 1
    assert res["trades"][0]["asset"] == "BTC"             # the most recent close
    assert res["window"]["label"] == "last 1 closed trades"


def test_fails_open_when_market_source_missing():
    """want_market=False → no price pull: if_held is None, exits are 'unknown', book_vs_market is empty —
    but the run still returns valid JSON with the trades + realized PnL. A missing source degrades a
    field, not the whole review."""
    res = _result(want_market=False)
    assert res["meta"]["trade_count"] == 3                # trades still collected
    assert all(t["if_held_delta_usd"] is None for t in res["trades"])
    assert res["timing_summary"]["exits_unknown"] == 3
    assert res["book_vs_market"]["top_movers"] == []
    assert res["timing_summary"]["realized_pnl_total"] == 340.0   # realized PnL is source-independent


def test_fails_open_on_empty_everything():
    """No strategies at all → valid JSON + meta.degraded (points at the token scope), never a crash."""
    res = review.run(review._FixtureClient({}), window_days=WINDOW_DAYS, now_ms=NOW_MS)
    assert "trades" in res and res["trades"] == []
    assert res["meta"].get("degraded")


def test_fails_open_when_ratchet_source_missing():
    """No ratchet_stop_list data → exit_reason falls back to UNKNOWN for every trade, but the timing math
    (which only needs prices) still computes. Exit attribution degrades in isolation."""
    with open(FIXTURE) as f:
        raw = json.load(f)
    raw.pop("ratchet_stop_list::0xkodiak00000000000000000000000000000kdk", None)
    old = os.environ.get("SENPI_STATE_DIR")
    os.environ["SENPI_STATE_DIR"] = REGISTRY_DIR
    try:
        res = review.run(review._FixtureClient(raw), window_days=WINDOW_DAYS, now_ms=NOW_MS)
    finally:
        if old is None:
            os.environ.pop("SENPI_STATE_DIR", None)
        else:
            os.environ["SENPI_STATE_DIR"] = old
    assert all(t["exit_reason"]["terminal"] == "UNKNOWN" for t in res["trades"])
    # timing still works — SOL still beat holding
    assert {t["asset"]: t["exit_vs_hold"] for t in res["trades"]}["SOL"] == "beat"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
