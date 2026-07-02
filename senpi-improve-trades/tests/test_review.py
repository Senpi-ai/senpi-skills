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
MIXED_FIXTURE = os.path.join(HERE, "fixtures", "review_mixed_status_fixture.json")
REGISTRY_DIR = os.path.join(HERE, "fixtures", "registry")   # holds installed_runtimes.json (kodiak)
KODIAK_WALLET = "0xKODIAK00000000000000000000000000000kdk"
# now just after the last fixture closeTime; a 30d window covers all three trades
NOW_MS = 1782800100000
WINDOW_DAYS = 30


def _run_with_registry(client, want_market=True, last_n=None):
    """Run the engine with the fixture registry dir pinned via SENPI_STATE_DIR (restored after)."""
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


def _result(want_market=True, last_n=None):
    with open(FIXTURE) as f:
        client = review._FixtureClient(json.load(f))
    return _run_with_registry(client, want_market=want_market, last_n=last_n)


def _mixed_result(want_market=True, last_n=None):
    with open(MIXED_FIXTURE) as f:
        client = review._FixtureClient(json.load(f))
    return _run_with_registry(client, want_market=want_market, last_n=last_n)


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


# ──────────────────────────────────────────────────────── current vs closed partition (the live-run fix)
# The mixed fixture: kodiak (ACTIVE) + grizzly (PAUSED) are the CURRENT book; a same-label kodiak (CLOSED)
# is a churned historical redeployment. Each has closed trades. The fix: closed strategies are HISTORY —
# their trades stay in trades[], but they NEVER get a per-strategy verdict and NEVER seed a "consolidate
# your N wallets" narrative out of what is really a redeployment.
CLOSED_WALLET = "0xKODIAKOLD00000000000000000000000000old"


def test_strategies_are_current_only():
    """strategies[] (the per-strategy VERDICT surface) contains ONLY the active/paused ones — the CLOSED
    kodiak redeployment is excluded. Two current strategies: kodiak (ACTIVE) + grizzly (PAUSED)."""
    res = _mixed_result()
    labels_status = {(s["label"], s["status"]) for s in res["strategies"]}
    assert labels_status == {("kodiak", "ACTIVE"), ("grizzly", "PAUSED")}
    wallets = {str(s["wallet"]).lower() for s in res["strategies"]}
    assert CLOSED_WALLET.lower() not in wallets       # the closed redeployment is NOT a live-book verdict


def test_closed_strategies_rollup_shape():
    """closed_strategies[] holds the CLOSED strategy as a minimal HISTORICAL rollup ONLY — exactly
    {label, wallet_short, status, trade_count, realized_pnl}. NO mandate/dsl/verdict/on_mandate fields
    (it's deregistered by design; never judged). Its 2 trades (BTC + DOGE) sum to 220."""
    res = _mixed_result()
    assert len(res["closed_strategies"]) == 1
    cs = res["closed_strategies"][0]
    assert cs["label"] == "kodiak" and cs["status"] == "CLOSED"
    assert cs["trade_count"] == 2                       # BTC + DOGE on the closed wallet
    assert cs["realized_pnl"] == 220.0                  # 200 + 20
    assert set(cs.keys()) == {"label", "wallet_short", "status", "trade_count", "realized_pnl"}
    # explicitly NO verdict/mandate leakage onto a closed strategy
    for banned in ("mandate", "dsl", "on_mandate_note", "verdict"):
        assert banned not in cs


def test_closed_strategy_trades_still_in_trades():
    """The closed strategy's trades STILL live in trades[] (the timing history is complete), attributed by
    label + status. BTC + DOGE (on the CLOSED kodiak) are present and tagged strategy_status CLOSED."""
    res = _mixed_result()
    by = {t["asset"]: t for t in res["trades"]}
    assert "BTC" in by and "DOGE" in by                 # closed-strategy trades kept in the timing set
    assert by["BTC"]["strategy_status"] == "CLOSED"
    assert by["DOGE"]["strategy_status"] == "CLOSED"
    assert by["DOGE"]["strategy_label"] == "kodiak"
    # current-book trades carry their live status
    assert by["SOL"]["strategy_status"] == "ACTIVE"
    assert by["ETH"]["strategy_status"] == "PAUSED"
    assert res["meta"]["trade_count"] == 4              # SOL + ETH + BTC + DOGE — all statuses in trades[]


