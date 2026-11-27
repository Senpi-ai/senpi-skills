#!/usr/bin/env python3
"""`--simulation` on deploy.py, close.py and status.py: one world per command.

The fake MCP follows the `strategy_list` contract for simulations: `simulation: true` answers simulations
only, with `simulation_routing: true` at the head of `data` and `simulation: true` on each row. `simulation` absent
answers live strategies only. An MCP with no simulation router ignores the flag and answers live rows with
no marker, and `SIMULATION_UNAVAILABLE` comes back as a tool failure.

    python3 -m pytest senpi-strategy-ops/tests/test_simulation_world.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli      # noqa: E402
import _journal  # noqa: E402
import close     # noqa: E402
import deploy    # noqa: E402
import status    # noqa: E402
from mcp_client import MCPError  # noqa: E402

LIVE_WALLET = "0x" + "ab" * 20
SIMULATION_WALLET = "0x0000000000000000" + "1762500000000" + "0a1b2c3d4e5"
SIMULATION_WALLET_2 = "0x0000000000000000" + "1762500000001" + "0a1b2c3d4e6"
SIMULATION_ID = "simulation-11111111-2222-4333-8444-555555555555"

# What `mcp_client._unwrap` raises for the MCP's `{success: false, error: {code, message, ...}}`:
# `tool failed: <code>: <message>`. The error's `details` (simulation-service's name, path) are not in it.
SIMULATION_OUTAGE = ("tool failed: SIMULATION_UNAVAILABLE: Simulations are unavailable right now: "
                "simulation-service did not answer within 5000 ms. Retry shortly with the same input.")
ROUTER_OFF = ("tool failed: SIMULATION_UNAVAILABLE: Simulations are not available on this "
              "server yet. Nothing was sent; try again later.")
# The router's answer when simulations are switched off on that server. It says "there are none here", but
# simulations made while they were on still exist in simulation-service, so it is not an answer.
SIMULATION_OFF = ("tool failed: SIMULATION_DISABLED: Simulations are switched off on this server, so "
             "there are none here. Nothing was sent. This error is deterministic. Do not retry with the "
             "same arguments.")
# Since MCP #336, with simulations off `strategy_list {simulation: true}` still answers (with the marker); every
# other simulation call answers this.
SIM_CALLS_OFF = ("tool failed: SIMULATION_DISABLED: Simulations are switched off on this server, so "
              "this call was refused and nothing was sent. Simulations made while they were on still exist: "
              "strategy_list with simulation: true lists them. This error is deterministic. Do not retry with "
              "the same arguments.")


def _live_row(sid="live-1", wallet=LIVE_WALLET, skill="omega", status_="ACTIVE"):
    return {"id": sid, "strategyWalletAddress": wallet, "status": status_, "strategyName": skill,
            "strategyMetadata": {"skillName": skill}}


def _simulation_row(sid=SIMULATION_ID, wallet=SIMULATION_WALLET, skill="omega", status_="ACTIVE", marked=True):
    row = {"id": sid, "strategyWalletAddress": wallet, "status": status_, "strategyName": skill,
           "strategyMetadata": {"skillName": skill}}
    if marked:
        row["simulation"] = True
    return row


class FakeMCP:
    def __init__(self, live=(), simulation=(), router=True, unavailable=False, writes_off=False):
        self.live, self.simulation = list(live), list(simulation)
        self.router, self.unavailable = router, unavailable
        self.writes_off = writes_off
        self.lists, self.closed = [], []

    def _simulation_call(self, kw):
        return self.router and (kw.get("simulation") is True
                                or str(kw.get("strategyId") or kw.get("strategy_id") or "").startswith("simulation-"))

    def mcp_call(self, name, timeout=None, **kw):
        if name in ("strategy_close", "strategy_get") and self._simulation_call(kw):
            if self.writes_off is True:
                raise MCPError(SIM_CALLS_OFF)
            if self.writes_off:
                raise MCPError(self.writes_off)
        if name == "strategy_close":
            self.closed.append(kw)
            return {"success": True, "data": {}}
        if name == "strategy_get":
            if "strategy_id" not in kw:
                raise MCPError("tool failed: INVALID_ARGUMENT: Invalid arguments — strategy_id: Required")
            rows = self.simulation if self._simulation_call(kw) else self.live
            row = next((r for r in rows if r["id"] == kw.get("strategy_id")), None)
            if row is None:
                raise MCPError("tool failed: NOT_FOUND: no such strategy")
            return {"success": True, "data": {"strategy": row}}
        assert name == "strategy_list", name
        self.lists.append(dict(kw))
        want = {str(s).upper() for s in kw.get("status") or []}

        def pick(rows):
            return [r for r in rows if not want or str(r.get("status")).upper() in want]
        if kw.get("simulation") is True and self.router:
            if self.unavailable:
                raise MCPError(self.unavailable if isinstance(self.unavailable, str) else SIMULATION_OUTAGE)
            return {"success": True, "data": {"simulation_routing": True, "strategies": pick(self.simulation)}}
        return {"success": True, "data": {"strategies": pick(self.live)}}


def _gw(kind, payload=None, message=None):
    return {"kind": kind, "payload": payload, "message": message, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


class ReaderOneWorldPerCall(unittest.TestCase):
    def test_a_simulation_read_sends_the_flag_and_keeps_only_marked_rows(self):
        mcp = FakeMCP(simulation=[_simulation_row(), _simulation_row(sid="simulation-x", wallet=SIMULATION_WALLET_2, marked=False)])
        rows = _cli.list_strategies_or_none(mcp, statuses=["ACTIVE"], simulation=True)
        self.assertEqual([r["id"] for r in rows], [SIMULATION_ID])
        self.assertEqual(mcp.lists, [{"status": ["ACTIVE"], "simulation": True}])

    def test_a_live_read_is_todays_call_and_drops_marked_rows(self):
        mcp = FakeMCP(live=[_live_row(), _simulation_row()])
        rows = _cli.list_strategies_or_none(mcp, statuses=["ACTIVE"])
        self.assertEqual([r["id"] for r in rows], ["live-1"])
        self.assertEqual(mcp.lists, [{"status": ["ACTIVE"]}])

    def test_a_simulation_reply_without_the_marker_is_no_answer(self):
        why = []
        mcp = FakeMCP(live=[_live_row()], router=False)
        self.assertIsNone(_cli.list_strategies_or_none(mcp, simulation=True, why=why))
        self.assertIn("simulation_routing: true", why[0])

    def test_a_live_reply_carrying_the_marker_is_no_answer(self):
        class Marked(FakeMCP):
            def mcp_call(self, name, timeout=None, **kw):
                return {"success": True, "data": {"simulation_routing": True, "strategies": [_live_row()]}}
        self.assertIsNone(_cli.list_strategies_or_none(Marked()))

    def test_simulation_unavailable_is_no_answer(self):
        why = []
        self.assertIsNone(_cli.list_strategies_or_none(FakeMCP(unavailable=True), simulation=True, why=why))
        self.assertIn("SIMULATION_UNAVAILABLE", why[0])

    def test_simulation_disabled_is_an_unknown_answer_not_an_empty_one(self):
        why = []
        self.assertIsNone(_cli.list_strategies_or_none(FakeMCP(unavailable=SIMULATION_OFF), simulation=True, why=why))
        self.assertIn("SIMULATION_DISABLED", why[0])
        self.assertNotIsInstance(why[0], _cli.NoSimulationRouter)


class RuntimeIds(unittest.TestCase):
    def test_live_and_simulation_ids(self):
        self.assertEqual(_cli.runtime_id_for("omega", "main", False), "omega-main")
        self.assertEqual(_cli.runtime_id_for("omega", "main", True), "omega-main-simulation")

    def test_a_simulation_id_is_never_cut(self):
        # The runtime refuses a simulation `<id>-<instance>` over 29 characters instead of cutting it,
        # because a cut gave two packages one runtime id.
        self.assertEqual(_cli.runtime_id_for("a" * 29, "main", True), "a" * 29 + "-main-simulation")
        self.assertEqual(_cli.runtime_id_for("a" * 30, "instance", True), "a" * 30 + "-instance-simulation")

    def test_simulation_addresses(self):
        self.assertTrue(_cli.is_simulation_address(SIMULATION_WALLET))
        self.assertTrue(_cli.is_simulation_address("  " + SIMULATION_WALLET.upper().replace("0X", "0x") + " "))
        self.assertFalse(_cli.is_simulation_address(LIVE_WALLET))
        self.assertFalse(_cli.is_simulation_address(None))

    def test_the_zero_address_is_live(self):
        zero = "0x" + "0" * 40
        for value in (zero, zero.upper().replace("0X", "0x")):
            self.assertFalse(_cli.is_simulation_address(value))
            self.assertFalse(_cli.looks_simulation(value))
        self.assertFalse(_cli.is_simulation_address("0x" + "0" * 29 + "0a1b2c3d4e5"))


class _CloseHarness(unittest.TestCase):
    """Drives `close.main` end to end over a FakeMCP, with the runtime inventory stubbed."""

    def setUp(self):
        self._orig = (close.MCPClient, _cli.list_runtimes, _cli.list_runtimes_or_none, _cli.run_cli,
                      close._pkg.load, _cli.gateway_call)
        self.mcp = FakeMCP()
        self.runtimes = []
        self.deleted = []
        mcp = self.mcp
        close.MCPClient = lambda *a, **k: mcp
        outer = self

        def _inventory(*a, **k):
            return [r for r in outer.runtimes if r["name"] not in outer.deleted]
        _cli.list_runtimes = _inventory
        _cli.list_runtimes_or_none = _inventory
        _cli.run_cli = lambda args, timeout=60: (0, "", "")

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            if method == "senpi.deleteRuntime":
                outer.deleted.append(params["id"])
                return _gw("ok", payload={})
            if method == "senpi.deploy.status":
                return _gw("refused", message="[NOT_FOUND] No deploy has been started on this agent.")
            raise AssertionError(method)
        _cli.gateway_call = _gateway

        class _Inst:
            name = "main"

        class _Pkg:
            id = "omega"
            version = "1.0.0"
            instances = [_Inst()]
            dir = Path("/nonexistent-close-py-simulation-test")
        close._pkg.load = lambda _p: _Pkg()

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))

    def tearDown(self):
        (close.MCPClient, _cli.list_runtimes, _cli.list_runtimes_or_none, _cli.run_cli,
         close._pkg.load, _cli.gateway_call) = self._orig

    def _run(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                close.main(["close.py", *args])
        code = ctx.exception.code
        if isinstance(code, str):
            return out.getvalue() + code, 1
        return out.getvalue(), code


class CloseOneWorld(_CloseHarness):
    def _both(self):
        self.mcp.live = [_live_row()]
        self.mcp.simulation = [_simulation_row()]
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"},
                         {"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]

    def test_close_by_name_closes_only_the_live_strategy_when_it_has_no_simulation(self):
        self.mcp.live = [_live_row()]
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"}]
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.assertEqual(self.deleted, ["omega-main"])
        self.assertNotIn("simulation", out)

    def test_close_by_name_with_live_closes_only_the_live_strategy(self):
        self._both()
        _out, code = self._run("omega", "--live")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.assertEqual(self.deleted, ["omega-main"])
        self.assertTrue(all("simulation" not in c for c in self.mcp.lists))

    def test_a_live_close_polls_with_live(self):
        self._both()
        out, code = self._run("omega", "--live")
        self.assertEqual(code, 0)
        self.assertIn("re-running: close.py omega --live", out)
        self.mcp.closed.clear()
        out, code = self._run("omega", "--live")
        self.assertEqual(code, 0)
        self.assertNotIn("NOTHING was closed", out)

    def test_close_by_name_refuses_when_the_package_also_has_a_simulation(self):
        self._both()
        out, code = self._run("omega")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("live-1", out)
        self.assertIn(SIMULATION_ID[:8], out)
        self.assertIn("close.py omega --live", out)
        self.assertIn("close.py omega --simulation", out)

    def test_the_refusal_keeps_the_instance_in_both_commands(self):
        self._both()
        out, code = self._run("omega", "--instance", "main")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertIn("close.py omega --instance main --live", out)
        self.assertIn("close.py omega --instance main --simulation", out)

    def test_a_closed_simulation_does_not_block_a_plain_close(self):
        self._both()
        self.mcp.simulation = [_simulation_row(status_="CLOSED")]
        _out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])

    def test_plain_close_is_unchanged_when_the_mcp_has_no_simulation_router(self):
        self._both()
        self.mcp.router = False
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.assertNotIn("simulation", out)
        self.assertNotIn("--live", out)

    def test_plain_close_refuses_when_simulation_is_off_on_the_server(self):
        # Switching simulations off does not delete the simulations made while it was on.
        self._both()
        self.mcp.unavailable = SIMULATION_OFF
        out, code = self._run("omega")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("SIMULATION_DISABLED", out)
        self.assertIn("close.py omega --live", out)

    def test_plain_close_of_a_live_only_package_refuses_when_simulation_is_off(self):
        self.mcp.live = [_live_row()]
        self.mcp.unavailable = SIMULATION_OFF
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"}]
        out, code = self._run("omega")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("close.py omega --live", out)

    def test_live_close_still_works_when_simulation_is_off(self):
        self._both()
        self.mcp.unavailable = SIMULATION_OFF
        _out, code = self._run("omega", "--live")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.assertEqual(self.deleted, ["omega-main"])

    def test_simulation_close_refuses_when_simulation_is_off(self):
        self._both()
        self.mcp.unavailable = SIMULATION_OFF
        out, code = self._run("omega", "--simulation")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("Do not re-run without --simulation", out)

    def test_plain_close_refuses_when_the_simulation_read_is_unknown(self):
        self._both()
        self.mcp.unavailable = True
        out, code = self._run("omega")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("SIMULATION_UNAVAILABLE", out)
        self.assertIn("close.py omega --live", out)

    def test_live_with_simulation_is_an_argument_error(self):
        _out, code = self._run("omega", "--live", "--simulation")
        self.assertEqual(code, 2)
        self.assertEqual(self.mcp.closed, [])

    def test_close_all_and_a_direct_live_id_are_not_gated(self):
        self._both()
        _out, code = self._run("--all", "--live")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.mcp.closed.clear()
        _out, code = self._run("--strategy-id", "live-1")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])

    def test_close_by_name_with_simulation_closes_only_the_simulation(self):
        self._both()
        out, code = self._run("omega", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": SIMULATION_ID}])
        self.assertEqual(self.deleted, ["omega-main-simulation"])
        self.assertTrue(self.mcp.lists and all(c.get("simulation") is True for c in self.mcp.lists))
        self.assertIn("close.py omega --simulation", out)

    def test_simulation_close_refuses_a_reply_without_the_marker(self):
        self._both()
        self.mcp.router = False
        out, code = self._run("omega", "--simulation")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("could not read your simulations", out)
        self.assertIn("Do not re-run without --simulation", out)

    def test_simulation_close_refuses_on_simulation_unavailable(self):
        self._both()
        self.mcp.unavailable = True
        out, code = self._run("omega", "--simulation")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertIn("SIMULATION_UNAVAILABLE", out)

    def test_simulation_close_ignores_a_row_without_the_simulation_field(self):
        self.mcp.simulation = [_simulation_row(marked=False)]
        self.runtimes = [{"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]
        out, code = self._run("omega", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("no strategies for this package", out)

    def test_simulation_close_by_instance_finds_the_simulation_runtime(self):
        self._both()
        _out, code = self._run("omega", "--instance", "main", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": SIMULATION_ID}])
        self.assertEqual(self.deleted, ["omega-main-simulation"])

    def test_close_all_with_simulation_closes_only_simulations(self):
        self._both()
        _out, code = self._run("--all", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": SIMULATION_ID}])

    def test_close_all_with_live_closes_only_live(self):
        self._both()
        _out, code = self._run("--all", "--live")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.assertEqual(self.deleted, ["omega-main"])


class CloseAllMeansAll(_CloseHarness):
    """"Close everything" runs `close.py --all`. It closed live strategies only, so a user's open
    simulations kept running and kept being charged."""

    def _both(self):
        self.mcp.live = [_live_row()]
        self.mcp.simulation = [_simulation_row()]
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"},
                         {"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]

    def test_close_all_closes_live_strategies_and_simulations(self):
        self._both()
        out, code = self._run("--all")
        self.assertEqual(code, 0, out)
        self.assertCountEqual(self.mcp.closed, [{"strategyId": "live-1"}, {"strategyId": SIMULATION_ID}])
        self.assertCountEqual(self.deleted, ["omega-main", "omega-main-simulation"])
        self.assertIn("re-running: close.py --all ", out)

    def test_close_all_closes_simulations_when_nothing_live_is_open(self):
        self.mcp.simulation = [_simulation_row(), _simulation_row(sid="simulation-x", wallet=SIMULATION_WALLET_2)]
        self.runtimes = [{"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]
        out, code = self._run("--all")
        self.assertEqual(code, 0, out)
        self.assertCountEqual(self.mcp.closed, [{"strategyId": SIMULATION_ID}, {"strategyId": "simulation-x"}])
        self.assertEqual(self.deleted, ["omega-main-simulation"])
        self.assertNotIn("no OPEN strategies", out)

    def test_unreadable_simulations_still_close_the_live_ones_and_say_the_simulations_were_not_closed(self):
        self._both()
        self.mcp.unavailable = True
        out, code = self._run("--all")
        self.assertEqual(code, 2, out)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.assertEqual(self.deleted, ["omega-main"])
        self.assertIn("SIMULATION_UNAVAILABLE", out)
        self.assertIn("simulations were NOT closed", out)
        self.assertIn("close.py --all --simulation", out)
        self.assertIn("may still be open and charged", out)
        self.assertNotIn("They keep running", out)

    def test_simulation_writes_off_still_close_the_live_ones_and_stop_no_simulation_runtime(self):
        self._both()
        self.mcp.writes_off = True
        out, code = self._run("--all")
        self.assertEqual(code, 2, out)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.assertEqual(self.deleted, ["omega-main"])
        self.assertIn("SIMULATION_DISABLED", out)
        self.assertIn("simulations were NOT closed", out)
        self.assertIn("They keep running and keep being charged", out)

    def test_an_mcp_with_no_simulation_router_closes_the_live_ones(self):
        self.mcp.live = [_live_row()]
        self.mcp.router = False
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"}]
        out, code = self._run("--all")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])

    def test_nothing_open_in_either_world_is_said_once(self):
        out, code = self._run("--all")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.mcp.closed, [])
        self.assertIn("no OPEN strategies or simulations to close", out)

    def test_close_all_sweeps_the_runtime_of_a_simulation_closed_elsewhere(self):
        self.mcp.simulation = [_simulation_row(status_="CLOSED")]
        self.runtimes = [{"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]
        out, code = self._run("--all")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.deleted, ["omega-main-simulation"])
        self.assertEqual(self.mcp.closed, [])

    def test_with_no_simulation_router_close_all_still_sweeps_a_closed_live_strategys_runtime(self):
        self.mcp.live = [_live_row(status_="CLOSED")]
        self.mcp.router = False
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"}]
        out, code = self._run("--all")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.deleted, ["omega-main"])
        self.assertEqual(self.mcp.closed, [])

    def test_a_closed_simulations_runtime_is_swept_even_if_a_later_inventory_read_fails(self):
        self.mcp.simulation = [_simulation_row(status_="CLOSED")]
        self.runtimes = [{"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]
        reads = []
        inventory = _cli.list_runtimes_or_none

        def _second_read_fails(*a, **k):
            reads.append(1)
            return None if len(reads) == 2 else inventory(*a, **k)
        _cli.list_runtimes_or_none = _second_read_fails
        out, code = self._run("--all")
        self.assertEqual(set(self.deleted), {"omega-main-simulation"}, out)
        self.assertNotIn("no OPEN strategies or simulations to close", out)

    def test_close_all_json_with_nothing_live_and_unreadable_simulations_is_json(self):
        self.mcp.unavailable = True
        out, code = self._run("--all", "--json")
        self.assertEqual(code, 2, out)
        doc = json.loads(out)
        self.assertIn("SIMULATION_UNAVAILABLE", doc["simulationsNotClosed"])
        self.assertEqual(doc["instances"], [])
        self.assertEqual(self.mcp.closed, [])

    def test_a_simulation_id_without_the_flag_is_not_found_and_points_at_simulation(self):
        self._both()
        out, code = self._run("--strategy-id", SIMULATION_ID)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertIn("--simulation", out)

    def test_a_simulation_id_with_the_flag_closes_it(self):
        self._both()
        out, code = self._run("--strategy-id", SIMULATION_ID, "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": SIMULATION_ID}])
        self.assertIn(f"close.py --strategy-id {SIMULATION_ID} --simulation", out)

    def test_a_simulation_close_resolves_the_simulation_deploy_request_only(self):
        self._both()
        for key, rid in ((_journal.key("omega", False), "dpl-req-20261107T000000Z-aaaaaaaa"),
                         (_journal.key("omega", True), "dpl-req-20261107T000000Z-bbbbbbbb")):
            _journal.write(key, {"packageId": "omega", "requestId": rid, "sentAt": "2026-11-07T00:00:00Z",
                                 "verb": "create", "paramsKey": {}, "resolved": None})
        _out, code = self._run("omega", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(_journal.read(_journal.key("omega", True))["resolved"], "closed-by-close.py")
        self.assertIsNone(_journal.read(_journal.key("omega", False))["resolved"])


class SimulationsMadeBeforeTheRename(_CloseHarness):
    """A simulation made while simulations were called paper runs as `<id>-<inst>-paper` and its
    deploy request sits under the `<id>.paper` journal key."""

    def setUp(self):
        super().setUp()
        self.mcp.simulation = [_simulation_row()]
        self.runtimes = [{"name": "omega-main-paper", "wallet": SIMULATION_WALLET, "status": "running"}]

    def test_an_instance_close_stops_the_paper_named_runtime(self):
        _out, code = self._run("omega", "--instance", "main", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": SIMULATION_ID}])
        self.assertEqual(self.deleted, ["omega-main-paper"])

    def test_a_simulation_runtime_wins_over_a_paper_named_one(self):
        self.runtimes = [{"name": "omega-main-paper", "wallet": SIMULATION_WALLET[:-1] + "f",
                          "status": "running"},
                         {"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]
        _out, code = self._run("omega", "--instance", "main", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.deleted, ["omega-main-simulation"])

    def test_a_live_runtime_named_paper_is_not_taken_for_a_simulation(self):
        self.runtimes = [{"name": "omega-main-paper", "wallet": LIVE_WALLET, "status": "running"}]
        _out, code = self._run("omega", "--instance", "main", "--simulation")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])

    def test_a_close_resolves_the_request_journaled_under_paper(self):
        _journal.write("omega.paper", {"packageId": "omega", "requestId": "dpl-req-20261107T000000Z-cccccccc",
                                       "sentAt": "2026-11-07T00:00:00Z", "verb": "create",
                                       "paramsKey": {}, "resolved": None})
        _out, code = self._run("omega", "--simulation")
        self.assertEqual(code, 0)
        entry = _journal.read(_journal.key("omega", True))
        self.assertEqual(entry["requestId"], "dpl-req-20261107T000000Z-cccccccc")
        self.assertEqual(entry["resolved"], "closed-by-close.py")


class Review953Followups(_CloseHarness):
    def test_a_plain_close_polls_with_live_so_a_simulation_outage_does_not_refuse_the_poll(self):
        self.mcp.live = [_live_row()]
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"}]
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertIn("re-running: close.py omega --live", out)
        self.mcp.live = [_live_row(status_="CLOSING_POSITIONS")]
        self.mcp.unavailable = True
        out, code = self._run("omega", "--live")
        self.assertEqual(code, 0)
        self.assertNotIn("NOTHING was closed", out)

    def test_a_simulation_sweep_leaves_a_closed_live_strategys_runtime_alone(self):
        self.mcp.live = [_live_row(status_="CLOSED")]
        self.mcp.simulation = []
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"}]
        for args in (("--all", "--simulation"), ("omega", "--simulation")):
            _out, code = self._run(*args)
            self.assertEqual(code, 0, args)
            self.assertEqual(self.deleted, [], args)

    def test_a_simulation_sweep_removes_a_closed_simulations_runtime(self):
        self.mcp.live = [_live_row(status_="CLOSED")]
        self.mcp.simulation = [_simulation_row(status_="CLOSED")]
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"},
                         {"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]
        _out, code = self._run("--all", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.deleted, ["omega-main-simulation"])
        self.assertEqual(self.mcp.closed, [])

    def test_a_dry_run_plans_the_sweep_the_real_run_does(self):
        self.mcp.live = [_live_row(status_="CLOSED")]
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"}]
        for args in (("omega", "--live"), ("--all",)):
            out, code = self._run(*args, "--dry-run", "--json")
            self.assertEqual(code, 0, args)
            self.assertIn('senpi.deleteRuntime {\\"id\\": \\"omega-main\\"}', out, args)
            self.assertEqual(self.deleted, [], args)
            self.assertEqual(self.mcp.closed, [], args)

class CloseSimulationWithWritesOff(_CloseHarness):
    """The runtime must not be stopped ahead of a refused write."""

    def setUp(self):
        super().setUp()
        self.mcp.live = [_live_row()]
        self.mcp.simulation = [_simulation_row()]
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"},
                         {"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]
        self.mcp.writes_off = True

    def _assert_refused_untouched(self, out, code):
        self.assertNotEqual(code, 0)
        self.assertEqual(self.deleted, [])
        self.assertEqual(self.mcp.closed, [])
        self.assertIn("SIMULATION_DISABLED", out)
        self.assertIn("switched back on", out)

    def test_a_package_close_stops_nothing(self):
        out, code = self._run("omega", "--simulation")
        self._assert_refused_untouched(out, code)
        self.assertIn("close.py omega --simulation", out)

    def test_an_instance_close_stops_nothing(self):
        out, code = self._run("omega", "--instance", "main", "--simulation")
        self._assert_refused_untouched(out, code)

    def test_close_all_stops_nothing(self):
        out, code = self._run("--all", "--simulation")
        self._assert_refused_untouched(out, code)

    def test_a_direct_close_stops_nothing(self):
        for flag, value in (("--strategy-id", SIMULATION_ID), ("--address", SIMULATION_WALLET)):
            out, code = self._run(flag, value, "--simulation")
            self._assert_refused_untouched(out, code)

    def test_the_close_works_once_switched_back_on(self):
        self._run("omega", "--simulation")
        self.mcp.writes_off = False
        _out, code = self._run("omega", "--simulation")
        self.assertEqual(code, 0)
        self.assertEqual(self.deleted, ["omega-main-simulation"])
        self.assertEqual(self.mcp.closed, [{"strategyId": SIMULATION_ID}])

    def test_a_live_close_is_untouched(self):
        _out, code = self._run("omega", "--live")
        self.assertEqual(code, 0)
        self.assertEqual(self.deleted, ["omega-main"])
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])

    def test_a_dry_run_still_plans(self):
        out, code = self._run("omega", "--simulation", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("planned", out)
        self.assertEqual(self.deleted, [])

    def test_a_simulation_not_active_keeps_its_runtime_too(self):
        for status_ in ("PAUSED", "CLOSING_POSITIONS"):
            self.mcp.simulation = [_simulation_row(status_=status_)]
            out, code = self._run("omega", "--simulation")
            self._assert_refused_untouched(out, code)

    def test_a_closed_simulation_still_reads_closed(self):
        self.mcp.simulation = [_simulation_row(status_="CLOSED")]
        out, code = self._run("--strategy-id", SIMULATION_ID, "--simulation")
        self.assertEqual(code, 0)
        self.assertIn("closed", out)
        self.assertEqual(self.deleted, [])

    def _probe_fails(self, exc):
        orig = self.mcp.mcp_call

        def flaky(name, timeout=None, **kw):
            if name == "strategy_get":
                raise exc
            return orig(name, timeout=timeout, **kw)
        self.mcp.mcp_call = flaky

    def test_an_unanswered_probe_stops_nothing(self):
        for exc in (MCPError("strategy_get HTTP 502"), TimeoutError("timed out")):
            self._probe_fails(exc)
            out, code = self._run("omega", "--simulation")
            self.assertNotEqual(code, 0)
            self.assertEqual(self.deleted, [])
            self.assertEqual(self.mcp.closed, [])
            self.assertIn(str(exc), out)
            self.assertIn("close.py omega --simulation", out)


    def test_an_unusable_probe_reply_stops_nothing(self):
        orig = self.mcp.mcp_call
        for reply in (None, {}, {"success": True, "data": {}}, {"success": True, "data": None},
                      {"success": False, "error": {"code": "INTERNAL", "message": "boom"}}):
            def empty(name, timeout=None, _r=reply, **kw):
                if name == "strategy_get":
                    return _r
                return orig(name, timeout=timeout, **kw)
            self.mcp.mcp_call = empty
            out, code = self._run("omega", "--simulation")
            self.assertNotEqual(code, 0, reply)
            self.assertEqual(self.deleted, [], reply)
            self.assertEqual(self.mcp.closed, [], reply)
            self.assertIn("could not be read before closing it", out)
            self.assertIn("close.py omega --simulation", out)


class StatusOneWorld(unittest.TestCase):
    def setUp(self):
        self._orig = (_cli.list_runtimes_or_none, status._runtime_cli_available, _cli.runtime_health_map)
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"},
                         {"name": "omega-main-simulation", "wallet": SIMULATION_WALLET, "status": "running"}]
        _cli.list_runtimes_or_none = lambda *a, **k: list(self.runtimes)
        status._runtime_cli_available = lambda **k: True
        _cli.runtime_health_map = lambda **k: {}

    def tearDown(self):
        _cli.list_runtimes_or_none, status._runtime_cli_available, _cli.runtime_health_map = self._orig

    def _mcp(self, **kw):
        return FakeMCP(live=[_live_row()], simulation=[_simulation_row()], **kw)

    def test_status_without_simulation_lists_live_only_and_no_simulation_orphan(self):
        mcp = self._mcp()
        rows, orphans, _ok = status.build(mcp, deep=False)
        self.assertEqual([r["strategyId"] for r in rows], ["live-1"])
        self.assertEqual(orphans, [])
        self.assertTrue(all("simulation" not in c for c in mcp.lists))

    def test_status_with_simulation_lists_simulations_only_and_no_live_orphan(self):
        mcp = self._mcp()
        rows, orphans, _ok = status.build(mcp, deep=False, simulation=True)
        self.assertEqual([(r["strategyId"], r["runtime"]) for r in rows], [(SIMULATION_ID, "omega-main-simulation")])
        self.assertEqual(orphans, [])
        self.assertTrue(all(c.get("simulation") is True for c in mcp.lists))

    def test_status_with_simulation_ignores_a_row_without_the_simulation_field(self):
        mcp = FakeMCP(simulation=[_simulation_row(marked=False)])
        rows, _orphans, _ok = status.build(mcp, deep=False, simulation=True)
        self.assertEqual(rows, [])

    def test_status_with_simulation_refuses_a_reply_without_the_marker(self):
        with self.assertRaises(SystemExit) as ctx:
            status.build(self._mcp(router=False), deep=False, simulation=True)
        self.assertIn("could not read your simulations", str(ctx.exception.code))

    def test_status_main_labels_simulations(self):
        orig = status.MCPClient
        status.MCPClient = lambda *a, **k: self._mcp()
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(status.main(["status.py", "--simulation", "--fast"]), 0)
        finally:
            status.MCPClient = orig
        out = buf.getvalue()
        self.assertIn("simulation", out)
        self.assertIn(SIMULATION_ID[:8], out)
        self.assertNotIn("live-1", out)


class StatusDriftPerWorld(unittest.TestCase):
    """A simulation's runtime is `<id>-<inst>-simulation`; drift must still find its instance on disk."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "strategy.yaml").write_text("id: omega\n")
        inst = types.SimpleNamespace(name="main", runtime_name="omega-main", runtime_text="x: 1\n",
                                     runtime_doc={"exit": {"dsl_preset": "new"}},
                                     runtime_path=tmp / "main" / "runtime.yaml")
        pkg = types.SimpleNamespace(id="omega", dir=tmp, instances=[inst])
        self._orig = (status._pkg.resolve_pkg_dir, status._pkg.load)
        status._pkg.resolve_pkg_dir = lambda _id: tmp
        status._pkg.load = lambda _id: pkg

    def tearDown(self):
        status._pkg.resolve_pkg_dir, status._pkg.load = self._orig

    def test_a_simulation_running_an_older_recipe_shows_drift(self):
        drift = status._config_drift("omega", "omega-main-simulation", {"omega-main-simulation": {"dslPreset": "old"}},
                                     simulation=True)
        self.assertEqual(drift["diffs"], [{"field": "dsl_preset", "running": "old", "on_disk": "new"}])

    def test_live_drift_is_unchanged(self):
        drift = status._config_drift("omega", "omega-main", {"omega-main": {"dslPreset": "old"}})
        self.assertEqual(drift["diffs"][0]["field"], "dsl_preset")
        self.assertIsNone(status._config_drift("omega", "omega-main-simulation",
                                               {"omega-main-simulation": {"dslPreset": "old"}}))


