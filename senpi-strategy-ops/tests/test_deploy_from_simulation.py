#!/usr/bin/env python3
"""`deploy.py create --from-simulation <id>`: a live deploy that carries the simulation it came from.

    python3 -m pytest senpi-strategy-ops/tests/test_deploy_from_simulation.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli      # noqa: E402
import _journal  # noqa: E402
import deploy    # noqa: E402

SIMULATION_ID = "simulation-11111111-2222-4333-8444-555555555555"


def _gw(kind, payload=None):
    return {"kind": kind, "payload": payload, "message": None, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


class FromSimulation(unittest.TestCase):
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
        self.answer = _gw("timeout")
        self.health = _gw("ok", payload={"deploy": {"capabilities": {"deployStartRequestId": True,
                                                                     "simulationYesGate": True}}})

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            if method == "senpi.getHealthStatus":
                return self.health
            self.calls.append((method, dict(params or {})))
            return self.answer
        _cli.gateway_call = _gateway

    def tearDown(self):
        _cli.gateway_call = self._orig

    def _args(self, **kw):
        base = dict(budget=30.0, decision_model=None, tick_wait=None, max_wait=60, json=False,
                    dry_run=False, request_ids=True, cmd="create", simulation=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _pkg(self):
        return types.SimpleNamespace(dir=str(self.root / "omega"), id="omega", version="1.0.0", instances=[])

    def _start(self, a):
        self.answer = _gw("ok", payload={"deployId": "dpl-1"})
        with contextlib.redirect_stderr(io.StringIO()):
            return deploy.start_deploy(self._pkg(), a, lambda m: None)

    def _main(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            try:
                deploy.main(["deploy.py", *argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, err.getvalue()

    def test_the_live_start_carries_from_simulation_id(self):
        self._start(self._args(from_simulation=SIMULATION_ID))
        method, params = self.calls[0]
        self.assertEqual(method, "senpi.deploy.start")
        self.assertEqual(params["fromSimulationId"], SIMULATION_ID)
        self.assertEqual(params["budget"], 30.0)

    def test_a_start_without_it_sends_no_such_param(self):
        self._start(self._args())
        self.assertNotIn("fromSimulationId", self.calls[0][1])

    def test_a_retry_after_a_lost_start_sends_it_again_under_the_same_request_id(self):
        a = self._args(from_simulation=SIMULATION_ID)
        self.answer = _gw("timeout")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            try:
                deploy.start_deploy(self._pkg(), a, lambda m: None)
            except SystemExit:
                pass
        self._start(a)
        first, second = self.calls[0][1], self.calls[-1][1]
        self.assertEqual(second["fromSimulationId"], SIMULATION_ID)
        self.assertEqual(second["requestId"], first["requestId"])

    def test_the_dry_run_plans_it(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            deploy.start_deploy(self._pkg(), self._args(from_simulation=SIMULATION_ID, dry_run=True),
                                lambda m: None)
        self.assertIn("fromSimulationId", buf.getvalue())
        self.assertIn(SIMULATION_ID, buf.getvalue())
        self.assertEqual(self.calls, [])

    def test_a_malformed_id_is_refused_before_any_gateway_call(self):
        for bad in ("simulation-abc", "11111111-2222-4333-8444-555555555555", "omega",
                    SIMULATION_ID + "x", "paper-11111111-2222-4333-8444-555555555555"):
            code, err = self._main("create", "omega", "--budget", "30", "--from-simulation", bad)
            self.assertEqual(code, deploy.EXIT_CODES["refused"], bad)
            self.assertIn("--from-simulation", err)
            self.assertIn("Nothing was started", err)
        self.assertEqual(self.calls, [])

    def test_it_is_refused_together_with_simulation(self):
        code, err = self._main("create", "omega", "--budget", "30", "--simulation",
                               "--from-simulation", SIMULATION_ID)
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("--simulation", err)
        self.assertIn("--from-simulation", err)
        self.assertIn("Nothing was started", err)
        self.assertEqual(self.calls, [])

    def test_runtime_does_not_take_it(self):
        code, _err = self._main("runtime", "omega", "--from-simulation", SIMULATION_ID)
        self.assertEqual(code, 2)
        self.assertEqual(self.calls, [])

    def _refused_start(self, a):
        self.answer = _gw("ok", payload={"deployId": "dpl-1"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as stop:
            deploy.start_deploy(self._pkg(), a, lambda m: None)
        return stop.exception.code, err.getvalue()

    def test_a_runtime_that_cannot_take_a_simulation_live_gets_nothing_dispatched(self):
        # 3.0.301 advertises request ids but not the take-live handling: it would ignore
        # fromSimulationId and fund the named package at its own settings.
        self.health = _gw("ok", payload={"deploy": {"capabilities": {"deployStartRequestId": True}}})
        code, err = self._refused_start(self._args(from_simulation=SIMULATION_ID))
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertEqual(self.calls, [])
        self.assertIn("Nothing was started", err)
        self.assertIn("default settings", err)

    def test_a_runtime_with_no_capabilities_at_all_gets_nothing_dispatched(self):
        self.health = _gw("ok", payload={"statuses": []})
        code, _err = self._refused_start(self._args(from_simulation=SIMULATION_ID))
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertEqual(self.calls, [])

    def test_an_unreadable_capability_read_dispatches_nothing(self):
        self.health = _gw("timeout")
        code, err = self._refused_start(self._args(from_simulation=SIMULATION_ID))
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertEqual(self.calls, [])
        self.assertIn("Nothing was started", err)

    def test_a_live_deploy_without_a_simulation_needs_no_such_capability(self):
        self.health = _gw("ok", payload={"deploy": {"capabilities": {"deployStartRequestId": True}}})
        self._start(self._args())
        self.assertEqual(self.calls[0][0], "senpi.deploy.start")

    def test_a_changed_simulation_id_is_a_changed_request(self):
        other = "simulation-99999999-2222-4333-8444-555555555555"
        self.assertNotEqual(deploy._params_key({"packageDir": "/p", "budget": 30.0, "fromSimulationId": other}),
                            deploy._params_key({"packageDir": "/p", "budget": 30.0,
                                                "fromSimulationId": SIMULATION_ID}))
        self.assertEqual(deploy._params_key({"packageDir": "/p", "budget": 30.0}),
                         {"packageDir": "/p", "budget": 30.0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
