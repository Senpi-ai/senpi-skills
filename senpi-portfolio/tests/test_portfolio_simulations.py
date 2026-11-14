#!/usr/bin/env python3
"""Simulations sit apart from live money.

`portfolio.py` reads simulations with a second `strategy_list {simulation: true}` call and shows them in
their own `simulations` section. They never enter the book, the totals or any reconcile. A simulation
reply without `simulation_routing: true`, or a `SIMULATION_UNAVAILABLE` failure, leaves the live part exactly as
it would be without the simulation call.

    python3 -m pytest senpi-portfolio/tests/test_portfolio_simulations.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import copy
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import portfolio  # noqa: E402
import test_portfolio as base  # noqa: E402

SIMULATION_WALLET = "0x0000000000000000" + "1762500000000" + "0a1b2c3d4e5"
SIMULATION_ID = "simulation-11111111-2222-4333-8444-555555555555"


def setup_module(module):
    base.setup_module(module)


def teardown_module(module):
    base.teardown_module(module)


def _fixture():
    with open(base.FIXTURE) as fh:
        return json.load(fh)


def _simulation_row(marked=True, sid=SIMULATION_ID, wallet=SIMULATION_WALLET):
    row = {"id": sid, "strategyName": "omega", "strategyMetadata": {"skillName": "omega"},
           "strategyWalletAddress": wallet, "status": "ACTIVE", "totalFunded": 1000}
    if marked:
        row["simulation"] = True
    return row


def _with_simulation(fx, reply):
    fx = copy.deepcopy(fx)
    fx["strategy_list::simulation"] = reply
    fx[f"strategy_get_clearinghouse_state::{SIMULATION_WALLET}"] = {
        "main": {"marginSummary": {"accountValue": "1012.5"}, "withdrawable": "900", "assetPositions": []},
        "xyz": {"marginSummary": {"accountValue": "900"}, "withdrawable": "900", "assetPositions": []}}
    return fx


class _Recording(portfolio._FixtureClient):
    def __init__(self, fx, raise_simulation=None):
        super().__init__(fx)
        self.calls = []
        self._raise = raise_simulation

    def mcp_call(self, tool, timeout=12, **kw):
        self.calls.append((tool, dict(kw)))
        if tool == "strategy_list" and kw.get("simulation") is True and self._raise:
            raise RuntimeError(self._raise)
        return super().mcp_call(tool, timeout=timeout, **kw)


def _live_view(result):
    out = {k: v for k, v in result.items() if k != "simulations"}
    out["meta"] = {k: v for k, v in out["meta"].items() if k != "warnings"}
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def _run(client):
    return portfolio.run(client, want_market=False)


def test_simulations_are_listed_apart_from_live():
    fx = _with_simulation(_fixture(), {"success": True, "data": {"simulation_routing": True,
                                                             "strategies": [_simulation_row()]}})
    client = _Recording(fx)
    result = _run(client)
    sims = result["simulations"]
    assert sims["status"] == "ok"
    assert [(s["strategy_id"], s["wallet"], s["account_value"]) for s in sims["strategies"]] == [
        (SIMULATION_ID, SIMULATION_WALLET, 1012.5)]
    assert SIMULATION_WALLET not in {s["wallet"] for s in result["strategies"]}
    assert SIMULATION_WALLET not in json.dumps(result["book"])
    simulation_lists = [kw for tool, kw in client.calls if tool == "strategy_list" and kw.get("simulation") is True]
    assert len(simulation_lists) == 1


def test_simulations_never_change_the_live_part():
    without = _run(_Recording(_fixture()))
    fx = _with_simulation(_fixture(), {"success": True, "data": {"simulation_routing": True,
                                                             "strategies": [_simulation_row()]}})
    with_sims = _run(_Recording(fx))
    assert _live_view(with_sims) == _live_view(without)
    assert with_sims["totals"] == without["totals"]


def test_simulation_unavailable_leaves_the_live_part_intact():
    without = _run(_Recording(_fixture()))
    result = _run(_Recording(_fixture(), raise_simulation="tool failed: SIMULATION_UNAVAILABLE: simulations are not available"))
    assert result["simulations"]["status"] == "unavailable"
    assert result["simulations"]["strategies"] == []
    assert "SIMULATION_UNAVAILABLE" in result["simulations"]["reason"]
    assert _live_view(result) == _live_view(without)
    assert not result["meta"].get("strategy_list_failed")


def test_a_reply_without_the_marker_shows_no_simulations():
    live_rows = _fixture()["strategy_list"]
    result = _run(_Recording(_with_simulation(_fixture(), {"success": True, "data": live_rows})))
    assert result["simulations"]["status"] == "unproven"
    assert result["simulations"]["strategies"] == []


def test_rows_without_the_simulation_field_are_ignored():
    fx = _with_simulation(_fixture(), {"success": True, "data": {"simulation_routing": True,
                                                             "strategies": [_simulation_row(marked=False)]}})
    result = _run(_Recording(fx))
    assert result["simulations"]["status"] == "ok"
    assert result["simulations"]["strategies"] == []


def test_a_simulation_row_in_the_live_reply_stays_out_of_the_book():
    fx = _fixture()
    fx["strategy_list"]["strategies"].append(_simulation_row())
    fx = _with_simulation(fx, {"success": True, "data": {"simulation_routing": True, "strategies": []}})
    result = _run(_Recording(fx))
    assert SIMULATION_WALLET not in {s["wallet"] for s in result["strategies"]}


def test_money_step_carries_the_simulations_section():
    fx = _with_simulation(_fixture(), {"success": True, "data": {"simulation_routing": True,
                                                             "strategies": [_simulation_row()]}})
    state = os.path.join(tempfile.mkdtemp(), "state.json")
    out = portfolio.step_money(_Recording(fx), state_path=state)
    assert [s["strategy_id"] for s in out["simulations"]["strategies"]] == [SIMULATION_ID]
    assert SIMULATION_WALLET not in {s["wallet"] for s in out["strategies"]}
    again = portfolio.step_strategies(_Recording(_fixture()), state_path=state)
    assert [s["strategy_id"] for s in again["simulations"]["strategies"]] == [SIMULATION_ID]