class UpdateFollowsTheDriftCommand(unittest.TestCase):
    """The command `status.py --simulation` prints for a drifted simulation must reach the update verb,
    aimed at the simulation's runtime."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "main").mkdir()
        inst = types.SimpleNamespace(name="main", runtime_name="omega-main",
                                     runtime_path=self.tmp / "main" / "runtime.yaml")
        self.pkg = types.SimpleNamespace(id="omega", dir=self.tmp, instances=[inst])
        self.calls = []
        self._orig = (status.build, deploy.local_pkg, deploy.full_validate, deploy.outside_durable_root,
                      _cli.run_cli)
        row = {"package": "omega", "is_pkg": True, "name": "omega", "name_source": "strategyName",
               "strategyId": SIMULATION_ID, "wallet": SIMULATION_WALLET, "status": "ACTIVE", "funded": "100",
               "positions": None, "paused": None, "reason": None, "runtime": "omega-main-simulation",
               "health": "running",
               "config_drift": {"diffs": [{"field": "dsl_preset", "running": "old", "on_disk": "new"}],
                                "dir": str(self.tmp / "main"), "package_dir": str(self.tmp)}}
        status.build = lambda *a, **k: ([row], [], True)
        deploy.local_pkg = lambda arg: self.pkg
        deploy.full_validate = lambda pkg: []
        deploy.outside_durable_root = lambda pkg, cmd, simulation=False, **k: None
        _cli.run_cli = lambda argv, timeout=60: (self.calls.append(list(argv)) or (0, "", ""))

    def tearDown(self):
        (status.build, deploy.local_pkg, deploy.full_validate, deploy.outside_durable_root,
         _cli.run_cli) = self._orig

    def test_the_printed_command_updates_the_simulation(self):
        import shlex
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status.main(["status.py", "omega", "--simulation"])
        line = next(ln for ln in out.getvalue().splitlines() if "deploy.py update" in ln)
        cmd = shlex.split(line.split("(add --apply")[0].split("python3 ", 1)[1])
        self.assertEqual(cmd[0], "deploy.py")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                deploy.main(cmd)
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(self.calls, [["senpi", "update", str(self.tmp / "main"),
                                       "--id", "omega-main-simulation"]])

    SIM_WALLET = "0x0000000000000000" + "1762500000000" + "0a1b2c3d4e5"
    SIM_READ = r"status\.py omega --(paper|simulation)\b"
    ANY_WORLD_FLAG = r"--(paper|simulation)\b"

    def test_an_applied_update_by_simulation_wallet_says_to_read_the_simulation(self):
        err = self._apply(self.SIM_WALLET, selector="--address")
        self.assertRegex(err, self.SIM_READ)

    def test_an_applied_update_by_live_wallet_says_to_read_the_live_strategy(self):
        err = self._apply(LIVE_WALLET, selector="--address")
        self.assertIn("status.py omega ", err)
        self.assertNotRegex(err, self.ANY_WORLD_FLAG)

    def test_a_live_id_beside_a_simulation_address_says_to_read_the_live_strategy(self):
        err = self._apply("omega-main", extra=["--address", self.SIM_WALLET])
        self.assertIn("status.py omega ", err)
        self.assertNotRegex(err, self.ANY_WORLD_FLAG)
        self.assertEqual(self.calls[-1][-3:], ["--id", "omega-main", "--apply"])

    def _apply(self, runtime_id, selector="--id", extra=()):
        (self.tmp / "main" / deploy.PROOF_FILE).write_text("{}")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                deploy.main(["deploy.py", "update", str(self.tmp), selector, runtime_id, *extra, "--apply"])
        self.assertEqual(raised.exception.code, 0, err.getvalue())
        return err.getvalue()

    def test_an_applied_simulation_update_says_to_read_the_simulation(self):
        err = self._apply(_cli.runtime_id_for("omega", "main", True))
        self.assertRegex(err, self.SIM_READ)

    def test_an_applied_live_update_says_to_read_the_live_strategy(self):
        err = self._apply("omega-main")
        self.assertIn("status.py omega ", err)
        self.assertNotRegex(err, self.ANY_WORLD_FLAG)

    def test_an_unknown_runtime_id_is_still_refused(self):
        inst, _sel, refusal = deploy.update_target(self.pkg, "omega-other-simulation")
        self.assertIsNone(inst)
        self.assertIn("no instance of this package runs as", refusal)


class UpdateIdNamingTwoInstances(unittest.TestCase):
    """Instances `main` and `main-simulation`: `omega-main-simulation` is both main's simulation and
    main-simulation's live runtime. The box holds one runtime by that id; its wallet says which."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        insts = []
        for name in ("main", "main-simulation"):
            (self.tmp / name).mkdir()
            insts.append(types.SimpleNamespace(name=name, runtime_name=f"omega-{name}",
                                               runtime_path=self.tmp / name / "runtime.yaml"))
        self.pkg = types.SimpleNamespace(id="omega", dir=self.tmp, instances=insts)
        self.inventory = None
        orig = _cli.list_runtimes_or_none
        _cli.list_runtimes_or_none = lambda why=None: self.inventory
        self.addCleanup(setattr, _cli, "list_runtimes_or_none", orig)

    def test_a_simulation_runtime_updates_from_its_own_instance(self):
        self.inventory = [{"name": "omega-main-simulation", "wallet": SIMULATION_WALLET}]
        inst, sel, refusal = deploy.update_target(self.pkg, "omega-main-simulation")
        self.assertIsNone(refusal)
        self.assertEqual(inst.name, "main")
        self.assertEqual(sel, ["--id", "omega-main-simulation"])

    def test_a_live_runtime_updates_from_its_own_instance(self):
        self.inventory = [{"name": "omega-main-simulation", "wallet": LIVE_WALLET}]
        inst, _sel, refusal = deploy.update_target(self.pkg, "omega-main-simulation")
        self.assertIsNone(refusal)
        self.assertEqual(inst.name, "main-simulation")

    def test_an_unreadable_inventory_refuses(self):
        inst, _sel, refusal = deploy.update_target(self.pkg, "omega-main-simulation")
        self.assertIsNone(inst)
        self.assertIn("main", refusal)
        self.assertIn("main-simulation", refusal)
        self.assertIn("Nothing was changed", refusal)

    def test_a_runtime_not_on_the_box_refuses(self):
        self.inventory = [{"name": "omega-main", "wallet": LIVE_WALLET}]
        inst, _sel, refusal = deploy.update_target(self.pkg, "omega-main-simulation")
        self.assertIsNone(inst)
        self.assertIn("Nothing was changed", refusal)

    def test_a_runtime_with_no_wallet_on_record_refuses(self):
        self.inventory = [{"name": "omega-main-simulation", "wallet": ""}]
        inst, _sel, refusal = deploy.update_target(self.pkg, "omega-main-simulation")
        self.assertIsNone(inst)
        self.assertIn("no wallet", refusal)
        self.assertIn("Nothing was changed", refusal)

    def test_a_killed_inventory_read_is_the_out_of_memory_stop(self):
        def killed(why=None):
            if why is not None:
                why.append(_cli.Cause("senpi.listRuntimes: error: killed", -9))
            return None
        _cli.list_runtimes_or_none = killed
        inst, _sel, refusal = deploy.update_target(self.pkg, "omega-main-simulation")
        self.assertIsNone(inst)
        self.assertTrue(_cli.sigkilled(refusal))

        orig = (deploy.local_pkg, deploy.full_validate, deploy.outside_durable_root, _cli.run_cli)
        self.addCleanup(lambda: (setattr(deploy, "local_pkg", orig[0]), setattr(deploy, "full_validate", orig[1]),
                                 setattr(deploy, "outside_durable_root", orig[2]), setattr(_cli, "run_cli", orig[3])))
        deploy.local_pkg = lambda arg: self.pkg
        deploy.full_validate = lambda pkg: []
        deploy.outside_durable_root = lambda pkg, cmd, simulation=False: None
        sent = []
        _cli.run_cli = lambda argv, timeout=60: (sent.append(argv) or (0, "", ""))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                deploy.main(["deploy.py", "update", "omega", "--id", "omega-main-simulation", "--json"])
        self.assertEqual(raised.exception.code, _cli.EXIT_OOM)
        self.assertIs(json.loads(out.getvalue())["oom"], True)
        self.assertIn("out of memory", err.getvalue())
        self.assertEqual(sent, [], "no update is sent after the kill")

    def test_an_id_only_one_instance_runs_as_reads_no_inventory(self):
        def boom(why=None):
            raise AssertionError("inventory read for an unambiguous id")
        _cli.list_runtimes_or_none = boom
        inst, _sel, refusal = deploy.update_target(self.pkg, "omega-main-simulation-simulation")
        self.assertIsNone(refusal)
        self.assertEqual(inst.name, "main-simulation")


