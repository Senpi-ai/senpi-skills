#!/usr/bin/env python3
"""`deploy.py plan` quotes a strategy and its variations as simulations and prints the test plan the
agent relays as given: each variation changes one setting, the plan carries the quoted credits an hour
on every budget it offers, ends with the simulate-or-live question, and starts nothing.

    python3 -m pytest senpi-strategy-ops/tests/test_deploy_plan.py
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

BATCH = "senpi.simulations.startBatch"
START = "senpi.deploy.startSimulation"
HEALTH = "senpi.getHealthStatus"
QUESTION = "Simulate these first, or take it live as it is now?"


def _answer(kind, payload=None, message=None):
    return {"kind": kind, "payload": payload, "message": message, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


def _batch_quote(params, per_hour=10, refuse=None):
    rows = []
    for v in params["variants"]:
        if v["label"] == refuse:
            rows.append({"label": v["label"], "status": "would_refuse", "detail": "unknown setting key"})
        else:
            rows.append({"label": v["label"], "status": "would_start", "budgetUsd": params["budgetUsd"],
                         "creditsPerHour": per_hour, "detail": "would be validated and started"})
    return _answer("ok", {"requestId": params["requestId"], "dryRun": True, "budgetUsd": params["budgetUsd"],
                          "rows": rows, "creditsPerHourAfter": per_hour * len(rows), "box": {}})


class Plan(unittest.TestCase):
    def setUp(self):
        self._orig = _cli.gateway_call
        self.calls = []
        self.batch = lambda params: _batch_quote(params)
        self.start = _answer("ok", {"dryRun": True, "budgetUsd": 1000, "creditsPerHour": 10,
                                    "creditsPerDay": 240, "creditsPerHourAfter": 25})
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
                return _answer("ok", {"deploy": {"capabilities": {"simulationYesGate": True}}})
            if method == BATCH:
                return self.batch(params)
            if method == START:
                return self.start
            raise AssertionError(f"unexpected gateway call {method}")
        _cli.gateway_call = _gateway

    def tearDown(self):
        _cli.gateway_call = self._orig

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                deploy._main(["deploy.py", "plan", *argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    VARIANTS = ("--variant", "pickier|scanner.inputs.minScore=7|pickier entries: score 7 instead of 5",
                "--variant", "wider-stop|exit.dsl_preset.phase1.max_loss_pct=9|a looser stop: 9% instead of 6%")

    def _catalog(self, *extra):
        return self._run("hawk", "--design", "buys breakouts with a 6% stop", "--trades", "a few times a week",
                         *self.VARIANTS, *extra)

    def test_catalog_plan_lists_each_variation_with_its_cost_and_ends_on_the_question(self):
        code, out, err = self._catalog()
        self.assertEqual(code, 0, err)
        self.assertIn("1. As designed: buys breakouts with a 6% stop", out)
        self.assertIn("2. pickier: pickier entries: score 7 instead of 5 (scanner.inputs.minScore = 7)", out)
        self.assertIn("3. wider-stop: a looser stop", out)
        self.assertIn("How often it should trade: a few times a week", out)
        self.assertIn("Budget: $1,000 of virtual money each, $3,000 in all.", out)
        self.assertIn("Cost: 10 credits an hour each, 30 an hour for all 3 (720 a day).", out)
        self.assertEqual(out.strip().splitlines()[-1], QUESTION)

    def test_the_plan_starts_nothing(self):
        self._catalog()
        self.assertTrue(self.calls)
        for method, params in self.calls:
            self.assertNotIn("userSaidYes", params)
            if method in (BATCH, START):
                self.assertIs(params.get("dryRun"), True)

    def test_one_quote_carries_the_design_and_every_variation(self):
        self._catalog()
        batch = [p for m, p in self.calls if m == BATCH]
        self.assertEqual(len(batch), 1)
        self.assertEqual([v["settings"] for v in batch[0]["variants"]],
                         [{}, {"scanner.inputs.minScore": 7}, {"exit.dsl_preset.phase1.max_loss_pct": 9}])
        self.assertEqual(batch[0]["strategy"], "hawk")

    def test_a_variation_changing_two_settings_is_refused_before_any_quote(self):
        code, out, err = self._run("hawk", "--design", "d", "--trades", "t",
                                   "--variant", "both|scanner.inputs.minScore=7,leverage=3|two things",
                                   "--variant", "ok|leverage=3|half leverage")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("exactly one setting", err)
        self.assertEqual(out, "")
        self.assertEqual(self.calls, [])

    def test_one_simulation_the_user_asked_for_is_quoted_on_every_budget_choice(self):
        code, out, err = self._run("hawk", "--design", "buys breakouts with a 6% stop", "--trades", "a few times a week",
                                   "--budget-choices", "100,1000")
        self.assertEqual(code, 0, err)
        self.assertIn("Simulation of hawk, on virtual money (nothing real moves): buys breakouts", out)
        self.assertIn("  - $100: 10 credits an hour (240 a day)", out)
        self.assertIn("  - $1,000: 10 credits an hour (240 a day)", out)
        self.assertIn("Picking a budget starts it.", out)
        self.assertEqual(out.strip().splitlines()[-1], "Which budget?")
        self.assertEqual([v["settings"] for m, p in self.calls if m == BATCH for v in p["variants"]], [{}, {}])

    def test_one_simulation_at_the_default_budget_asks_to_start(self):
        code, out, err = self._run("hawk", "--design", "d", "--trades", "t")
        self.assertEqual(code, 0, err)
        self.assertIn("Budget: $1,000 of virtual money.", out)
        self.assertIn("Cost: 10 credits an hour (240 a day).", out)
        self.assertEqual(out.strip().splitlines()[-1], "Start it?")

    def test_fewer_than_two_variations_is_not_a_test_plan(self):
        code, out, err = self._run("hawk", "--design", "d", "--trades", "t",
                                   "--variant", "ok|leverage=3|half leverage")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("two to four variations", err)

    def test_a_refused_variation_prints_no_plan(self):
        self.batch = lambda params: _batch_quote(params, refuse="pickier")
        code, out, err = self._catalog()
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertEqual(out, "")
        self.assertIn("pickier: unknown setting key", err)

    def test_an_unquotable_plan_prints_no_plan_and_no_estimate(self):
        self.batch = lambda params: _answer("error", None, "simulation-service is temporarily unavailable")
        code, out, err = self._catalog()
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertEqual(out, "")
        self.assertIn("never estimate", err)

    def test_budget_choices_each_carry_their_quoted_cost(self):
        code, out, err = self._catalog("--budget-choices", "100,500,1000")
        self.assertEqual(code, 0, err)
        self.assertIn("  - $100 each: 10 credits an hour each, 30 an hour for all 3 (720 a day)", out)
        self.assertIn("  - $500 each: 10 credits an hour each", out)
        self.assertIn("  - $1,000 each: 10 credits an hour each", out)
        self.assertEqual(sorted(p["budgetUsd"] for m, p in self.calls if m == BATCH), [100, 500, 1000])
        self.assertIn("Nothing has started. Picking a budget starts them.", out)
        self.assertEqual(out.strip().splitlines()[-1], "Which budget? Or take it live as it is now?")

    def test_a_named_budget_is_quoted_at_that_budget(self):
        code, out, err = self._catalog("--budget", "250")
        self.assertEqual(code, 0, err)
        self.assertIn("Budget: $250 of virtual money each, $750 in all.", out)
        self.assertEqual([p["budgetUsd"] for m, p in self.calls if m == BATCH], [250])

    def test_the_users_own_package_is_quoted_through_its_simulation_dry_run(self):
        pkg = self.root / "dip-gift"
        pkg.mkdir()
        code, out, err = self._run(str(pkg), "--design", "buys BTC and ETH dips in an uptrend",
                                   "--trades", "1 to 3 times a week", *self.VARIANTS)
        self.assertEqual(code, 0, err)
        starts = [p for m, p in self.calls if m == START]
        self.assertEqual(len(starts), 1)
        self.assertIs(starts[0]["dryRun"], True)
        self.assertIn("Test plan for dip-gift", out)
        self.assertIn("Cost: 10 credits an hour each, 30 an hour for all 3 (720 a day). Everything you run, "
                      "once these start: 45 credits an hour.", out)
        self.assertEqual(out.strip().splitlines()[-1], QUESTION)

    def test_an_unclear_reply_is_not_a_yes(self):
        _, _, err = self._catalog()
        self.assertIn("\"Your call\", \"sounds good\", \"you pick\" and silence are not a yes", err)


if __name__ == "__main__":
    unittest.main()
