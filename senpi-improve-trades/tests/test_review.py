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
    assert sol["exit_vs_hold"] == "exit_ahead"


def test_exit_worse_when_price_ran_after_a_long_exit():
    """ETH long: exit 2000, price now 2200 → +10%; notional 0.5×2000=1000, long → if_held +100.
    Holding would have made more → the exit was WORSE."""
    eth = _by_asset(_result())["ETH"]
    assert eth["price_since_exit_pct"] == 10.0
    assert eth["if_held_delta_usd"] == 100.0
    assert eth["exit_vs_hold"] == "held_higher"


def test_short_sign_is_direction_adjusted():
    """THE short-sign guard. BTC SHORT: exit 100000, price now 95000 → price fell 5%. A short GAINS when
    price falls, so the counterfactual FLIPS sign: notional 0.05×100000=5000, if_held = 5000×+0.05 =
    +250 (NOT -250). Holding would have made more → WORSE."""
    btc = _by_asset(_result())["BTC"]
    assert btc["direction"] == "short"
    assert btc["price_since_exit_pct"] == -5.0        # raw price move is down
    assert btc["if_held_delta_usd"] == 250.0          # but the SHORT counterfactual is POSITIVE
    assert btc["exit_vs_hold"] == "held_higher"


def test_timing_summary_counts_beat_vs_worse():
    """PROCESS-framed aggregate: 1 exit beat holding (SOL), 2 were worse (ETH, BTC). Realized total 340;
    if_all_reclosed_now is the honest counterfactual aggregate (-60+100+250 = 290), CONTEXT not a
    projection."""
    ts = _result()["timing_summary"]
    assert ts["trade_count"] == 3
    assert ts["exits_ahead"] == 1
    assert ts["exits_held_higher"] == 2
    assert ts["exits_flat"] == 0
    assert ts["gross_realized_pnl_total"] == 340.0
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
    assert strat["gross_realized_pnl"] == 340.0


def test_open_book_unrealized_and_total_pnl():
    """WS1 total-ledger: kodiak holds an open SOL long (+$120 unrealized) → the strategy read carries
    unrealized_pnl + total_pnl (realized 340 + unrealized 120 = 460), and pnl_summary rolls up TOTAL with
    the current/closed realized split. A book riding an open winner is NOT judged on realized alone."""
    res = _result()
    strat = {s["label"]: s for s in res["strategies"]}["kodiak"]
    assert strat["gross_realized_pnl"] == 340.0
    assert strat["unrealized_pnl"] == 120.0
    assert strat["gross_total_pnl"] == 460.0
    assert strat["open_position_count"] == 1
    op = strat["open_positions"][0]
    assert op["asset"] == "SOL" and op["direction"] == "long"
    assert op["unrealized_pnl"] == 120.0 and op["return_on_equity_pct"] == 10.0
    ps = res["pnl_summary"]
    assert ps["gross_realized"] == 340.0 and ps["unrealized"] == 120.0 and ps["gross_total"] == 460.0
    assert ps["realized_by_book"] == {"current": 340.0, "closed": 0.0}


def test_open_book_unreadable_is_unknown_not_zero():
    """WS1 fail-open: when the clearinghouse can't be read, unrealized reads None (UNKNOWN) and total_pnl is
    None — NEVER a fabricated 0. Guards a realized-only headline from masquerading as total."""
    with open(FIXTURE) as f:
        raw = json.load(f)
    raw.pop("strategy_get_clearinghouse_state::0xkodiak00000000000000000000000000000kdk", None)
    old = os.environ.get("SENPI_STATE_DIR")
    os.environ["SENPI_STATE_DIR"] = REGISTRY_DIR
    try:
        res = review.run(review._FixtureClient(raw), window_days=WINDOW_DAYS, now_ms=NOW_MS)
    finally:
        if old is None:
            os.environ.pop("SENPI_STATE_DIR", None)
        else:
            os.environ["SENPI_STATE_DIR"] = old
    strat = {s["label"]: s for s in res["strategies"]}["kodiak"]
    assert strat["unrealized_pnl"] is None and strat["gross_total_pnl"] is None   # UNKNOWN, not 0
    assert res["pnl_summary"]["unrealized"] is None and res["pnl_summary"]["gross_total"] is None
    assert res["pnl_summary"]["gross_realized"] == 340.0                          # realized still known


def test_pnl_summary_partial_coverage_is_flagged_floor_not_complete():
    """WS1 degraded-read honesty (Sarvesh review): when SOME current wallets read and some are UNKNOWN,
    unrealized/total sum only the readable ones — a FLOOR, not a complete total. The engine MUST flag that
    (`unrealized_partial`) with the coverage counts, so the narrator says 'at least $X (N of M readable)'
    and never presents a partial sum as complete. Same principle as all-fail→None and undetermined≠all-clear,
    for the PARTIALLY-known case."""
    strat_reads = [
        {"realized_pnl": 100.0, "unrealized_pnl": 500.0},   # wallet A: readable, +$500 open
        {"realized_pnl": 50.0, "unrealized_pnl": None},     # wallet B: read FAILED → UNKNOWN
    ]
    ps = review._pnl_summary(150.0, strat_reads)
    assert ps["unrealized"] == 500.0                         # only the readable wallet — a FLOOR
    assert ps["gross_total"] == 650.0                              # 150 realized + 500 known unrealized (floor)
    assert ps["unrealized_partial"] is True                 # <-- flagged, not silent
    assert ps["unrealized_coverage"] == {"read": 1, "current_strategies": 2}


def test_pnl_summary_full_coverage_is_not_partial():
    """Control: every current wallet reads → unrealized_partial False (total is complete, not a floor)."""
    strat_reads = [{"realized_pnl": 100.0, "unrealized_pnl": 500.0},
                   {"realized_pnl": 50.0, "unrealized_pnl": -20.0}]
    ps = review._pnl_summary(150.0, strat_reads)
    assert ps["unrealized"] == 480.0 and ps["gross_total"] == 630.0
    assert ps["unrealized_partial"] is False


def test_pnl_summary_all_unreadable_is_none_not_partial():
    """Control: 0 wallets readable is the all-UNKNOWN case (unrealized/total None), NOT a partial floor."""
    strat_reads = [{"realized_pnl": 100.0, "unrealized_pnl": None},
                   {"realized_pnl": 50.0, "unrealized_pnl": None}]
    ps = review._pnl_summary(150.0, strat_reads)
    assert ps["unrealized"] is None and ps["gross_total"] is None
    assert ps["unrealized_partial"] is False                # None ≠ floor


