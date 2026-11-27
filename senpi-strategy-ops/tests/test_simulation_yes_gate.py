#!/usr/bin/env python3
"""The runtime refuses `senpi.deploy.startSimulation` without `userSaidYes: true` and answers a real quote
for `dryRun: true`. deploy.py forwards the yes only when the user gave it, and quotes a simulation through
`--dry-run` only on a runtime that advertises `deploy.capabilities.simulationYesGate`: an older runtime
ignores `dryRun` and STARTS, so nothing is ever sent to it.

    python3 -m pytest senpi-strategy-ops/tests/test_simulation_yes_gate.py
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
import deploy    # noqa: E402

START = "senpi.deploy.startSimulation"
HEALTH = "senpi.getHealthStatus"
QUOTE = {"dryRun": True, "budgetUsd": 50, "creditsPerHour": 12, "creditsPerDay": 288,
         "creditsPerHourAfter": 30, "creditsLeft": 1500,
         "next": "Show the user this quote, ask whether to start it, and end your turn."}


def _answer(kind, payload=None, message=None):
    return {"kind": kind, "payload": payload, "message": message, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


def _health(gate):
    caps = {"deployStartRequestId": True}
    if gate is not None:
        caps["simulationYesGate"] = gate
    return _answer("ok", {"deploy": {"capabilities": caps}})


class SimulationYesGate(unittest.TestCase):
    def setUp(self):
        self._orig = _cli.gateway_call
        self.calls = []
        self.health = _health(True)
        self.quote = _answer("ok", dict(QUOTE))
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.root = Path(tmp)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append((method, dict(params or {})))
            if method == HEALTH:
                return self.health
            if method == START:
                return self.quote if (params or {}).get("dryRun") else _answer("ok", {"deployId": "dpl-1"})
            raise AssertionError(f"unexpected gateway call {method}")
        _cli.gateway_call = _gateway

    def tearDown(self):
        _cli.gateway_call = self._orig

    def _args(self, **kw):
        base = dict(budget=50.0, decision_model=None, tick_wait=None, max_wait=60, json=False,
                    dry_run=False, request_ids=False, cmd="create", simulation=True, user_said_yes=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _pkg(self):
        return types.SimpleNamespace(dir=str(self.root / "omega"), id="omega", version="1.0.0", instances=[])

    def _run(self, a):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                deploy.start_deploy(self._pkg(), a, lambda m: None)
                code = None
            except SystemExit as e:
                code = e.code
        return code, out.getvalue() + err.getvalue()

    def _starts(self):
        return [p for m, p in self.calls if m == START]

    def test_the_yes_is_forwarded_when_the_user_gave_it(self):
        self._run(self._args(user_said_yes=True))
        self.assertEqual(self._starts()[0].get("userSaidYes"), True)

    def test_no_yes_is_sent_when_the_user_did_not_give_it(self):
        self._run(self._args())
        self.assertNotIn("userSaidYes", self._starts()[0])

    def test_a_dry_run_on_a_gated_runtime_asks_for_the_quote_and_prints_it(self):
        code, text = self._run(self._args(dry_run=True, user_said_yes=True))
        self.assertEqual(code, 0)
        starts = self._starts()
        self.assertEqual(len(starts), 1)
        self.assertIs(starts[0]["dryRun"], True)
        self.assertEqual(starts[0]["budget"], 50.0)
        self.assertNotIn("userSaidYes", starts[0])
        self.assertNotIn("requestId", starts[0])
        for fact in ("12 credits an hour", "288 a day", "30 credits an hour", "1500 credits left",
                     QUOTE["next"], "Nothing was started."):
            self.assertIn(fact, text)

    def test_a_missing_quote_field_is_left_out_not_invented(self):
        self.quote = _answer("ok", {"dryRun": True, "creditsPerHour": 12, "creditsLeft": None})
        code, text = self._run(self._args(dry_run=True))
        self.assertEqual(code, 0)
        self.assertIn("12 credits an hour", text)
        self.assertNotIn("a day", text)
        self.assertNotIn("left", text)
        self.assertIn("Nothing was started.", text)

    def test_a_dry_run_without_the_capability_never_sends_dry_run(self):
        for health in (_health(None), _health(False), _answer("timeout"),
                       _answer("ok", {"deploy": {}})):
            self.calls.clear()
            self.health = health
            code, text = self._run(self._args(dry_run=True))
            self.assertEqual(code, 0)
            self.assertEqual(self._starts(), [], health)
            self.assertIn("planned:", text)
            self.assertIn("cannot start on their agent until its cost can be quoted", text)
            self.assertIn("cannot be quoted", text)

    def test_a_refused_quote_is_relayed_and_nothing_else_is_sent(self):
        self.quote = _answer("refused", message="[INVALID_REQUEST] budget below the minimum")
        code, text = self._run(self._args(dry_run=True))
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertEqual(len(self._starts()), 1)
        self.assertIn("[INVALID_REQUEST] budget below the minimum", text)
        self.assertIn("its cost could not be read just now and nothing was started", text)
        self.assertNotIn("planned:", text)

    def test_an_answer_that_is_not_a_quote_is_called_out(self):
        self.quote = _answer("ok", {"deployId": "dpl-9"})
        code, text = self._run(self._args(dry_run=True))
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertIn("dpl-9", text)
        self.assertIn("deploy.py status", text)

    def test_a_live_dry_run_reads_nothing(self):
        code, text = self._run(self._args(dry_run=True, simulation=False))
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [])
        self.assertIn("senpi.deploy.start ", text)


class SkillTextCarriesTheYes(unittest.TestCase):
    SKILL = " ".join((Path(__file__).resolve().parents[1] / "SKILL.md").read_text().split())

    def test_every_batch_start_sends_the_yes(self):
        self.assertEqual(self.SKILL.count('"dryRun":false`, `"userSaidYes":true'), 1)  # parentId, differing budgets
        self.assertIn("`deploy.py start-plan <catalog id> --user-said-yes`", self.SKILL)
        self.assertIn("`deploy.py start-plan <id> --user-said-yes`", self.SKILL)
        self.assertIn('Never send `startBatch` with `"dryRun":false` yourself', self.SKILL)

    def test_a_local_package_is_quoted_with_plan(self):
        self.assertIn("**A local package** (no catalog id): `deploy.py plan <dir>`, relayed, end the turn", self.SKILL)
        self.assertIn("create <dir> --simulation --user-said-yes", self.SKILL)

    def test_the_method_is_never_called_directly(self):
        self.assertIn("never call `senpi.deploy.startSimulation` yourself", self.SKILL)


if __name__ == "__main__":
    unittest.main()