class DeploySimulation(unittest.TestCase):
    def setUp(self):
        self._orig = _cli.gateway_call
        self.calls = []
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.root = Path(tmp)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))
        self.answer = _gw("ok", payload={"deployId": "dpl-1"})

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append((method, dict(params or {})))
            return self.answer
        _cli.gateway_call = _gateway

    def tearDown(self):
        _cli.gateway_call = self._orig

    def _args(self, simulation, **kw):
        base = dict(budget=30.0, decision_model=None, tick_wait=None, max_wait=60, json=False,
                    dry_run=False, request_ids=True, cmd="create", simulation=simulation)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _pkg(self):
        return types.SimpleNamespace(dir=str(self.root / "omega"), id="omega", version="1.0.0", instances=[])

    def _start(self, a):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                return deploy.start_deploy(self._pkg(), a, lambda m: None), err.getvalue(), None
            except SystemExit as e:
                return None, err.getvalue(), e.code

    def test_create_with_simulation_calls_start_simulation_with_the_same_params(self):
        deploy_id, _err, _code = self._start(self._args(True))
        self.assertEqual(deploy_id, "dpl-1")
        method, params = self.calls[0]
        self.assertEqual(method, "senpi.deploy.startSimulation")
        self.assertEqual(params["budget"], 30.0)
        self.assertEqual(params["packageDir"], str(self.root / "omega"))
        self.assertNotIn("simulation", params)

    def test_create_without_simulation_calls_start(self):
        self._start(self._args(False))
        self.assertEqual(self.calls[0][0], "senpi.deploy.start")

    def test_dry_run_plans_start_simulation(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            deploy.start_deploy(self._pkg(), self._args(True, dry_run=True), lambda m: None)
        self.assertIn("senpi.deploy.startSimulation", buf.getvalue())
        self.assertEqual([c for c in self.calls if c[0] != "senpi.getHealthStatus"], [])

    def test_a_runtime_without_start_simulation_refuses_and_never_steers_to_live(self):
        self.answer = _gw("unknown_method", message="unknown method: senpi.deploy.startSimulation")
        _id, err, code = self._start(self._args(True))
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("no simulation deploy", err)
        self.assertIn("Do not re-run without --simulation", err)

    def test_a_simulation_start_never_reuses_a_live_open_request(self):
        live_rid = "dpl-req-20261107T000000Z-aaaaaaaa"
        _journal.write(_journal.key("omega", False),
                       {"packageId": "omega", "requestId": live_rid, "sentAt": "2026-11-07T00:00:00Z",
                        "verb": "create", "paramsKey": {"packageDir": str(self.root / "omega"), "budget": 30.0},
                        "resolved": None})
        self._start(self._args(True))
        self.assertNotEqual(self.calls[0][1].get("requestId"), live_rid)
        self.assertIsNone(_journal.read(_journal.key("omega", False))["resolved"])
        self.assertEqual(_journal.read(_journal.key("omega", True))["resolved"], "dpl-1")

    def test_the_outside_root_refusal_keeps_simulation_in_its_rerun(self):
        pkg = types.SimpleNamespace(dir="/elsewhere/omega", id="omega")
        self.assertIn("create /", deploy.outside_durable_root(pkg, "create"))
        text = deploy.outside_durable_root(pkg, "create", simulation=True)
        self.assertIn(f"create {self.root.resolve() / 'omega'} --simulation", text)

    def test_the_unnamed_template_refusal_keeps_simulation_in_its_rerun(self):
        orig = deploy._username
        deploy._username = lambda: None
        pkg = types.SimpleNamespace(id="omega", manifest={}, catalog={"tier": "core"})
        a = types.SimpleNamespace(owner=None, name=None, package="omega", cmd="create", simulation=True)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                deploy._fork_for_deploy(pkg, a, lambda m: None)
        finally:
            deploy._username = orig
        self.assertIn('--budget <usd> --simulation', err.getvalue())

    def _lost_start_reading(self, job_meta, simulation):
        fresh = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 1))
        job = {"meta": {"deployId": "dpl-other", "packageId": "omega", "startedAt": fresh, **job_meta},
               "state": {"status": "running"}}

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append((method, dict(params or {})))
            return _gw("timeout") if method.startswith("senpi.deploy.start") else _gw("ok", payload=job)
        _cli.gateway_call = _gateway
        orig_sleep = deploy.time.sleep
        deploy.time.sleep = lambda s: None
        try:
            return self._start(self._args(simulation, request_ids=False))[0]
        finally:
            deploy.time.sleep = orig_sleep

    def test_a_lost_simulation_start_never_adopts_a_live_job(self):
        self.assertIsNone(self._lost_start_reading({}, simulation=True))

    def test_a_lost_live_start_never_adopts_a_simulation_job(self):
        self.assertIsNone(self._lost_start_reading({"simulation": True}, simulation=False))

    def test_a_lost_simulation_start_adopts_its_own_world_job(self):
        self.assertEqual(self._lost_start_reading({"simulation": True}, simulation=True), "dpl-other")

    def test_the_dry_run_json_refusal_keeps_simulation_in_its_rerun(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit):
            deploy.main(["deploy.py", "create", "omega", "--budget", "30", "--simulation", "--dry-run", "--json"])
        text = err.getvalue()
        self.assertIn("create omega --simulation", text)
        self.assertIn("senpi.deploy.startSimulation", text)
        self.assertNotIn("senpi.deploy.start ", text)

    def test_the_parser_takes_simulation_on_create_and_runtime(self):
        seen = []
        orig = deploy.run_deploy, deploy.ensure_pkg, deploy._fork_for_deploy, deploy.outside_durable_root, \
            deploy.full_validate, deploy.start_request_ids_supported
        pkg = self._pkg()
        deploy.run_deploy = lambda p, a, log: seen.append((a.cmd, a.simulation)) or 0
        deploy.ensure_pkg = lambda *a, **k: pkg
        deploy._fork_for_deploy = lambda p, a, log: p
        deploy.outside_durable_root = lambda p, cmd, simulation=False, **k: None
        deploy.full_validate = lambda p: []
        deploy.start_request_ids_supported = lambda log, as_json=False: True
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        old_root = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old_root) if old_root is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))
        deploy._write_plan_record("omega", [30.0], [], False)  # the user saw omega quoted at $30 by `plan`,
        rec = deploy._plan_record_path("omega")            # in a turn before this one
        rec.write_text(json.dumps({**json.loads(rec.read_text()), "at": time.time() - deploy.PLAN_MIN_GAP_S - 1}))
        try:
            for argv in (["create", "omega", "--budget", "30", "--simulation", "--user-said-yes"],
                         ["runtime", "omega", "--budget", "30", "--simulation", "--user-said-yes"],
                         ["create", "omega", "--budget", "30"]):
                with self.assertRaises(SystemExit):
                    deploy.main(["deploy.py", *argv])
        finally:
            (deploy.run_deploy, deploy.ensure_pkg, deploy._fork_for_deploy, deploy.outside_durable_root,
             deploy.full_validate, deploy.start_request_ids_supported) = orig
        self.assertEqual(seen, [("create", True), ("runtime", True), ("create", False)])


