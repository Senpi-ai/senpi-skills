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


def test_open_book_unrealized_and_total_pnl():
    """WS1 total-ledger: kodiak holds an open SOL long (+$120 unrealized) → the strategy read carries
    unrealized_pnl + total_pnl (realized 340 + unrealized 120 = 460), and pnl_summary rolls up TOTAL with
    the current/closed realized split. A book riding an open winner is NOT judged on realized alone."""
    res = _result()
    strat = {s["label"]: s for s in res["strategies"]}["kodiak"]
    assert strat["realized_pnl"] == 340.0
    assert strat["unrealized_pnl"] == 120.0
    assert strat["total_pnl"] == 460.0
    assert strat["open_position_count"] == 1
    op = strat["open_positions"][0]
    assert op["asset"] == "SOL" and op["direction"] == "long"
    assert op["unrealized_pnl"] == 120.0 and op["return_on_equity_pct"] == 10.0
    ps = res["pnl_summary"]
    assert ps["realized"] == 340.0 and ps["unrealized"] == 120.0 and ps["total"] == 460.0
    assert ps["realized_by_book"] == {"current": 340.0, "closed": 0.0}


def test_open_book_unreadable_is_unknown_not_zero():
    """WS1 fail-open: when the clearinghouse can't be read, unrealized reads None (UNKNOWN) and total_pnl is
    None — NEVER a fabricated 0. Guards a realized-only headline from masquerading as total."""
    with open(FIXTURE) as f:
        raw = json.load(f)
    raw.pop("discovery_get_trader_state", None)
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
    assert strat["unrealized_pnl"] is None and strat["total_pnl"] is None   # UNKNOWN, not 0
    assert res["pnl_summary"]["unrealized"] is None and res["pnl_summary"]["total"] is None
    assert res["pnl_summary"]["realized"] == 340.0                          # realized still known


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
    assert ps["total"] == 650.0                              # 150 realized + 500 known unrealized (floor)
    assert ps["unrealized_partial"] is True                 # <-- flagged, not silent
    assert ps["unrealized_coverage"] == {"read": 1, "current_strategies": 2}


def test_pnl_summary_full_coverage_is_not_partial():
    """Control: every current wallet reads → unrealized_partial False (total is complete, not a floor)."""
    strat_reads = [{"realized_pnl": 100.0, "unrealized_pnl": 500.0},
                   {"realized_pnl": 50.0, "unrealized_pnl": -20.0}]
    ps = review._pnl_summary(150.0, strat_reads)
    assert ps["unrealized"] == 480.0 and ps["total"] == 630.0
    assert ps["unrealized_partial"] is False


def test_pnl_summary_all_unreadable_is_none_not_partial():
    """Control: 0 wallets readable is the all-UNKNOWN case (unrealized/total None), NOT a partial floor."""
    strat_reads = [{"realized_pnl": 100.0, "unrealized_pnl": None},
                   {"realized_pnl": 50.0, "unrealized_pnl": None}]
    ps = review._pnl_summary(150.0, strat_reads)
    assert ps["unrealized"] is None and ps["total"] is None
    assert ps["unrealized_partial"] is False                # None ≠ floor


def test_open_book_is_one_batched_call_and_merges_xyz():
    """The open book is ONE discovery_get_trader_state call for all current wallets, and an `xyz:` (HIP-3)
    position counts the same as a main-perp one — that tool returns both in a single openPositions list."""
    calls = []

    class Batched:
        def mcp_call(self, tool, timeout=12, **kw):
            calls.append((tool, tuple(kw.get("trader_addresses") or ())))
            return {"traders": [
                {"address": "0xaaa", "openPositions": [
                    {"coin": "xyz:GOLD", "coinDisplayName": "GOLD", "szi": "1", "unrealizedPnl": "4.0"},
                    {"coin": "BTC", "coinDisplayName": "BTC", "szi": "-1", "unrealizedPnl": "-1.5"}]},
                {"address": "0xbbb", "openPositions": []}]}

    strategies = [{"wallet": "0xAAA", "status": "ACTIVE"}, {"wallet": "0xBBB", "status": "ACTIVE"},
                  {"wallet": "0xAAA", "status": "ACTIVE"},            # duplicate wallet — deduped
                  {"wallet": "0xCCC", "status": "CLOSED"}]            # closed — has no open book
    meta = {}
    book = review.fetch_open_book(Batched(), strategies, meta)
    assert calls == [("discovery_get_trader_state", ("0xAAA", "0xBBB"))]   # ONE call, current + deduped
    assert book["0xaaa"]["unrealized_pnl"] == 2.5                          # 4.0 + (-1.5), xyz included
    assert [p["asset"] for p in book["0xaaa"]["positions"]] == ["GOLD", "BTC"]
    assert book["0xaaa"]["positions"][1]["direction"] == "short"
    assert book["0xbbb"]["unrealized_pnl"] == 0.0                          # read fine, genuinely flat
    assert "0xccc" not in book


def test_open_book_failure_leaves_wallet_absent_not_zero():
    """A failed state read must leave the wallet ABSENT (UNKNOWN downstream), never a fabricated 0."""
    class Down:
        def mcp_call(self, tool, timeout=12, **kw):
            raise RuntimeError("upstream down")
    meta = {}
    assert review.fetch_open_book(Down(), [{"wallet": "0xAAA", "status": "ACTIVE"}], meta) == {}
    assert meta["warnings"]


