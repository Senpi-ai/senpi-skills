#!/usr/bin/env python3
"""`deploy.py call <method> --params '<json>'`: the simulation and package reads the skill text names,
through `openclaw gateway call`, which answers on a plugin-mode box as well as a daemon one. The
`senpi` command exists only where the runtime daemon is installed.

    python3 -m pytest senpi-strategy-ops/tests/test_deploy_call.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import re
import sys
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS / "scripts"))

import _cli    # noqa: E402
import deploy  # noqa: E402

SKILL_TEXTS = [OPS / "SKILL.md", OPS.parent / "senpi-strategy-author" / "SKILL.md"]


def _gw(kind, payload=None, message=None, rc=0):
    return {"kind": kind, "payload": payload, "message": message, "code": None, "rc": rc, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


class Call(unittest.TestCase):
    def setUp(self):
        self._orig = _cli.gateway_call
        self.calls = []
        self.answer = _gw("ok", payload={"strategyId": "hawk", "settings": {"leverage": 3}})

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append((method, dict(params or {}), rpc_timeout_ms))
            return self.answer
        _cli.gateway_call = _gateway

    def tearDown(self):
        _cli.gateway_call = self._orig

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                deploy.main(["deploy.py", *argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_a_package_read_goes_through_the_gateway_and_prints_its_answer(self):
        params = {"simulationId": "simulation-11111111-2222-4333-8444-555555555555"}
        code, out, _ = self._main("call", "senpi.packages.get", "--params", json.dumps(params))
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[0][:2], ("senpi.packages.get", params))
        self.assertEqual(json.loads(out), {"strategyId": "hawk", "settings": {"leverage": 3}})

    def test_every_method_the_skill_names_is_callable(self):
        for method in ("senpi.packages.list", "senpi.packages.get", "senpi.simulations.startBatch",
                       "senpi.simulations.batchStatus"):
            code, _, err = self._main("call", method, "--params", "{}")
            self.assertEqual(code, 0, (method, err))
        self.assertEqual([c[0] for c in self.calls], ["senpi.packages.list", "senpi.packages.get",
                                                      "senpi.simulations.startBatch",
                                                      "senpi.simulations.batchStatus"])

    def test_a_batch_start_gets_the_start_timeout_not_the_read_one(self):
        self._main("call", "senpi.simulations.startBatch", "--params", '{"requestId": "r1"}')
        self.assertEqual(self.calls[0][2], deploy.START_RPC_MS)

    def test_a_deploy_start_is_refused_with_nothing_sent(self):
        for method in ("senpi.deploy.start", "senpi.deploy.startSimulation", "senpi.deleteRuntime"):
            code, _, err = self._main("call", method, "--params", "{}")
            self.assertEqual(code, deploy.EXIT_CODES["refused"], method)
            self.assertIn("Nothing was sent", err)
        self.assertEqual(self.calls, [])

    def test_params_that_are_not_a_json_object_are_refused_with_nothing_sent(self):
        for bad in ("[1]", "{nope", '"x"'):
            code, _, err = self._main("call", "senpi.packages.get", "--params", bad)
            self.assertEqual(code, deploy.EXIT_CODES["refused"], bad)
            self.assertIn("Nothing was sent", err)
        self.assertEqual(self.calls, [])

    def test_a_refusal_is_relayed_with_its_code(self):
        self.answer = _gw("refused", message="[E_NEEDS_USER_YES] Nothing was started.")
        code, _, err = self._main("call", "senpi.simulations.startBatch", "--params", "{}")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NEEDS_USER_YES]", err)

    def test_a_runtime_without_the_method_says_so(self):
        self.answer = _gw("unknown_method", message="unknown method: senpi.simulations.startBatch")
        code, _, err = self._main("call", "senpi.simulations.startBatch", "--params", "{}")
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertIn("does not have", err)


class SkillText(unittest.TestCase):
    def test_no_skill_tells_the_agent_to_run_senpi_call(self):
        for path in SKILL_TEXTS:
            hits = re.findall(r"`senpi call [^`]*`", path.read_text())
            self.assertEqual(hits, [], f"{path.parent.name}/SKILL.md: `senpi` exists only on daemon boxes")

    def test_the_ops_skill_names_the_call_verb_for_each_method(self):
        text = SKILL_TEXTS[0].read_text()
        for method in ("senpi.packages.list", "senpi.packages.get", "senpi.simulations.startBatch"):
            self.assertIn(f"deploy.py call {method}", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
