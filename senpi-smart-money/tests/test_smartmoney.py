#!/usr/bin/env python3
"""Offline engine test — runs smartmoney.run() + the step subcommands against a recorded MCP fixture
(no network).

    python3 -m pytest senpi-smart-money/tests/   # or: python3 tests/test_smartmoney.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import smartmoney  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "smartmoney_fixture.json")


def _client():
    with open(FIXTURE) as f:
        return smartmoney._FixtureClient(json.load(f))


def _result():
    return smartmoney.run(_client(), want_near=True)


def _tmp_state():
    return os.path.join(tempfile.mkdtemp(), "state.json")


def _canon(obj):
    return json.dumps(obj, sort_keys=True)


def test_cohorts_built():
    c = _result()["cohorts"]
    assert c["smart"]["members_sampled"] >= 2   # 0xsmart1/2 land in the >=$1M band
    assert c["crowd"]["members_sampled"] >= 2   # 0xcrowd1/2 land in the $10-100k band


def test_smart_leaning_headline():
    """The proven cohort: short HYPE, long BTC — both above the lean threshold with enough members."""
    leaning = {x["asset"]: x for x in _result()["smart_leaning"]}
    assert leaning["HYPE"]["direction"] == "short" and leaning["HYPE"]["members"] >= 5
    assert leaning["BTC"]["direction"] == "long" and leaning["BTC"]["members"] >= 5


def test_divergence_opposite_sides():
    """The core signal: smart short HYPE while the crowd is long it — flagged opposite_sides."""
    div = {x["asset"]: x for x in _result()["divergences"]}
    assert "HYPE" in div, "HYPE divergence not detected"
    h = div["HYPE"]
    assert h["opposite_sides"] is True
    assert h["smart_direction"] == "short" and h["crowd_direction"] == "long"
    assert h["smart_members"] >= 5 and h["crowd_members"] >= 5


def test_near_term_present_when_healthy():
    res = _result()
    assert res["meta"]["near_term_available"] is True
    near = res["near_term"]
    assert near["status"] == {"window": "4h", "last_update_timestamp": 1782226800000,
                              "total_leaderboard_traders": 4821}                    # never `prices`
    hype = near["concentration"]["rows"][0]
    assert (hype["token"], hype["direction"], hype["contribution_pct_change_15m"]) == ("HYPE", "short", 0.4)
    assert near["hot_traders"]["rows"][0]["trader_id"] == "0xhot"
    # a blocked, positionless event is still a tier crossing: kept, without inventing positions
    assert near["momentum_events"]["rows"] == [{
        "trader_id": "0xhot", "tier_label": "Tier 1 (Exceptional, $2+)", "delta_pnl": 2100000,
        "decision": "blocked", "blocked_reason": "concentration_too_low", "detected_at": "2026-06-23T14:55:00Z"}]


# Shapes per senpi-hyperliquid-mcp src/types/leaderboard.types.ts + src/tools/leaderboard.tools.ts: each
# handler wraps its client payload under one more key (data.markets.markets[], data.events.events[], …).
def _live_sized_responses():
    """Sized like the live reads of 2026-10-09 (near_term printed 227,529 chars): a status with a price
    for every market, the 200-row market board, 100 top traders, and 200 momentum events."""
    pos = [{"market": m, "delta_pnl": 2e6, "direction": "short", "leverage": 5} for m in ("ETH", "BTC", "SOL", "HYPE")]
    events = [{"trader_id": f"0x{i % 12:040x}", "tier": 1, "tier_label": "Tier 1 (Exceptional, $2+)",
               "delta_pnl": 5_016_915.63417801 - i, "decision": "sent" if i in (7, 30) else "blocked",
               "blocked_reason": None if i in (7, 30) else "trader_cooldown_active",
               "blocked_details": None if i in (7, 30) else f"Trader 0x{i % 12:040x} cooldown active, Tier1 not > Tier1",
               "concentration": 0.7 if i in (7, 30) else None, "top_positions": pos if i in (7, 30) else None,
               "trader_tags": None, "detected_at": f"2026-10-09T20:{42 - i // 10:02d}:{59 - i % 60:02d}.470Z"}
              for i in range(200)]
    return {
        "leaderboard_get_status": {"success": True, "data": {"status": {
            "window": "4h", "base_snapshot_timestamp": 1, "last_update_timestamp": 2,
            "total_leaderboard_traders": 5000, "prices": {f"xyz:ASSET{i}": 1.0 + i for i in range(800)}}}},
        "leaderboard_get_markets": {"success": True, "data": {"markets": {"markets": [
            {"token": f"A{i}", "dex": "xyz" if i % 3 else "", "direction": "long", "max_leverage": 20,
             "pct_of_top_traders_gain": 50 - i / 10, "contribution_pct_change_15m": 0.1234567,
             "contribution_pct_change_1h": 0.2345678, "contribution_pct_change_4h": 0.3456789,
             "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": 0.5,
             "token_price_change_pct_4h": 1.5123456, "day_notional_volume": 1e8, "trader_count": 40,
             "is_dominant_direction": True} for i in range(200)],
            "source_trader_count": 100, "window": "4h", "timestamp": 3}}},
        "leaderboard_get_top": {"success": True, "data": {"leaderboard": {"data": [
            {"rank": i + 1, "trader_id": f"0x{i:040x}", "unrealized_pnl": 1e6 - i, "pnl_percentage": 12.5,
             "ath_delta": -100.0, "top_markets": ["BTC", "ETH", "HYPE"],
             "trader_tags": {"tas": "Active", "tcs": "Elite", "trp_score": 80, "trp_label": "High"}}
            for i in range(100)], "window": "4h", "timestamp": 3, "total_traders": 5000}, "count": 100}},
        "leaderboard_get_momentum_events": {"success": True, "data": {"events": {"events": events, "query": {},
                                                                              "total_count": 1835}}},
    }


class _Client:
    def __init__(self, responses):
        self.responses, self.calls = responses, {}

    def mcp_call(self, tool, timeout=12, **kw):
        self.calls[tool] = kw
        return self.responses.get(tool)


def test_near_term_is_bounded_on_live_sized_reads():
    """OpenClaw truncates at ~16k chars: near_term must print complete, projected JSON within its budget."""
    client, meta = _Client(_live_sized_responses()), {"warnings": []}
    near = smartmoney.fetch_near_term(client, meta)
    assert meta["warnings"] == []
    assert len(json.dumps(near, ensure_ascii=False)) <= smartmoney.NEAR_BUDGET_CHARS
    assert client.calls["leaderboard_get_momentum_events"] == {"limit": 200}
    assert "prices" not in near["status"]
    c, h, m = near["concentration"], near["hot_traders"], near["momentum_events"]
    assert (c["total_count"], c["source_trader_count"], h["total_count"], h["total_traders"], m["total_count"]) == \
        (200, 100, 100, 5000, 1835)
    assert 1 <= len(c["rows"]) <= smartmoney.NEAR_MARKET_LIMIT and c["rows"][0]["token"] == "A0"
    assert "day_notional_volume" not in c["rows"][0]
    assert len(h["rows"]) == smartmoney.NEAR_TRADER_LIMIT and "trader_tags" not in h["rows"][0]
    # one row per trader, the two with a sent event first, newest first after that; the budget trims from
    # the tail of the largest layer, so the order survives whatever it cuts
    rows = m["rows"]
    assert 3 <= len(rows) <= smartmoney.NEAR_EVENT_LIMIT
    assert [(r["trader_id"][-2:], r["decision"]) for r in rows[:2]] == [("07", "sent"), ("06", "sent")]
    assert [r["trader_id"][-2:] for r in rows[2:]] == ["00", "01", "02", "03", "04", "05"][:len(rows) - 2]
    assert len(rows[0]["top_positions"]) == 3 and "top_positions" not in rows[2]
    assert all("blocked_details" not in r for r in rows)


def test_near_term_is_untouched_by_the_budget_when_it_fits():
    """Prod's shape on 2026-10-09 (3 traders re-firing, every event blocked and positionless) fits whole:
    the budget only bites on outliers."""
    responses = _live_sized_responses()
    for i, e in enumerate(responses["leaderboard_get_momentum_events"]["data"]["events"]["events"]):
        e.update(trader_id=f"0x{i % 3:040x}", decision="blocked", blocked_reason="concentration_too_low",
                 top_positions=None)
    near = smartmoney.fetch_near_term(_Client(responses), {"warnings": []})
    assert [len(near[k]["rows"]) for k in ("concentration", "hot_traders", "momentum_events")] == [
        smartmoney.NEAR_MARKET_LIMIT, smartmoney.NEAR_TRADER_LIMIT, 3]


def test_near_term_budget_trims_rows_never_below_one():
    responses = _live_sized_responses()
    for t in responses["leaderboard_get_top"]["data"]["leaderboard"]["data"]:
        t["top_markets"] = [f"xyz:MARKET{j}" for j in range(80)]            # an oversized row
    near = smartmoney.fetch_near_term(_Client(responses), {"warnings": []})
    assert len(json.dumps(near, ensure_ascii=False)) <= smartmoney.NEAR_BUDGET_CHARS
    assert len(near["hot_traders"]["rows"]) == 1 and near["hot_traders"]["rows"][0]["rank"] == 1
    assert near["hot_traders"]["total_count"] == 100              # the source count still says how many

    # nothing fits: one row per layer, never zero
    for r in responses["leaderboard_get_markets"]["data"]["markets"]["markets"]:
        r["token"] = "x" * 3000
    for e in responses["leaderboard_get_momentum_events"]["data"]["events"]["events"]:
        e["tier_label"] = "x" * 3000
    near = smartmoney.fetch_near_term(_Client(responses), {"warnings": []})
    assert [len(near[k]["rows"]) for k in ("concentration", "hot_traders", "momentum_events")] == [1, 1, 1]


def test_a_renamed_envelope_is_unavailable_not_empty():
    """Schema drift must read as a null layer plus a warning — empty rows would narrate as "nothing hot"."""
    responses = _live_sized_responses()
    responses["leaderboard_get_markets"] = {"success": True, "data": {"markets": {"data": []}}}
    responses["leaderboard_get_status"] = {"success": True, "data": {"window": "4h"}}
    meta = {"warnings": []}
    near = smartmoney.fetch_near_term(_Client(responses), meta)
    assert near["concentration"] is None and near["status"] is None
    assert near["hot_traders"]["rows"] and near["momentum_events"]["rows"]
    assert any(w.startswith("leaderboard_get_markets failed") and "markets.markets" in w for w in meta["warnings"])
    assert any("leaderboard_get_status" in w for w in meta["warnings"])


def test_cohorts_unavailable_flag_on_empty():
    """No discovery data → flagged honestly, no exception, and named as what it was: a read that
    SUCCEEDED and returned nothing. That is the app-scoped-token case, so the token note belongs
    on this branch and only on it."""
    res = smartmoney.run(smartmoney._FixtureClient({}), want_near=True)
    cu = res["meta"].get("cohorts_unavailable")
    assert cu and "succeeded and returned no traders" in cu
    assert "USER-scoped SENPI_AUTH_TOKEN" in cu
    assert res["smart_leaning"] == [] and res["divergences"] == []


class _DeadDiscoveryClient:
    """Every discovery read times out (the upstream trader-data degradation of 2026-09-18); the rest
    of the engine reads offline-empty."""

    def mcp_call(self, tool, timeout=12, **kw):
        if tool.startswith("discovery_"):
            raise TimeoutError("timed out")
        return None


def test_a_failed_cohort_read_is_never_reported_as_a_token_problem():
    """An empty successful read and a failed read are different facts and must not render as the
    same sentence. `cohorts_unavailable` asserted the token scope for both, so a timed-out cohort
    read presented to the agent — and to whoever read the output — as an auth misconfiguration."""
    res = smartmoney.run(_DeadDiscoveryClient(), want_near=False)
    cu = res["meta"].get("cohorts_unavailable")
    assert cu and "read failed" in cu
    assert "timed out" in cu                       # the recorded failure is quoted, not guessed at
    assert "SENPI_AUTH_TOKEN" not in cu and "app-scoped" not in cu
    assert res["smart_leaning"] == [] and res["divergences"] == []


# ──────────────────────────────────────────────────────────── step subcommands (fast, resumable)
def test_step_cohorts_emits_its_slice():
    """`cohorts` step emits ONLY the cohort slice (headline + divergence table), no near_term, offline."""
    res = smartmoney.step_cohorts(_client(), want_near=True, state_path=_tmp_state())
    assert set(res.keys()) == {"as_of", "cohorts", "smart_leaning", "divergences", "meta"}
    assert "near_term" not in res
    div = {x["asset"]: x for x in res["divergences"]}
    assert div["HYPE"]["opposite_sides"] is True   # the core signal survives the step boundary


def test_step_near_term_emits_its_slice():
    """`near_term` step layers the 4h flow onto the (self-healed) cohort headline, offline."""
    res = smartmoney.step_near_term(_client(), want_near=True, state_path=_tmp_state())
    assert "near_term" in res and res["meta"]["near_term_available"] is True
    assert res["near_term"]["concentration"]["rows"][0]["token"] == "HYPE"


def test_cohorts_then_near_term_reproduces_all():
    """cohorts → near_term over a SHARED state file reproduces the composed `all` output, field by field."""
    all_res = _result()
    statep = _tmp_state()
    c_res = smartmoney.step_cohorts(_client(), want_near=True, state_path=statep)
    n_res = smartmoney.step_near_term(_client(), want_near=True, state_path=statep)
    composed = {"as_of": n_res["as_of"], "cohorts": c_res["cohorts"],
                "smart_leaning": c_res["smart_leaning"], "divergences": c_res["divergences"],
                "near_term": n_res["near_term"]}
    for k in ("as_of", "cohorts", "smart_leaning", "divergences", "near_term"):
        assert _canon(all_res[k]) == _canon(composed[k]), f"{k} diverged from all"


def test_near_term_self_heals_on_absent_state():
    """`near_term` with NO prior state re-runs the cohort fetch itself → still produces the full read."""
    all_res = _result()
    res = smartmoney.step_near_term(_client(), want_near=True, state_path=_tmp_state())
    assert _canon(res["divergences"]) == _canon(all_res["divergences"])
    assert res["meta"]["near_term_available"] is True


def test_near_term_fails_open_on_corrupt_state():
    """A corrupt state file → fail-open recompute (self-heal), never an exception."""
    all_res = _result()
    statep = _tmp_state()
    os.makedirs(os.path.dirname(statep), exist_ok=True)
    with open(statep, "w") as fh:
        fh.write("{ not valid json ]]")
    res = smartmoney.step_near_term(_client(), want_near=True, state_path=statep)
    assert _canon(res["divergences"]) == _canon(all_res["divergences"])


def test_step_cohorts_unavailable_flag_offline():
    """Empty discovery → `cohorts` step flags cohorts_unavailable and near_term reads it from state."""
    statep = _tmp_state()
    ec = smartmoney.step_cohorts(smartmoney._FixtureClient({}), want_near=True, state_path=statep)
    assert ec["meta"].get("cohorts_unavailable")
    assert ec["smart_leaning"] == [] and ec["divergences"] == []
    en = smartmoney.step_near_term(smartmoney._FixtureClient({}), want_near=True, state_path=statep)
    assert en["meta"].get("cohorts_unavailable")   # propagated through the shared state


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