class VerifySeesSimulations(unittest.TestCase):
    """`verify` reads live strategies only. When the package has no live wallet but runs as a
    simulation, it must say so and name no live `create`."""

    def setUp(self):
        self._orig = (deploy.verify_reads, deploy.deploy_job_facts, deploy.MCPClient)
        deploy.verify_reads = lambda pkg: ([], [], {})
        deploy.deploy_job_facts = lambda pkg_id: ([], False)
        self.mcp = FakeMCP(simulation=[_simulation_row()])
        deploy.MCPClient = lambda *a, **k: self.mcp

    def tearDown(self):
        deploy.verify_reads, deploy.deploy_job_facts, deploy.MCPClient = self._orig

    def _verify(self):
        inst = types.SimpleNamespace(name="main", runtime_name="omega-main")
        pkg = types.SimpleNamespace(id="omega", instances=[inst])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = deploy.cmd_verify(pkg, types.SimpleNamespace(json=False))
        return code, err.getvalue()

    def test_a_simulation_only_package_names_no_live_create(self):
        code, err = self._verify()
        self.assertEqual(code, deploy.VERIFY_NOT)
        self.assertNotIn("create omega", err)
        self.assertIn("simulation", err)
        self.assertIn("status.py omega --simulation", err)
        self.assertEqual([c.get("simulation") for c in self.mcp.lists], [True])

    def test_each_instance_row_says_whether_that_instance_is_simulated(self):
        self.mcp.simulation = [_simulation_row(skill="omega-swing")]
        self.mcp.simulation[0]["strategyMetadata"] = {"skillName": "omega"}
        insts = [types.SimpleNamespace(name=n, runtime_name=f"omega-{n}") for n in ("swing", "scalp")]
        pkg = types.SimpleNamespace(id="omega", instances=insts)
        sims, unread = deploy.package_simulations(pkg)
        self.assertIsNone(unread)
        rows = {i.name: deploy.verify_instance(pkg, i, [], [], {}, simulations=sims) for i in insts}
        self.assertIn("'omega-swing' runs as a SIMULATION", rows["swing"]["issue"])
        self.assertIn("no simulation of it either", rows["scalp"]["issue"])
        self.assertNotIn("runs as a SIMULATION", rows["scalp"]["issue"])
        for row in rows.values():
            self.assertNotIn("create omega", row["next"])
            self.assertIn("status.py omega --simulation", row["next"])

    def test_no_simulation_keeps_the_create_step(self):
        self.mcp.simulation = []
        _code, err = self._verify()
        self.assertIn("create omega --budget <usd>", err)

    def test_simulation_off_on_the_server_names_no_live_create(self):
        # A simulation made while simulations were on still exists after it is switched off.
        self.mcp.unavailable = SIMULATION_OFF
        _code, err = self._verify()
        self.assertNotIn("create omega", err)
        self.assertIn("status.py omega --simulation", err)

    def test_no_simulation_router_keeps_todays_step(self):
        self.mcp.router = False
        _code, err = self._verify()
        self.assertIn("create omega --budget <usd>", err)

    def test_a_simulation_service_outage_names_no_live_create(self):
        self.mcp.unavailable = True
        _code, err = self._verify()
        self.assertNotIn("create omega", err)
        self.assertIn("could not be read", err)
        self.assertIn("status.py omega --simulation", err)

    def test_a_router_that_is_off_names_no_live_create_either(self):
        self.mcp.unavailable = ROUTER_OFF
        _code, err = self._verify()
        self.assertNotIn("create omega", err)
        self.assertIn("status.py omega --simulation", err)

    def test_a_timed_out_simulation_lookup_names_no_live_create(self):
        class Timeout(FakeMCP):
            def mcp_call(self, name, timeout=None, **kw):
                if kw.get("simulation") is True:
                    raise TimeoutError("timed out")
                return super().mcp_call(name, timeout=timeout, **kw)
        self.mcp = Timeout(simulation=[_simulation_row()])
        _code, err = self._verify()
        self.assertNotIn("create omega", err)
        self.assertIn("could not be read", err)
        self.assertIn("status.py omega --simulation", err)

    def test_a_simulation_reply_with_no_list_names_no_live_create(self):
        class Drift(FakeMCP):
            def mcp_call(self, name, timeout=None, **kw):
                if kw.get("simulation") is True:
                    return {"success": True, "data": {"simulation_routing": True, "oops": []}}
                return super().mcp_call(name, timeout=timeout, **kw)
        self.mcp = Drift()
        _code, err = self._verify()
        self.assertNotIn("create omega", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