def test_open_book_batches_in_chunks_of_fifty():
    """discovery_get_trader_state takes at most 50 addresses, so 120 wallets is 3 calls — never 120."""
    sizes = []

    class Counting:
        def mcp_call(self, tool, timeout=12, **kw):
            sizes.append(len(kw.get("trader_addresses") or ()))
            return {"traders": []}

    strategies = [{"wallet": f"0x{i:040x}", "status": "ACTIVE"} for i in range(120)]
    review.fetch_open_book(Counting(), strategies, {})
    assert sizes == [50, 50, 20]


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
    assert strat["realized_pnl"] == 340.0
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
    assert s["strategies"][0]["realized_pnl"] == 340.0
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


# ── closed trades: realized PnL is read from discovery, never reconstructed ──
def test_closed_strategy_history_comes_from_discovery():
    """A CLOSED strategy still has history — it is keyed by wallet address, which outlives the strategy
    record. No fill reconstruction, no on-chain fallback: the rows are read and reported as-is."""
    w = "0xclosed00000000000000000000000000000closed"
    client = review._FixtureClient({f"discovery_get_trader_history::{w.lower()}": {"closedPositions": [
        {"closedOrderId": "a", "coin": "SMSN", "szi": "1", "entryPx": "180", "exitPx": "188",
         "realizedPnl": "8.00", "totalFees": "0.40", "openTime": 1784000001000, "closeTime": 1784000002000},
        {"closedOrderId": "b", "coin": "SOL", "szi": "1", "entryPx": "78", "exitPx": "77",
         "realizedPnl": "-1.00", "totalFees": "0.10", "openTime": 1784000003000, "closeTime": 1784000004000},
    ]}})
    trades = review.fetch_closed_trades(client, w, None, None, None, {})
    assert len(trades) == 2
    assert round(sum(t["realized_pnl"] for t in trades), 2) == 7.0     # exactly what discovery reported
    assert all(t["source"] == "discovery" for t in trades)
    assert trades[0]["asset"] == "SOL"                                 # newest close first


def test_realized_pnl_is_never_reduced_by_fees():
    """The bug this fixes: fees are a SEPARATE cost that realizedPnl already excludes. `realized_pnl` must
    stay exactly what discovery reported, with `fees` carried alongside for the fee question only."""
    w = "0xfee0000000000000000000000000000000000fee"
    client = review._FixtureClient({f"discovery_get_trader_history::{w.lower()}": {"closedPositions": [
        {"closedOrderId": "a", "coin": "BTC", "szi": "1", "entryPx": "100", "exitPx": "110",
         "realizedPnl": "19.00", "totalFees": "16.99", "closeTime": 1784000002000}]}})
    t = review.fetch_closed_trades(client, w, None, None, None, {})[0]
    assert t["realized_pnl"] == 19.0                     # NOT 19.00 - 16.99
    assert t["fees"] == 16.99
    assert review._timing_summary([t])["realized_pnl_total"] == 19.0
    assert review._timing_summary([t])["fees_total"] == 16.99
    assert review._pnl_summary(19.0, [], 16.99)["realized"] == 19.0


def test_zero_open_time_is_missing_not_epoch():
    """discovery sends openTime 0 for "no open time". 0 is a missing-value sentinel, not 1970."""
    w = "0xzero000000000000000000000000000000000zer"
    client = review._FixtureClient({f"discovery_get_trader_history::{w.lower()}": {"closedPositions": [
        {"closedOrderId": "a", "coin": "BTC", "szi": "1", "realizedPnl": "1.00",
         "openTime": 0, "closeTime": 1784000002000}]}})
    assert review.fetch_closed_trades(client, w, None, None, None, {})[0]["open_time"] is None


def test_no_trades_when_discovery_is_empty():
    """An empty discovery result is a real answer: a book that has not closed anything → []."""
    w = "0xbrandnew0000000000000000000000000000new"
    client = review._FixtureClient({f"discovery_get_trader_history::{w.lower()}": {"closedPositions": []}})
    assert review.fetch_closed_trades(client, w, None, None, None, {}) == []


def test_account_history_is_periods_only_no_raw_series():
    """account_get_historical_info is the FIRST read. Emit the per-period totals only — the raw
    accountValueHistory series are snapshots, not trades, and must not reach the narrator."""
    client = review._FixtureClient({"account_get_historical_info": {"historical_info": {
        "day": {"totalPnlByValue": "12.00", "totalPnlByPercentage": "1.2",
                "accountValueHistory": [{"timestamp": "1", "value": "900"},
                                        {"timestamp": "2", "value": "1012.00"}]},
        "allTime": {"totalPnlByValue": "-460.00", "totalPnlByPercentage": "-46.0",
                    "accountValueHistory": [{"timestamp": "2", "value": "1012.00"}]}}}})
    acc = review.fetch_account_history(client, {})
    assert acc["day"] == {"pnl": 12.0, "pnl_pct": 1.2, "account_value": 1012.0}
    assert acc["allTime"]["pnl"] == -460.0
    assert "accountValueHistory" not in json.dumps(acc)
    assert "pnlHistory" not in json.dumps(acc)


def test_account_history_failure_is_unknown_not_zero():
    class Down:
        def mcp_call(self, tool, timeout=12, **kw):
            raise RuntimeError("upstream down")
    meta = {}
    assert review.fetch_account_history(Down(), meta) is None      # UNKNOWN, never 0.0
    assert meta["warnings"]


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
            "pnl_summary": {"total": 468.44, "realized": -239.44},
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
    assert slim["pnl_summary"]["total"] == 468.44
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
    pos = base["discovery_get_trader_state"]["traders"][0]["openPositions"]
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
    fx["discovery_get_trader_state"] = {"traders": [
        {"address": COUGAR_LONG.lower(), "openPositions": pos, "openOrders": []},
        {"address": COUGAR_SHORT.lower(), "openPositions": pos, "openOrders": []}]}
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