def test_meta_current_and_closed_counts():
    """meta carries the split: 2 current (active+paused), 1 closed. strategy_count is still the total (3)."""
    meta = _mixed_result()["meta"]
    assert meta["current_strategy_count"] == 2
    assert meta["closed_strategy_count"] == 1
    assert meta["strategy_count"] == 3


def test_current_missing_mandate_is_not_a_bug():
    """A CURRENT strategy with no mandate on file (grizzly PAUSED — absent from the registry) → the note
    says look it up / check the registry and is explicitly NOT framed as a bug. (Guards Fix B's rule that
    an absent mandate on a live strategy is a lookup, on a closed one is silence.)"""
    strat = {s["label"]: s for s in _mixed_result()["strategies"]}
    griz = strat["grizzly"]
    assert griz["status"] == "PAUSED"
    assert griz["mandate"] is None
    note = griz["on_mandate_note"].lower()
    # framed as a lookup, NOT a defect: it explicitly disclaims "bug" and points at the registry
    assert "not a bug" in note
    assert "registry" in note or "look it up" in note


# ──────────────────────────────────────────── telemetry enrichment (event log ENRICHES discovery trades)
# The rule: onchain data → discovery (the trade list + all onchain facts); runtime events → telemetry.
# Telemetry fills each discovery trade's exit_reason and produces missed_signals[]; it NEVER reconstructs a
# trade or re-derives a price/PnL. Read offline via SENPI_EVENTS_FIXTURE keyed by runtime id (kodiak-main),
# so there is NO subprocess in tests.
EVENTS_FIXTURE = os.path.join(HERE, "fixtures", "events_fixture.json")


def _result_with_telemetry(want_market=True, last_n=None):
    """Run the engine with the registry dir AND the event-log fixture pinned (both restored after)."""
    old_ev = os.environ.get("SENPI_EVENTS_FIXTURE")
    os.environ["SENPI_EVENTS_FIXTURE"] = EVENTS_FIXTURE
    try:
        return _result(want_market=want_market, last_n=last_n)
    finally:
        if old_ev is None:
            os.environ.pop("SENPI_EVENTS_FIXTURE", None)
        else:
            os.environ["SENPI_EVENTS_FIXTURE"] = old_ev


def test_telemetry_dsl_closed_enriches_exit_reason_by_asset_time():
    """A discovery SOL trade + a matching telemetry `dsl.closed` (no order_id → matched by asset +
    close_time within ~2min): exit_reason.terminal == 'tier_breach', tier_index == 2, source ==
    'telemetry'. Telemetry writes exit_reason ONLY — the discovery px/pnl/direction are UNCHANGED."""
    sol = _by_asset(_result_with_telemetry())["SOL"]
    er = sol["exit_reason"]
    assert er["terminal"] == "tier_breach"          # native close_reason, not reconstructed
    assert er["tier_index"] == 2
    assert er["high_water_roe"] == 41.0
    assert er["source"] == "telemetry"
    assert sol["source"] == "telemetry"             # the trade row reflects telemetry enrichment
    # discovery still OWNS the onchain facts — telemetry did not touch them
    assert sol["entry_px"] == 120.0 and sol["exit_px"] == 150.0
    assert sol["realized_pnl"] == 90.0
    assert sol["direction"] == "long"
    assert sol["price_since_exit_pct"] == -13.33    # counterfactual math still discovery+market
    assert sol["if_held_delta_usd"] == -60.0