def test_is_transient_classifies_retryable_vs_definitive():
    """Retry policy: transport blips + 5xx/429 retry; a definitive answer (4xx, {success:false}, JSON-RPC,
    tool error) does NOT — retrying a real 'no' just repeats it."""
    from mcp_client import MCPError
    assert review._is_transient(TimeoutError("timed out")) is True
    assert review._is_transient(ConnectionResetError()) is True
    assert review._is_transient(MCPError("strategy_get_clearinghouse_state HTTP 503")) is True
    assert review._is_transient(MCPError("x HTTP 429")) is True
    assert review._is_transient(MCPError("x HTTP 404")) is False
    assert review._is_transient(MCPError("tool failed: SERR001: no access")) is False
    assert review._is_transient(MCPError("JSON-RPC error -32601: method not found")) is False


def test_fetch_open_positions_retries_transient_then_succeeds():
    """A transient blip is retried; the second attempt's clean read is used — no fabricated None."""
    calls = {"n": 0}
    ok = {"main": {"assetPositions": [{"position": {"coin": "SOL", "szi": "1", "unrealizedPnl": "25.0"}}]}}

    class Flaky:
        def mcp_call(self, tool, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("blip")
            return ok
    meta = {}
    unreal, positions = review.fetch_open_positions(Flaky(), "0xabc", meta)
    assert calls["n"] == 2                          # retried once
    assert unreal == 25.0 and len(positions) == 1
    assert not meta.get("warnings")                 # succeeded → no failure warning


def test_fetch_open_positions_does_not_retry_definitive_failure():
    """A definitive {success:false} is NOT retried (it's a real answer) → one call, unrealized UNKNOWN."""
    from mcp_client import MCPError
    calls = {"n": 0}

    class Denied:
        def mcp_call(self, tool, **kw):
            calls["n"] += 1
            raise MCPError("tool failed: SERR001: no access")
    meta = {}
    unreal, positions = review.fetch_open_positions(Denied(), "0xabc", meta)
    assert calls["n"] == 1                           # NOT retried
    assert unreal is None and positions == []        # UNKNOWN, never a fabricated 0
    assert meta["warnings"]                          # a warning was recorded


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
    assert res["timing_summary"]["gross_realized_pnl_total"] == 340.0   # realized PnL is source-independent


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
    assert {t["asset"]: t["exit_vs_hold"] for t in res["trades"]}["SOL"] == "exit_ahead"
    # WS3: telemetry down AND zero exits attributed → the telemetry-down case → status UNDETERMINED, not all-clear
    ta = res["telemetry_availability"]
    assert ta["status"] == "undetermined"
    assert ta["streams_computed"] is False
    assert ta["exit_attribution"]["attributed"] == 0


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
    assert cs["gross_realized_pnl"] == 220.0                  # 200 + 20
    assert set(cs.keys()) == {"label", "wallet_short", "status", "trade_count",
                              "gross_realized_pnl", "fees", "net_realized_pnl", "fees_status", "fees_coverage"}
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
    # WS3: telemetry down → the leak/blocked/fee streams are UNDETERMINED (not computed), never 'none'.
    ta = res["telemetry_availability"]
    assert ta["streams_computed"] is False
    assert ta["exit_attribution"]["attributed"] == 1     # SOL attributed via the ratchet fallback
    assert ta["status"] == "partial"


# ──────────────────────────────────── telemetry quick-action aggregations (reuse the fetched events/trades)
# All six telemetry quick actions read from aggregations computed off the SAME fetched events + trades[] —
# no re-fetch. These guard the four engine-side ones (dsl_close_reason_mix, blocked_summary, leaks,
# execution_quality) using the events fixture already in play.


def test_dsl_close_reason_mix_buckets_terminals_and_premature():
    """'Shaken out too early / how are my exits firing?' — dsl_close_reason_mix tallies trades by
    exit_reason.terminal OVERALL + by asset_class + by strategy_label, and flags the premature cohort.
    With telemetry: SOL tier_breach, ETH max_retrace (premature), BTC UNKNOWN → 1 premature exit."""
    res = _result_with_telemetry()
    mix = res["dsl_close_reason_mix"]
    assert mix["overall"]["trade_count"] == 3
    bt = mix["overall"]["by_terminal"]
    assert bt.get("tier_breach") == 1 and bt.get("max_retrace") == 1 and bt.get("UNKNOWN") == 1
    # ETH max_retrace is a premature terminal → exactly one premature exit
    assert mix["overall"]["premature_exits"] == 1
    # every bucket carries strategy_label as its key → per-strategy 'why is X losing' filter
    assert "kodiak" in mix["by_strategy"]
    assert mix["by_strategy"]["kodiak"]["by_terminal"].get("tier_breach") == 1
    assert "crypto" in mix["by_asset_class"]
    prem = {s["asset"] for s in mix["premature_exit_samples"]}
    assert "ETH" in prem


def test_blocked_summary_tallies_reason_codes_by_strategy():
    """'What did my own limits block?' — blocked_summary tallies missed_signals by reason_code, overall +
    by strategy_label. HYPE/no_slots + AVAX/risk_gate_max_drawdown, both on kodiak."""
    res = _result_with_telemetry()
    bs = res["blocked_summary"]
    assert bs["total_blocked"] == 2
    assert bs["by_reason_code"].get("no_slots") == 1
    assert bs["by_reason_code"].get("risk_gate_max_drawdown") == 1
    assert bs["by_strategy"]["kodiak"].get("no_slots") == 1


def test_leaks_scan_failed_orders_protection_gaps_and_halts():
    """'Where am I leaking?' — leaks scans the SAME events for order.failed (reason), dsl.sl_sync_failed /
    dsl.handoff_failed (protection gaps), runtime.paused (risk halts). The fixture has one of each."""
    res = _result_with_telemetry()
    lk = res["leaks"]
    assert lk["order_failed"]["count"] == 1
    assert lk["order_failed"]["samples"][0]["reason"] == "insufficient_margin"
    assert lk["protection_gaps"]["count"] == 1
    assert lk["protection_gaps"]["samples"][0]["event"] == "dsl.sl_sync_failed"
    assert lk["risk_halts"]["count"] == 1
    assert lk["risk_halts"]["samples"][0]["reason"] == "max_drawdown_halt"
    assert res["meta"]["leak_counts"] == {"order_failed": 1, "protection_gaps": 1, "risk_halts": 1}


def test_execution_quality_maker_vs_taker_ratio():
    """'What am I paying in fees — maker vs taker?' — execution_quality tallies order.filled by
    senpi.order.execution_as_maker. Fixture: 2 maker (SOL entry, ETH exit) + 1 taker (SOL exit) → ratio
    2/3. The authoritative-fee ledger hook is documented, not called."""
    res = _result_with_telemetry()
    eq = res["execution_quality"]
    assert eq["maker_fills"] == 2
    assert eq["taker_fills"] == 1
    assert eq["maker_ratio"] == 0.6667
    assert "execution_get_closed_position_details" in eq["authoritative_fee_note"]


def test_aggregations_fail_open_when_telemetry_absent():
    """Fail-open: with NO event fixture (older build / closed ring), the leak + execution + blocked
    aggregations are empty/zeroed and dsl_close_reason_mix still tallies from the ratchet/UNKNOWN
    fallback — never a crash, valid structure throughout."""
    res = _result()                                  # no SENPI_EVENTS_FIXTURE
    assert res["leaks"]["order_failed"]["count"] == 0
    assert res["leaks"]["protection_gaps"]["count"] == 0
    assert res["leaks"]["risk_halts"]["count"] == 0
    assert res["execution_quality"]["maker_fills"] == 0
    assert res["execution_quality"]["maker_ratio"] is None
    assert res["blocked_summary"]["total_blocked"] == 0
    # dsl_close_reason_mix still runs off the ratchet/UNKNOWN fallback terminals
    mix = res["dsl_close_reason_mix"]
    assert mix["overall"]["trade_count"] == 3
    assert mix["overall"]["by_terminal"].get("SL_TRIGGERED") == 1   # SOL via ratchet fallback
    assert mix["overall"]["by_terminal"].get("UNKNOWN") == 2        # ETH (ACTIVE record) + BTC (no record)


# ──────────────────────────────────────────── resumable STEP subcommands (fast, standalone, state-shared)
# The engine also exposes the review as fast, resumable STEPS (timing → strategies → telemetry → market)
# over a shared state file, so a long review runs as short calls the agent narrates between (mirrors
# senpi-strategy-ops deploy.py) instead of one multi-minute call that trips the exec timeout. Each step is
# fail-open, idempotent, standalone (self-heals its prereqs), and prints only its own slice. A
# timing→strategies→telemetry sequence over a shared state must reproduce the SAME combined values as `all`.
import tempfile   # noqa: E402


def _fresh_client():
    with open(FIXTURE) as f:
        return review._FixtureClient(json.load(f))


def _with_env(fn, events=False):
    """Run fn() with the registry dir (and optionally the events fixture) pinned, all restored after."""
    old_sd = os.environ.get("SENPI_STATE_DIR")
    old_ev = os.environ.get("SENPI_EVENTS_FIXTURE")
    os.environ["SENPI_STATE_DIR"] = REGISTRY_DIR
    if events:
        os.environ["SENPI_EVENTS_FIXTURE"] = EVENTS_FIXTURE
    try:
        return fn()
    finally:
        for k, v in (("SENPI_STATE_DIR", old_sd), ("SENPI_EVENTS_FIXTURE", old_ev)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_step_timing_slice_standalone():
    """`timing` prints ONLY its slice — trades (exit_reason UNKNOWN, telemetry not run yet) + timing_summary
    + window — and matches `all`'s timing_summary. Runs standalone (fresh state)."""
    sp = os.path.join(tempfile.mkdtemp(), "s.json")
    out = _with_env(lambda: review.step_timing(_fresh_client(), window_days=WINDOW_DAYS,
                                               want_market=True, state_path=sp, now_ms=NOW_MS))
    assert set(out) == {"window", "trades", "timing_summary", "meta"}   # ONLY the timing slice
    assert out["timing_summary"]["trade_count"] == 3
    assert out["timing_summary"]["exits_ahead"] == 1
    # exit_reason is the placeholder here — telemetry/ratchet has not run on the fast path
    assert all(t["exit_reason"]["terminal"] == "UNKNOWN" for t in out["trades"])
    # but the timing counterfactual (prices only) is already correct
    assert {t["asset"]: t["exit_vs_hold"] for t in out["trades"]}["SOL"] == "exit_ahead"
    all_ts = _result()["timing_summary"]
    assert out["timing_summary"] == all_ts               # timing slice == all's timing_summary


def test_step_strategies_slice_reads_state():
    """`strategies` (after `timing`) prints the per-strategy read + closed rollup + dsl_close_reason_mix,
    matching `all`. Reads the persisted state — no re-fetch needed."""
    sp = os.path.join(tempfile.mkdtemp(), "s.json")

    def _seq():
        review.step_timing(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                           state_path=sp, now_ms=NOW_MS)
        return review.step_strategies(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                                      state_path=sp, now_ms=NOW_MS)
    out = _with_env(_seq)
    assert set(out) == {"strategies", "closed_strategies", "pnl_summary", "dsl_close_reason_mix", "meta"}
    strat = {s["label"]: s for s in out["strategies"]}["kodiak"]
    assert strat["dsl"]["hard_stop_roe_pct"] == -15.0    # mandate/DSL from the registry
    assert strat["gross_realized_pnl"] == 340.0
    assert out["meta"]["current_strategy_count"] == 1
    assert out["strategies"] == _result()["strategies"]  # == all's per-strategy read


def test_step_market_slice_standalone():
    """`market` prints ONLY book_vs_market, matching `all` (self-heals the held set from state/refetch)."""
    sp = os.path.join(tempfile.mkdtemp(), "s.json")
    out = _with_env(lambda: review.step_market(_fresh_client(), window_days=WINDOW_DAYS,
                                              want_market=True, state_path=sp, now_ms=NOW_MS))
    assert set(out) == {"book_vs_market", "meta"}
    assert out["book_vs_market"] == _result()["book_vs_market"]
    gap_assets = {g["asset"] for g in out["book_vs_market"]["gaps"]}
    assert "HYPE" in gap_assets


def test_step_sequence_reproduces_all_combined():
    """THE core step contract: timing → strategies → telemetry over ONE shared state file reproduces the
    SAME combined values as `all` — including the fully telemetry-enriched trades, missed_signals, leaks,
    execution_quality, and the telemetry-REFRESHED dsl_close_reason_mix + meta rollup."""
    sp = os.path.join(tempfile.mkdtemp(), "s.json")

    def _seq():
        t = review.step_timing(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                               state_path=sp, now_ms=NOW_MS)
        s = review.step_strategies(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                                   state_path=sp, now_ms=NOW_MS)
        te = review.step_telemetry(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                                   state_path=sp, now_ms=NOW_MS)
        m = review.step_market(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                               state_path=sp, now_ms=NOW_MS)
        return t, s, te, m
    t, s, te, m = _with_env(_seq, events=True)

    old_ev = os.environ.get("SENPI_EVENTS_FIXTURE")
    os.environ["SENPI_EVENTS_FIXTURE"] = EVENTS_FIXTURE
    try:
        allr = _result()                                 # `all` with telemetry present
    finally:
        if old_ev is None:
            os.environ.pop("SENPI_EVENTS_FIXTURE", None)
        else:
            os.environ["SENPI_EVENTS_FIXTURE"] = old_ev

    assert te["trades"] == allr["trades"]                # trades fully enriched, byte-for-byte
    assert t["timing_summary"] == allr["timing_summary"]
    assert s["strategies"] == allr["strategies"]
    assert s["closed_strategies"] == allr["closed_strategies"]
    assert te["missed_signals"] == allr["missed_signals"]
    assert te["blocked_summary"] == allr["blocked_summary"]
    assert te["leaks"] == allr["leaks"]
    assert te["execution_quality"] == allr["execution_quality"]
    assert te["dsl_close_reason_mix"] == allr["dsl_close_reason_mix"]   # telemetry-refreshed mix
    assert m["book_vs_market"] == allr["book_vs_market"]
    assert s["pnl_summary"] == allr["pnl_summary"]                      # total-ledger rollup parity
    assert te["telemetry_availability"] == allr["telemetry_availability"]   # undetermined-signal parity
    # meta sub-fields each step owns line up with all
    assert te["meta"]["exit_reason_source_counts"] == allr["meta"]["exit_reason_source_counts"]
    assert te["meta"]["telemetry_source"] == allr["meta"]["telemetry_source"]
    assert s["meta"]["current_strategy_count"] == allr["meta"]["current_strategy_count"]


def test_step_telemetry_enriches_exit_reason_over_state():
    """`telemetry` fills the exit_reason the fast `timing` path left UNKNOWN: after timing→telemetry, SOL is
    tier_breach (telemetry) and ETH max_retrace (telemetry) — matching the composed run."""
    sp = os.path.join(tempfile.mkdtemp(), "s.json")

    def _seq():
        review.step_timing(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                           state_path=sp, now_ms=NOW_MS)
        return review.step_telemetry(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                                     state_path=sp, now_ms=NOW_MS)
    out = _with_env(_seq, events=True)
    by = {t["asset"]: t for t in out["trades"]}
    assert by["SOL"]["exit_reason"]["terminal"] == "tier_breach"
    assert by["SOL"]["exit_reason"]["source"] == "telemetry"
    assert by["ETH"]["exit_reason"]["terminal"] == "max_retrace"
    assert out["meta"]["telemetry_source"] == "available"
    assert out["meta"]["exit_reason_source_counts"]["telemetry"] == 2


def test_steps_self_heal_when_state_absent():
    """Every step works STANDALONE (no prior step): with a fresh state file each recomputes its prereqs.
    `strategies` and `telemetry` self-heal the trade fetch and still produce correct slices."""
    d = tempfile.mkdtemp()

    def _solo():
        s = review.step_strategies(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                                   state_path=os.path.join(d, "a.json"), now_ms=NOW_MS)
        te = review.step_telemetry(_fresh_client(), window_days=WINDOW_DAYS, want_market=True,
                                   state_path=os.path.join(d, "b.json"), now_ms=NOW_MS)
        return s, te
    s, te = _with_env(_solo, events=True)
    assert {x["label"] for x in s["strategies"]} == {"kodiak"}
    assert s["strategies"][0]["gross_realized_pnl"] == 340.0
    # telemetry self-healed the fetch, then enriched
    assert {t["asset"]: t["exit_reason"]["terminal"] for t in te["trades"]}["SOL"] == "tier_breach"


def test_step_fail_open_on_corrupt_state():
    """A corrupt/garbage state file never crashes a step — it fails open to a recompute. Guards the
    'never crash on a missing/corrupt state file' contract."""
    sp = os.path.join(tempfile.mkdtemp(), "corrupt.json")
    with open(sp, "w") as f:
        f.write("{ this is not::: valid json ][")
    out = _with_env(lambda: review.step_strategies(_fresh_client(), window_days=WINDOW_DAYS,
                                                   want_market=True, state_path=sp, now_ms=NOW_MS))
    assert len(out["strategies"]) == 1                   # recomputed, not crashed
    assert out["strategies"][0]["label"] == "kodiak"


def test_step_flags_window_last_nomarket_apply():
    """--last / --no-market apply per step. --last 1 → one trade in the timing slice; --no-market → prices
    null + empty book_vs_market."""
    d = tempfile.mkdtemp()

    def _run():
        t = review.step_timing(_fresh_client(), window_days=WINDOW_DAYS, last_n=1, want_market=True,
                               state_path=os.path.join(d, "l.json"), now_ms=NOW_MS)
        sp2 = os.path.join(d, "nm.json")
        tn = review.step_timing(_fresh_client(), window_days=WINDOW_DAYS, want_market=False,
                                state_path=sp2, now_ms=NOW_MS)
        mn = review.step_market(_fresh_client(), window_days=WINDOW_DAYS, want_market=False,
                                state_path=sp2, now_ms=NOW_MS)
        return t, tn, mn
    t, tn, mn = _with_env(_run)
    assert t["timing_summary"]["trade_count"] == 1 and t["window"]["label"] == "last 1 closed trades"
    assert all(x["price_now"] is None for x in tn["trades"])
    assert mn["book_vs_market"]["top_movers"] == []


def test_default_state_path_uses_tempdir_and_window_key():
    """The default state path lives under tempfile.gettempdir()/senpi-improve-trades and is KEYED by the
    window (and --last) so distinct reviews don't clobber each other. Never touches $HOME."""
    p = review._default_state_path(7, None)
    assert p.startswith(tempfile.gettempdir())
    assert os.path.join("senpi-improve-trades", "state-7d.json") in p
    assert review._default_state_path(30.0, 20).endswith("state-30d-last20.json")


# ── on-chain fill recovery (closed-strategy trap: discovery clears on close, HL fills persist) ──
def test_reconstruct_closed_from_fills_fifo_direction_pnl():
    base = 1784000000000
    fills = [
        {"coin": "HYPE", "dir": "Open Long",   "sz": "2",  "px": "100", "closedPnl": "0",   "fee": "0.1",  "time": base + 1000, "oid": 1},
        {"coin": "HYPE", "dir": "Open Long",   "sz": "1",  "px": "110", "closedPnl": "0",   "fee": "0.05", "time": base + 2000, "oid": 2},
        {"coin": "HYPE", "dir": "Close Long",  "sz": "2",  "px": "120", "closedPnl": "40",  "fee": "0.12", "time": base + 3000, "oid": 3},
        {"coin": "HYPE", "dir": "Close Long",  "sz": "1",  "px": "130", "closedPnl": "20",  "fee": "0.06", "time": base + 4000, "oid": 4},
        {"coin": "SOL",  "dir": "Open Short",  "sz": "10", "px": "200", "closedPnl": "0",   "fee": "0.2",  "time": base + 1500, "oid": 5},
        {"coin": "SOL",  "dir": "Close Short", "sz": "10", "px": "190", "closedPnl": "100", "fee": "0.19", "time": base + 9000, "oid": 6},
    ]
    tr = review._reconstruct_closed_from_fills(fills, None, None, None)
    assert len(tr) == 3
    by = {(t["asset"], t["exit_px"]): t for t in tr}
    # FIFO: first HYPE close (2) matches the 2@100 lot; the second (1) matches the 1@110 lot
    assert by[("HYPE", 120.0)]["entry_px"] == 100.0 and by[("HYPE", 120.0)]["direction"] == "long"
    assert by[("HYPE", 130.0)]["entry_px"] == 110.0
    assert by[("SOL", 190.0)]["direction"] == "short" and by[("SOL", 190.0)]["entry_px"] == 200.0
    assert round(sum(t["realized_pnl"] for t in tr), 2) == 160.0    # HL closedPnl — GROSS, authoritative
    # round-trip fee = matched OPEN-side fee (pro-rated) + this CLOSE fill's fee (HL fee is builder-inclusive)
    assert by[("HYPE", 120.0)]["fee"] == 0.22 and by[("HYPE", 130.0)]["fee"] == 0.11
    assert by[("SOL", 190.0)]["fee"] == 0.39
    assert round(sum(t["fee"] for t in tr), 2) == 0.72              # total trading fees over the window
    assert all(t["source"] == "onchain_fills" for t in tr)
    assert tr[0]["asset"] == "SOL"                                  # newest close first
    assert [t["asset"] for t in review._reconstruct_closed_from_fills(fills, base + 5000, None, None)] == ["SOL"]
    assert review._reconstruct_closed_from_fills(fills, None, None, 1)[0]["asset"] == "SOL"
    assert review._reconstruct_closed_from_fills(None, None, None, None) == []


def test_closed_strategy_falls_back_to_onchain_fills():
    """The bug this fixes: CLOSED strategy → discovery empty → recover REAL trades on-chain; never report
    'no trades', and realized PnL comes from HL closedPnl (the $0-after-close is a withdrawal, not a loss)."""
    w = "0xclosed00000000000000000000000000000closed"
    fills = [
        {"coin": "SMSN", "dir": "Open Long",  "sz": "1", "px": "180", "closedPnl": "0",  "time": 1784000001000, "oid": 11},
        {"coin": "SMSN", "dir": "Close Long", "sz": "1", "px": "188", "closedPnl": "8",  "time": 1784000002000, "oid": 12},
        {"coin": "SOL",  "dir": "Open Long",  "sz": "1", "px": "78",  "closedPnl": "0",  "time": 1784000003000, "oid": 13},
        {"coin": "SOL",  "dir": "Close Long", "sz": "1", "px": "77",  "closedPnl": "-1", "time": 1784000004000, "oid": 14},
    ]
    client = review._FixtureClient({
        f"discovery_get_trader_history::{w.lower()}": {"closedPositions": []},   # Senpi index cleared on close
        f"hl::userFills::{w.lower()}": fills,                                     # HL retains fills by address
    })
    meta = {}
    trades = review.fetch_closed_trades(client, w, None, None, None, meta)
    assert len(trades) == 2                                     # real trades recovered, not "zero"
    assert round(sum(t["realized_pnl"] for t in trades), 2) == 7.0
    assert meta.get("closed_trade_source") == "onchain_fills"
    assert w in meta.get("onchain_recovered_wallets", [])


def test_no_trades_when_both_sources_empty():
    """Empty discovery AND empty on-chain fills ⇒ genuinely no trades (a brand-new book) → []."""
    w = "0xbrandnew0000000000000000000000000000new"
    client = review._FixtureClient({
        f"discovery_get_trader_history::{w.lower()}": {"closedPositions": []},
        f"hl::userFills::{w.lower()}": [],
    })
    meta = {}
    assert review.fetch_closed_trades(client, w, None, None, None, meta) == []
    assert "closed_trade_source" not in meta


# ── fee-honest PnL: discovery is GROSS; fees come from the fills ledger; net stays UNKNOWN when unread ──
def test_discovery_trades_get_fees_from_fills_and_net():
    """discovery owns the trade list but carries NO fee (its realized_pnl is GROSS). fetch_closed_trades
    stamps each row's round-trip fee from the fills ledger (matched by close order id), so net = gross − fees."""
    w = "0xfeed00000000000000000000000000000000feed"
    disco = {"closedPositions": [
        {"coin": "HYPE", "szi": "2",   "entryPx": "100", "exitPx": "120", "realizedPnl": "40",
         "closeTime": 1784000003000, "openTime": 1784000001000, "closedOrderId": 3},
        {"coin": "SOL",  "szi": "-10", "entryPx": "200", "exitPx": "190", "realizedPnl": "100",
         "closeTime": 1784000009000, "openTime": 1784000001500, "closedOrderId": 6},
    ]}
    fills = [
        {"coin": "HYPE", "dir": "Open Long",   "sz": "2",  "px": "100", "closedPnl": "0",   "fee": "0.10", "time": 1784000001000, "oid": 1},
        {"coin": "HYPE", "dir": "Close Long",  "sz": "2",  "px": "120", "closedPnl": "40",  "fee": "0.12", "time": 1784000003000, "oid": 3},
        {"coin": "SOL",  "dir": "Open Short",  "sz": "10", "px": "200", "closedPnl": "0",   "fee": "0.20", "time": 1784000001500, "oid": 5},
        {"coin": "SOL",  "dir": "Close Short", "sz": "10", "px": "190", "closedPnl": "100", "fee": "0.19", "time": 1784000009000, "oid": 6},
    ]
    client = review._FixtureClient({
        f"discovery_get_trader_history::{w.lower()}": disco,
        f"hl::userFills::{w.lower()}": fills,
    })
    trades = review.fetch_closed_trades(client, w, None, None, None, {})
    assert len(trades) == 2
    by = {t["asset"]: t for t in trades}
    assert by["HYPE"]["fee"] == 0.22 and by["SOL"]["fee"] == 0.39   # open + close, matched by oid
    for t in trades:
        t["strategy_wallet"] = w
    agg = review._pnl_by_wallet(trades)[w.lower()]
    assert agg["pnl"] == 140.0 and round(agg["fees"], 2) == 0.61 and agg["fees_known"] == 2


def test_fees_undetermined_when_fills_read_fails():
    """discovery has closed trades but the fills ledger is UNREADABLE → each fee is None, and the record marks
    fees/net UNDETERMINED (never a fake $0 net that would read to the user as booked profit)."""
    w = "0xnofills0000000000000000000000000000nofil"
    disco = {"closedPositions": [
        {"coin": "HYPE", "szi": "2", "entryPx": "100", "exitPx": "120", "realizedPnl": "40",
         "closeTime": 1784000003000, "closedOrderId": 3},
    ]}
    client = review._FixtureClient({f"discovery_get_trader_history::{w.lower()}": disco})   # NO userFills → read fails
    trades = review.fetch_closed_trades(client, w, None, None, None, {})
    assert len(trades) == 1 and trades[0]["fee"] is None
    for t in trades:
        t["strategy_wallet"] = w
    strategies = [{"wallet": w, "status": "ACTIVE", "label": "x", "mandate": None}]
    r = review._strategy_reads(trades, strategies, {w.lower(): {"unrealized_pnl": 5.0, "positions": []}})[0]
    assert r["gross_realized_pnl"] == 40.0                          # gross still known
    assert r["fees"] is None and r["net_realized_pnl"] is None and r["fees_status"] == "undetermined"
    assert r["net_total_pnl"] is None and r["gross_total_pnl"] == 45.0   # net UNKNOWN; gross total = 40 + 5


def test_reconstruct_fees_are_signed_maker_rebates_reduce_cost():
    """HL `fee` is SIGNED — a maker rebate is NEGATIVE (the wallet was paid). The round-trip fee sums it
    as-is; abs() would flip a rebate into a cost and understate net by 2x (Ignas' review of #538)."""
    base = 1784000000000
    fills = [
        {"coin": "SOL", "dir": "Open Long",  "sz": "1", "px": "100", "closedPnl": "0",  "fee": "-0.5", "time": base + 1, "oid": 1},  # maker rebate
        {"coin": "SOL", "dir": "Close Long", "sz": "1", "px": "110", "closedPnl": "10", "fee": "0.2",  "time": base + 2, "oid": 2},  # taker cost
    ]
    tr = review._reconstruct_closed_from_fills(fills, None, None, None)
    assert len(tr) == 1 and tr[0]["fee"] == -0.3        # −0.5 rebate + 0.2 cost — SIGNED, not abs()
    tr[0]["strategy_wallet"] = "0xw"
    agg = review._pnl_by_wallet(tr)["0xw"]
    assert agg["fees"] == -0.3 and round(agg["pnl"] - agg["fees"], 2) == 10.3   # the rebate ADDS to net


def test_fees_coverage_reports_resolved_over_total():
    """When some trades' fees resolve and one doesn't, the record carries fees_coverage {resolved, total}
    so a consumer can say 'net for R of T' instead of only a flat undetermined (Ignas' review of #538)."""
    w = "0xcov00000000000000000000000000000000cov0"
    trades = [
        {"asset": "A", "realized_pnl": 10.0, "fee": 0.1,  "strategy_wallet": w},
        {"asset": "B", "realized_pnl": 5.0,  "fee": None, "strategy_wallet": w},   # one unresolved
    ]
    strategies = [{"wallet": w, "status": "ACTIVE", "label": "x", "mandate": None}]
    r = review._strategy_reads(trades, strategies, {w.lower(): {"unrealized_pnl": 0.0, "positions": []}})[0]
    assert r["fees_status"] == "undetermined" and r["net_realized_pnl"] is None
    assert r["fees_coverage"] == {"resolved": 1, "total": 2}


def test_fetch_window_fills_pages_userfillsbytime_when_windowed():
    """A windowed review pages `userFillsByTime` (so coverage matches the window, not the recent-2000 cap);
    no window → the recent `userFills` slice. Both are served by the fixture under their own hl:: keys."""
    w = "0xwin00000000000000000000000000000000win0"
    byt = [{"coin": "X", "dir": "Open Long", "sz": "1", "px": "1", "closedPnl": "0", "fee": "0.1", "time": 5, "oid": 1}]
    recent = [{"coin": "Y", "dir": "Open Long", "sz": "1", "px": "1", "closedPnl": "0", "fee": "0.2", "time": 5, "oid": 2}]
    client = review._FixtureClient({
        f"hl::userFillsByTime::{w.lower()}": byt,
        f"hl::userFills::{w.lower()}": recent,
    })
    assert review._fetch_window_fills(client, w, 100, 200, {}) == byt        # windowed → userFillsByTime
    assert review._fetch_window_fills(client, w, None, None, {}) == recent   # no window → userFills


# ────────────────────────────── scope + fast-fail (the "telemetry unavailable" live-run fix) ──
# The failure: "improve my last 10 trades" fanned the event-log shell-out across a churned book of dozens of
# CLOSED runtimes. A closed runtime's on-disk ring is torn down, so each `openclaw senpi events` hung to its
# timeout; N hangs → the whole review reported "telemetry unavailable" (every exit_reason UNKNOWN) — even for
# the live strategies whose ring WAS readable. Fix: only current (active/paused) strategies are ever event-
# fetched, last_n is a GLOBAL bound, and repeated timeouts trip a breaker.
def _mixed_result_with_telemetry(last_n=None):
    """Mixed current/closed fixture WITH the event-log fixture pinned (both env vars restored after)."""
    old_ev = os.environ.get("SENPI_EVENTS_FIXTURE")
    os.environ["SENPI_EVENTS_FIXTURE"] = EVENTS_FIXTURE
    try:
        return _mixed_result(last_n=last_n)
    finally:
        if old_ev is None:
            os.environ.pop("SENPI_EVENTS_FIXTURE", None)
        else:
            os.environ["SENPI_EVENTS_FIXTURE"] = old_ev


def test_closed_strategy_is_never_event_fetched():
    """A CLOSED strategy's ring is gone — the engine must NEVER shell `openclaw senpi events` at it (that
    hang, fanned across a churned book, is what made a full review 'time out'). The mixed fixture has 3
    strategies (kodiak ACTIVE, grizzly PAUSED, kodiak CLOSED); only the 2 CURRENT ones are ever event-fetched
    — the closed redeployment is skipped, and its trades stay reconstructed (never telemetry-sourced)."""
    calls = []
    real = review._fetch_events
    def _spy(runtime_id, since_ms, meta):
        calls.append(runtime_id)
        return real(runtime_id, since_ms, meta)
    review._fetch_events = _spy
    try:
        res = _mixed_result_with_telemetry()
    finally:
        review._fetch_events = real
    assert len(calls) == 2                                   # only the 2 current strategies — never the closed one
    by = {t["asset"]: t for t in res["trades"]}
    assert by["BTC"]["source"] != "telemetry"                # closed-strategy trades: reconstructed, not enriched
    assert by["DOGE"]["source"] != "telemetry"


def test_last_n_is_global_not_per_strategy():
    """--last N is a GLOBAL bound across the whole book, not N-per-strategy. The mixed fixture has 4 trades
    across 2 wallets (current + closed); last_n=2 → the 2 most-recent overall (pre-fix: up to 2 per wallet =
    4). The two returned are exactly the two newest by close_time."""
    full = _mixed_result()
    assert full["meta"]["trade_count"] == 4
    top2 = [t["asset"] for t in full["trades"][:2]]         # the engine returns trades newest-first
    capped = _mixed_result(last_n=2)
    assert capped["meta"]["trade_count"] == 2                # GLOBAL cap — not 2 per wallet
    assert [t["asset"] for t in capped["trades"]] == top2


def test_event_timeout_fails_open_without_a_breaker():
    """A slow event fetch fails open for THAT strategy only (returns [] + a warning) — and there is NO
    count-based circuit breaker: the parallel fan-out gives each worker a private meta, so a cross-strategy
    timeout counter could never accumulate mid-fan-out (the real bound is the current-book-only guard, which
    never probes closed strategies). Guards against reintroducing the dead breaker Duncan flagged."""
    import subprocess as _sp
    old_ev = os.environ.pop("SENPI_EVENTS_FIXTURE", None)   # force the subprocess path, not the fixture
    def _raise_timeout(*a, **k):
        raise _sp.TimeoutExpired(cmd="openclaw", timeout=review.EVENTS_CALL_TIMEOUT_S)
    real_run = review.subprocess.run
    review.subprocess.run = _raise_timeout
    try:
        meta = {}
        assert review._fetch_events("rt-1", NOW_MS, meta) == []            # fail-open for this strategy
        assert any("timed out" in w for w in meta.get("warnings", []))     # a warning was recorded
        assert "_telemetry_dead" not in meta                               # ONE timeout is not terminal
        assert not hasattr(review, "EVENTS_TIMEOUT_BUDGET")                # the dead breaker constant is gone
    finally:
        review.subprocess.run = real_run
        if old_ev is not None:
            os.environ["SENPI_EVENTS_FIXTURE"] = old_ev


# ─────────────────────── stdout context-cost slimming (the samurai-pro timeout fix) ───────────────
# review.py's stdout IS the model's context next turn. A 146-trade account dumped the full array (~29k
# tokens) into context → prefill blew the delivery timeout (samurai-pro >60s tail). The fix: stdout
# carries a top-N OUTLIER sample + counts; aggregates stay complete; the on-disk state keeps the full
# array; `--full` restores it.
def _big_result(n_trades=146, n_missed=40):
    def mk(i):
        return {"asset": f"A{i % 40}", "direction": "long" if i % 2 else "short",
                "realized_pnl": round((i - 73) * 3.14, 2), "if_held_delta_usd": round((73 - i) * 5.5, 2),
                "close_time": 1782500000000 + i * 60000, "exit_vs_hold": "held_higher",
                "price_since_exit_pct": round((i - 73) * 0.3, 2), "strategy_label": "lion",
                "strategy_wallet": f"0xwallet{i:036x}", "margin_used": 250.0, "leverage": 5,
                "mandate": "Long winners, short laggards — a deliberately verbose per-trade field.",
                "exit_reason": {"terminal": "SL_TRIGGERED", "tier_reached": 2, "high_water_roe": 41.0}}
    return {"trades": [mk(i) for i in range(n_trades)],
            "timing_summary": {"trade_count": n_trades, "exits_ahead": 124},
            "pnl_summary": {"gross_total": 468.44, "net_total": 467.5, "gross_realized": -239.44},
            "missed_signals": [{"asset": f"M{i}", "reason_code": "no_slots"} for i in range(n_missed)],
            "meta": {"trade_count": n_trades}}


def test_stdout_slim_samples_trades_and_keeps_aggregates():
    """STDOUT carries a top-N outlier SAMPLE (not the 146-row array that blew the delivery timeout); every
    aggregate stays intact; the payload shrinks >85%."""
    big = _big_result()
    slim = review._slim_for_context(big, full=False)
    assert len(slim["trades"]) == review.STDOUT_TRADES_SAMPLE                  # sampled, not all 146
    assert slim["trades_sample"]["total"] == 146                              # true count preserved
    assert slim["timing_summary"]["exits_ahead"] == 124                       # aggregate untouched
    assert slim["pnl_summary"]["gross_total"] == 468.44
    assert len(slim["missed_signals"]) == review.STDOUT_MISSED_SAMPLE
    keys = set(slim["trades"][0].keys())                                      # per-trade fields trimmed
    assert "realized_pnl" in keys and "exit_reason" in keys
    assert not ({"strategy_wallet", "mandate", "margin_used"} & keys)         # verbose internals dropped
    assert len(json.dumps(slim)) < 0.15 * len(json.dumps(big))               # the whole point


def test_stdout_slim_samples_the_outliers():
    """The sample is the biggest-realized + biggest-hold-to-now moves — the trades a coach actually cites."""
    big = _big_result()
    slim = review._slim_for_context(big, full=False)
    biggest = max(big["trades"], key=lambda t: abs(t["realized_pnl"]))
    assert any(t["realized_pnl"] == biggest["realized_pnl"] for t in slim["trades"])


def test_stdout_slim_full_flag_restores_everything():
    big = _big_result()
    assert review._slim_for_context(big, full=True) is big                    # unchanged, complete


def test_stdout_slim_small_book_not_falsely_sampled():
    """A small book (<= sample size) is field-trimmed but NOT flagged as a 'sample' — nothing hidden, so
    the narrator must not imply trades were dropped."""
    small = {"trades": [{"asset": "BTC", "realized_pnl": 10.0, "strategy_wallet": "0xabc"}],
             "meta": {"trade_count": 1}}
    slim = review._slim_for_context(small, full=False)
    assert len(slim["trades"]) == 1
    assert "strategy_wallet" not in slim["trades"][0]                         # still field-trimmed
    assert "trades_sample" not in slim                                        # NOT flagged as truncated


# ──────────────────────────────────────────── the strategy LABEL (strategyName, MCP #190)
def test_label_prefers_the_strategys_own_name_over_the_package_id():
    """`strategyName` is the strategy's OWN name; `tradingStrategyName` is the PACKAGE id the backend
    reads off `strategyMetadata.skillName`. Labelling by the package id is what renders the long and
    short sleeves of one strategy as an ambiguous "cougar ×2"."""
    assert review._strategy_label(
        {"strategyName": "cougar-long", "tradingStrategyName": "cougar"}) == "cougar-long"


def test_a_present_but_empty_label_falls_through_to_the_package_id():
    """`strategyName` is nullable BY MECHANISM — `strategy_create` has no name input and it is optional
    on `strategy_create_custom_strategy` — and the MCP now selects it, so the key is present on EVERY
    row. Silence at that leg must not answer for the legs behind it."""
    for empty in (None, "", "  "):
        assert review._strategy_label(
            {"strategyName": empty, "tradingStrategyName": "kodiak"}) == "kodiak", repr(empty)
    assert review._strategy_label({}) == "strategy"


COUGAR_LONG = "0xCOUGARLONG000000000000000000000000000lng"
COUGAR_SHORT = "0xCOUGARSHORT00000000000000000000000000sht"

_SLEEVE_YAML = """name: {name}
group: cougar
description: >
  {desc}
exit:
  dsl_preset:
    phase1: {{hard_stop_roe_pct: -14}}
    phase2: {{arm_at_roe_pct: 8, tiers: [{{trigger_pct: 12, lock_hw_pct: 6}}]}}
"""


def _two_sleeve_fixture():
    """ONE package deployed as TWO sleeves, as the backend really returns it: a distinct `strategyName`
    per sleeve, the SAME `tradingStrategyName` (it is the package id), the `strategyMetadata.skillName`
    stamp it is derived from, and a DIFFERENT mandate per sleeve. Both sleeves close trades."""
    with open(FIXTURE) as f:
        base = json.load(f)
    hist = base["discovery_get_trader_history::0xkodiak00000000000000000000000000000kdk"]["closedPositions"]
    ch = base["strategy_get_clearinghouse_state::0xkodiak00000000000000000000000000000kdk"]
    fx = {k: v for k, v in base.items() if k.startswith(("market_get_asset_data::", "leaderboard_"))}
    fx["strategy_list"] = {"strategies": [
        {"strategyName": "cougar-long", "tradingStrategyName": "cougar",
         "strategyMetadata": {"skillName": "cougar", "skillVersion": "1.0.0"},
         "id": "strat-cougar-long", "strategyWalletAddress": COUGAR_LONG, "status": "ACTIVE"},
        {"strategyName": "cougar-short", "tradingStrategyName": "cougar",
         "strategyMetadata": {"skillName": "cougar", "skillVersion": "1.0.0"},
         "id": "strat-cougar-short", "strategyWalletAddress": COUGAR_SHORT, "status": "ACTIVE"}]}
    # long sleeve closes SOL+ETH, short sleeve closes BTC → both buckets non-empty, different terminals
    fx[f"discovery_get_trader_history::{COUGAR_LONG.lower()}"] = {"closedPositions": hist[:2]}
    fx[f"discovery_get_trader_history::{COUGAR_SHORT.lower()}"] = {"closedPositions": hist[2:]}
    fx[f"ratchet_stop_list::{COUGAR_LONG.lower()}"] = {"configs": [
        {"asset": "SOL", "status": "SL_TRIGGERED", "currentTierIndex": 2, "highWaterRoe": 41.0}]}
    fx[f"ratchet_stop_list::{COUGAR_SHORT.lower()}"] = {"configs": [
        {"asset": "BTC", "status": "SL_TRIGGERED", "currentTierIndex": 0, "highWaterRoe": 5.0}]}
    fx[f"strategy_get_clearinghouse_state::{COUGAR_LONG.lower()}"] = ch
    fx[f"strategy_get_clearinghouse_state::{COUGAR_SHORT.lower()}"] = ch
    return fx


def _two_sleeve_registry():
    """A registry dir where BOTH sleeve wallets carry `group: cougar` — the runtime.yaml's own key, the
    authoritative "these are one strategy" field."""
    import tempfile
    d = tempfile.mkdtemp()
    doc = {"version": 1, "runtimes": [
        {"id": "cougar-long", "wallet": COUGAR_LONG,
         "runtimeYamlContent": _SLEEVE_YAML.format(name="cougar-long", desc="Long book of the pair.")},
        {"id": "cougar-short", "wallet": COUGAR_SHORT,
         "runtimeYamlContent": _SLEEVE_YAML.format(name="cougar-short", desc="Short book of the pair.")}]}
    with open(os.path.join(d, "installed_runtimes.json"), "w") as f:
        json.dump(doc, f)
    return d


def _run_two_sleeves():
    old = os.environ.get("SENPI_STATE_DIR")
    os.environ["SENPI_STATE_DIR"] = _two_sleeve_registry()
    try:
        return review.run(review._FixtureClient(_two_sleeve_fixture()), window_days=WINDOW_DAYS,
                          want_market=False, now_ms=NOW_MS)
    finally:
        if old is None:
            os.environ.pop("SENPI_STATE_DIR", None)
        else:
            os.environ["SENPI_STATE_DIR"] = old


def test_sleeves_of_one_package_bucket_apart_in_by_strategy():
    """THE behavioural change. `dsl_close_reason_mix.by_strategy` is keyed by label and routes a fix at a
    DSL preset — and the DSL ladder is PER INSTANCE, so a merged long+short bucket pointed one "fix" at
    two different ladders. Labelled by the package id both sleeves landed in a single `cougar` bucket."""
    res = _run_two_sleeves()
    mix = res["dsl_close_reason_mix"]
    assert set(mix["by_strategy"]) == {"cougar-long", "cougar-short"}
    assert mix["by_strategy"]["cougar-long"]["trade_count"] == 2
    assert mix["by_strategy"]["cougar-short"]["trade_count"] == 1
    assert mix["overall"]["trade_count"] == 3          # the split does not lose or duplicate a trade
    assert {t["strategy_label"] for t in res["trades"]} == {"cougar-long", "cougar-short"}


def test_sleeve_rows_carry_the_attribution_that_proves_they_are_one_strategy():
    """Naming the sleeves apart REMOVED the accidental detector behind "never merge two ACTIVE same-label
    wallets": they no longer share a label, and they never shared a mandate. `group` (the runtime.yaml key)
    and `skill_name` (the attribution stamp) are the only fields that say these two rows are one book —
    without them on the row the rule is unactionable and closing the losing sleeve looks reasonable."""
    res = _run_two_sleeves()
    rows = {s["label"]: s for s in res["strategies"]}
    assert set(rows) == {"cougar-long", "cougar-short"}
    assert [rows[k]["group"] for k in rows] == ["cougar", "cougar"]
    assert [rows[k]["skill_name"] for k in rows] == ["cougar", "cougar"]
    # nothing ELSE on the row links them — this is why the two fields have to be there
    assert rows["cougar-long"]["mandate"] != rows["cougar-short"]["mandate"]
    assert rows["cougar-long"]["wallet"] != rows["cougar-short"]["wallet"]


def test_the_shared_group_survives_an_unreadable_registry():
    """`group` comes from the runtime.yaml, so it is None exactly when the registry is unreadable — the
    degradation the rule already documents. `skill_name` is read from the strategy record itself, so it
    still proves the pairing on a host with no registry."""
    old = os.environ.get("SENPI_STATE_DIR")
    os.environ["SENPI_STATE_DIR"] = os.path.join(HERE, "fixtures", "no-such-registry")
    try:
        res = review.run(review._FixtureClient(_two_sleeve_fixture()), window_days=WINDOW_DAYS,
                         want_market=False, now_ms=NOW_MS)
    finally:
        if old is None:
            os.environ.pop("SENPI_STATE_DIR", None)
        else:
            os.environ["SENPI_STATE_DIR"] = old
    rows = {s["label"]: s for s in res["strategies"]}
    assert all(r["group"] is None for r in rows.values())          # registry gone → no group
    assert all(r["skill_name"] == "cougar" for r in rows.values())  # attribution still pairs them


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