def test_telemetry_position_closed_matches_by_order_id():
    """ETH's `position.closed` carries senpi.order.id == the discovery closedOrderId (0xETH) → matched by
    the EXACT order-id lane (priority over asset+time). terminal 'max_retrace', source telemetry."""
    eth = _by_asset(_result_with_telemetry())["ETH"]
    assert eth["exit_reason"]["terminal"] == "max_retrace"
    assert eth["exit_reason"]["source"] == "telemetry"
    assert eth["exit_reason"]["high_water_roe"] == 5.2   # from senpi.position.roe


def test_telemetry_no_match_leaves_ratchet_or_unknown():
    """BTC has no telemetry exit event and no ratchet record → exit_reason stays honest UNKNOWN, never
    guessed. The discovery trade is untouched (still a short, pnl 200)."""
    btc = _by_asset(_result_with_telemetry())["BTC"]
    assert btc["exit_reason"]["terminal"] == "UNKNOWN"
    assert btc["source"] == "reconstructed"          # no telemetry enrichment for this row
    assert btc["direction"] == "short" and btc["realized_pnl"] == 200.0


def test_missed_signals_carry_rejected_and_blocked():
    """`missed_signals[]` = the telemetry-native 'what did I miss': signal.outcome with result
    rejected/blocked (accepted is EXCLUDED). HYPE rejected/no_slots + AVAX blocked/risk_gate present;
    the accepted SOL signal is NOT."""
    res = _result_with_telemetry()
    ms = {m["asset"]: m for m in res["missed_signals"]}
    assert "HYPE" in ms and ms["HYPE"]["result"] == "rejected"
    assert ms["HYPE"]["reason_code"] == "no_slots"
    assert ms["HYPE"]["direction"] == "long" and ms["HYPE"]["score"] == 12.5
    assert ms["HYPE"]["strategy_label"] == "kodiak"
    assert "AVAX" in ms and ms["AVAX"]["result"] == "blocked"
    assert ms["AVAX"]["reason_code"] == "risk_gate_max_drawdown"
    assert "SOL" not in ms                            # accepted signal is not a 'miss'
    assert res["meta"]["missed_signal_count"] == 2


def test_meta_telemetry_source_available_and_counts():
    """With telemetry present and landing on trades, meta.telemetry_source == 'available' and the
    exit_reason_source_counts split them: SOL + ETH telemetry, BTC unknown (no telemetry, no ratchet)."""
    meta = _result_with_telemetry()["meta"]
    assert meta["telemetry_source"] == "available"
    counts = meta["exit_reason_source_counts"]
    assert counts["telemetry"] == 2                  # SOL (dsl.closed) + ETH (position.closed)
    assert counts["unknown"] == 1                    # BTC — neither telemetry nor ratchet
    assert counts.get("ratchet", 0) == 0
    assert "_telemetry_warned" not in meta           # internal flag not leaked into the contract


def test_telemetry_unavailable_leaves_discovery_intact():
    """No event fixture (and no openclaw in tests) → telemetry FAILS OPEN: discovery trades intact,
    exit_reason falls back to ratchet/UNKNOWN, meta.telemetry_source == 'unavailable', missed_signals
    empty, NO crash. This is the older-build / closed-strategy-ring-gone path."""
    res = _result()                                  # base helper: no SENPI_EVENTS_FIXTURE
    assert res["meta"]["trade_count"] == 3           # discovery path fully intact
    assert res["meta"]["telemetry_source"] == "unavailable"
    assert res["missed_signals"] == []
    assert res["meta"]["missed_signal_count"] == 0
    by = _by_asset(res)
    # SOL still attributed by the ratchet SECONDARY fallback (SL_TRIGGERED), BTC honest UNKNOWN
    assert by["SOL"]["exit_reason"]["terminal"] == "SL_TRIGGERED"
    assert by["SOL"]["exit_reason"]["source"] == "ratchet"
    assert by["BTC"]["exit_reason"]["terminal"] == "UNKNOWN"
    # discovery facts untouched with zero telemetry
    assert by["SOL"]["realized_pnl"] == 90.0 and by["BTC"]["direction"] == "short"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
