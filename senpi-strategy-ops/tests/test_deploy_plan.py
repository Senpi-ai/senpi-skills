#!/usr/bin/env python3
"""`deploy.py plan` quotes a strategy and its variations as simulations and prints the test plan the
agent relays as given: each variation changes one setting, the plan carries the quoted credits an hour
on every budget it offers, ends with the simulate-or-live question, and starts nothing.

    python3 -m pytest senpi-strategy-ops/tests/test_deploy_plan.py
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


RUNTIME_YAML = """name: dip-gift-main
strategy:
  wallet: "${DIP_GIFT_WALLET}"
  default_leverage: 3
scanners:
  - name: dips
    type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    inputs:
      pullbackThreshold: 5.0
      minScore: 5
exit:
  dsl_preset:
    phase1:
      max_loss_pct: 6
"""


def _write_pkg(root, name="dip-gift"):
    pkg = root / name
    pkg.mkdir()
    (pkg / "strategy.yaml").write_text(f"id: {name}\nname: {name}\n")
    (pkg / "runtime.yaml").write_text(RUNTIME_YAML)
    return pkg


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
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "Test plan for hawk, on virtual money (nothing real moves):")
        self.assertEqual(lines[1], "1. As designed: buys breakouts with a 6% stop · trades a few times a week · "
                                   "$1,000 · 10 credits an hour")
        self.assertEqual(lines[2], "2. pickier: pickier entries: score 7 instead of 5 (scanner.inputs.minScore = 7) · "
                                   "trades a few times a week · $1,000 · 10 credits an hour")
        self.assertTrue(lines[3].startswith("3. wider-stop: a looser stop"))
        self.assertTrue(lines[3].endswith(" · trades a few times a week · $1,000 · 10 credits an hour"))
        self.assertEqual(lines[4], "All 3: 30 credits an hour (720 a day). Nothing has started.")
        self.assertEqual(lines[5], QUESTION)
        self.assertEqual(len(lines), 6)

    def test_a_variation_can_name_how_often_it_trades(self):
        code, out, err = self._run("hawk", "--design", "d", "--trades", "a few times a week",
                                   "--variant", "pickier|scanner.inputs.minScore=7|pickier entries|about once a week",
                                   "--variant", "ok|leverage=3|half leverage")
        self.assertEqual(code, 0, err)
        self.assertIn("2. pickier: pickier entries (scanner.inputs.minScore = 7) · trades about once a week · "
                      "$1,000 · 10 credits an hour", out)
        self.assertIn("3. ok: half leverage (leverage = 3) · trades a few times a week ·", out)

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
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "Simulation of hawk, on virtual money (nothing real moves): buys breakouts with a 6% "
                                   "stop · trades a few times a week")
        self.assertEqual(lines[1], "Budget: $100 (10 credits an hour, 240 a day) · $1,000 (10 credits an hour, "
                                   "240 a day). Nothing starts until you pick one.")
        self.assertEqual(lines[2], "Which budget?")
        self.assertEqual(len(lines), 3)
        self.assertEqual([v["settings"] for m, p in self.calls if m == BATCH for v in p["variants"]], [{}, {}])

    def test_one_simulation_at_the_default_budget_asks_to_start(self):
        code, out, err = self._run("hawk", "--design", "d", "--trades", "t")
        self.assertEqual(code, 0, err)
        self.assertIn("Simulation of hawk, on virtual money (nothing real moves): d · trades t · $1,000 · "
                      "10 credits an hour (240 a day). Nothing has started.", out)
        self.assertEqual(out.strip().splitlines()[-1], "Start it?")

    def test_a_variation_that_changes_nothing_is_refused(self):
        code, out, err = self._run("hawk", "--design", "d", "--trades", "t",
                                   "--variant", "baseline|leverage=5|the design as is",
                                   "--variant", "ok|leverage=3|half leverage")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("already simulation 1", err)
        self.assertEqual(self.calls, [])

    def test_one_simulation_needs_no_design_or_trades(self):
        code, out, err = self._run("hawk")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip().splitlines()[0], "Simulation of hawk, on virtual money (nothing real moves): "
                                                      "$1,000 · 10 credits an hour (240 a day). Nothing has started.")

    def test_a_test_plan_needs_design_and_trades(self):
        code, _out, err = self._run("hawk", *self.VARIANTS)
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("--design", err)
        self.assertEqual(self.calls, [])

    def test_the_agent_is_told_a_simulation_needs_no_deposit(self):
        _, _, err = self._catalog()
        self.assertIn("A simulation needs no deposit", err)

    def test_a_variation_set_to_the_designs_own_value_is_refused_whatever_its_label(self):
        pkg = _write_pkg(self.root)
        for setting in ("pullbackThreshold=5.0", "scanner.inputs.pullbackThreshold=5", "strategy.default_leverage=3",
                        "exit.dsl_preset.phase1.max_loss_pct=6"):
            self.calls.clear()
            code, out, err = self._run(str(pkg), "--design", "d", "--trades", "t",
                                       "--variant", f"tighter-floor|{setting}|a tighter floor",
                                       "--variant", "ok|minScore=7|pickier entries")
            self.assertEqual(code, deploy.EXIT_CODES["refused"], setting)
            self.assertIn("is already the design's own value", err)
            self.assertEqual(out, "")
            self.assertEqual(self.calls, [], setting)

    def test_a_real_change_from_the_designs_value_is_quoted(self):
        pkg = _write_pkg(self.root)
        code, out, err = self._run(str(pkg), "--design", "d", "--trades", "t",
                                   "--variant", "tighter-floor|pullbackThreshold=3|a shallower dip",
                                   "--variant", "ok|minScore=7|pickier entries")
        self.assertEqual(code, 0, err)

    def test_one_variation_is_one_simulation_with_that_setting(self):
        code, out, err = self._run("hawk", "--variant", "stop4|exit.dsl_preset.phase1.max_loss_pct=4|a 4% stop")
        self.assertEqual(code, 0, err)
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "Simulation of hawk, on virtual money (nothing real moves): stop4: a 4% stop "
                                   "(exit.dsl_preset.phase1.max_loss_pct = 4) · $1,000 · 10 credits an hour (240 a day). "
                                   "Nothing has started.")
        self.assertEqual(lines[1], "Start it?")
        [quote] = [p for m, p in self.calls if m == BATCH]
        self.assertEqual([v["settings"] for v in quote["variants"]], [{"exit.dsl_preset.phase1.max_loss_pct": 4}])

    def test_more_than_four_variations_is_refused(self):
        many = [x for i in range(5) for x in ("--variant", f"v{i}|leverage={i + 1}|lev {i + 1}")]
        code, _out, err = self._run("hawk", "--design", "d", "--trades", "t", *many)
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
        self.assertIn("2. pickier: pickier entries: score 7 instead of 5 (scanner.inputs.minScore = 7) · trades a "
                      "few times a week\n", out)
        self.assertIn("Budget each: $100 (10 credits an hour each, 30 an hour for all 3, 720 a day) · $500 (10 "
                      "credits an hour each", out)
        self.assertIn("· $1,000 (10 credits an hour each", out)
        self.assertIn("Nothing starts until you pick one.", out)
        self.assertEqual(sorted(p["budgetUsd"] for m, p in self.calls if m == BATCH), [100, 500, 1000])
        self.assertEqual(out.strip().splitlines()[-1], "Which budget? Or take it live as it is now?")

    def test_a_named_budget_is_quoted_at_that_budget(self):
        code, out, err = self._catalog("--budget", "250")
        self.assertEqual(code, 0, err)
        self.assertIn("· $250 · 10 credits an hour", out)
        self.assertIn("All 3: 30 credits an hour (720 a day).", out)
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
        self.assertIn("Test plan for dip-gift, on virtual money", out)
        self.assertIn("All 3: 30 credits an hour (720 a day).", out)
        self.assertEqual(out.strip().splitlines()[-1], QUESTION)

    def test_an_unclear_reply_is_not_a_yes(self):
        _, _, err = self._catalog()
        self.assertIn("\"Your call\", \"sounds good\", \"you pick\" and silence are not a yes", err)


class AStartNeedsItsPlan(unittest.TestCase):
    """`create --simulation --user-said-yes` starts a package only after `plan` quoted it at that budget:
    on dev an agent skipped the plan and started three charged simulations on "sounds good"."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.root = Path(tmp)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))
        self.pkg_dir = _write_pkg(self.root)
        self.started = []
        self.instances = ["main"]
        names = ("run_deploy", "ensure_pkg", "_fork_for_deploy", "outside_durable_root", "full_validate",
                 "start_request_ids_supported")
        orig = {n: getattr(deploy, n) for n in names}
        orig_gw = _cli.gateway_call
        self.addCleanup(lambda: [setattr(deploy, n, f) for n, f in orig.items()])
        self.addCleanup(lambda: setattr(_cli, "gateway_call", orig_gw))
        deploy.run_deploy = lambda p, a, log: self.started.append((a.package, a.budget)) or 0
        deploy.ensure_pkg = lambda *a, **k: types.SimpleNamespace(id="dip-gift", dir=self.pkg_dir,
                                                                    instances=self.instances)
        deploy._fork_for_deploy = lambda p, a, log: p
        deploy.outside_durable_root = lambda p, cmd, simulation=False, **k: None
        deploy.full_validate = lambda p: []
        deploy.start_request_ids_supported = lambda log, as_json=False: True

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            if method == HEALTH:
                return _answer("ok", {"deploy": {"capabilities": {"simulationYesGate": True}}})
            if method == START and (params or {}).get("dryRun") is True:
                return _answer("ok", {"dryRun": True, "budgetUsd": params.get("budget"), "creditsPerHour": 10,
                                      "creditsPerDay": 240, "creditsPerHourAfter": 10})
            raise AssertionError(f"unexpected gateway call {method} {params}")
        _cli.gateway_call = _gateway

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                deploy._main(["deploy.py", *argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def _plan(self, *extra, age=None, ref=None):
        code, _out, err = self._main("plan", ref or str(self.pkg_dir), *extra)
        self.assertEqual(code, 0, err)
        self._age(deploy.PLAN_MIN_GAP_S + 1 if age is None else age)

    def _age(self, seconds):
        for rec in (self.root / deploy.PLAN_RECORD_DIR).glob("*.json"):
            data = json.loads(rec.read_text())
            data["at"] -= seconds
            rec.write_text(json.dumps(data))

    def test_a_yes_start_with_no_plan_starts_nothing_and_names_plan(self):
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn(f"deploy.py plan {self.pkg_dir}", err)
        self.assertEqual(self.started, [])

    def test_after_its_plan_the_yes_starts_it(self):
        self._plan()
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.started), 1)

    def test_the_plan_counts_for_the_package_by_its_id_too(self):
        self._plan()
        code, _out, err = self._main("create", "dip-gift", "--simulation", "--user-said-yes")
        self.assertEqual(code, 0, err)

    def test_a_budget_the_plan_did_not_quote_starts_nothing(self):
        self._plan()
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes",
                                     "--budget", "500")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("--budget 500", err)
        self.assertEqual(self.started, [])

    def test_a_budget_choice_the_plan_quoted_starts(self):
        self._plan("--budget-choices", "100,500")
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes",
                                     "--budget", "500")
        self.assertEqual(code, 0, err)

    def test_a_start_in_the_same_turn_as_its_plan_starts_nothing(self):
        self._plan(age=0)
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn("end your turn", err)
        self.assertEqual(self.started, [])

    def test_a_stale_plan_does_not_count(self):
        self._plan(age=deploy.PLAN_RECORD_TTL_S + 1)
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)

    def test_a_variation_fork_of_a_started_simulation_needs_no_plan_of_its_own(self):
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes",
                                     "--variant-of", "simulation-11111111-2222-4333-8444-555555555555")
        self.assertEqual(code, 0, err)

    def test_a_package_of_several_wallets_keeps_its_billed_by_the_hour_yes(self):
        self.instances = ["long", "short"]
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes")
        self.assertEqual(code, 0, err)

    def test_a_live_create_is_untouched(self):
        code, _out, err = self._main("create", str(self.pkg_dir), "--budget", "50")
        self.assertNotIn("E_NO_PLAN", err)


class ACatalogPlanStartsFromItsRecord(unittest.TestCase):
    """`start-plan` starts exactly the batch `plan` quoted, only after a later yes: on dev an agent ran
    `plan hawk` and started it in the same turn, reading "simulate hawk" as the yes."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.root = Path(tmp)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))
        self.calls = []
        orig = _cli.gateway_call
        self.addCleanup(lambda: setattr(_cli, "gateway_call", orig))
        orig_sleep = deploy._sleep
        self.addCleanup(lambda: setattr(deploy, "_sleep", orig_sleep))
        deploy._sleep = lambda s: None

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            params = dict(params or {})
            self.calls.append((method, params))
            if method == BATCH:
                if params.get("dryRun"):
                    return _batch_quote(params)
                return _answer("ok", {"requestId": params["requestId"], "dryRun": False, "rows": [
                    {"label": v["label"], "status": "starting"} for v in params["variants"]]})
            if method == "senpi.simulations.batchStatus":
                return _answer("ok", {"requestId": params["requestId"], "rows": [
                    {"label": "as-designed", "status": "started", "phase": "live", "simulationId": "simulation-1"}]})
            raise AssertionError(f"unexpected gateway call {method}")
        _cli.gateway_call = _gateway

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                deploy._main(["deploy.py", *argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def _plan(self, *extra, age=None):
        code, _out, err = self._main("plan", "hawk", "--design", "d", "--trades", "t", *extra)
        self.assertEqual(code, 0, err)
        for rec in (self.root / deploy.PLAN_RECORD_DIR).glob("*.json"):
            data = json.loads(rec.read_text())
            data["at"] -= deploy.PLAN_MIN_GAP_S + 1 if age is None else age
            rec.write_text(json.dumps(data))

    def _starts(self):
        return [p for m, p in self.calls if m == BATCH and not p.get("dryRun")]

    def test_after_the_yes_it_starts_the_quoted_batch(self):
        self._plan("--variant", "pickier|scanner.inputs.minScore=7|pickier", "--variant", "lev3|leverage=3|less")
        code, out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, 0, err)
        [start] = self._starts()
        self.assertIs(start["userSaidYes"], True)
        self.assertEqual(start["strategy"], "hawk")
        self.assertEqual(start["budgetUsd"], 1000)
        self.assertEqual([v["settings"] for v in start["variants"]],
                         [{}, {"scanner.inputs.minScore": 7}, {"leverage": 3}])
        self.assertIn("as-designed: live (simulation-1)", out)

    def test_the_opening_request_is_not_the_yes(self):
        self._plan(age=0)
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn("end your turn", err)
        self.assertEqual(self._starts(), [])

    def test_no_plan_no_start(self):
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("deploy.py plan hawk", err)
        self.assertEqual(self.calls, [])

    def test_without_the_yes_flag_it_starts_nothing(self):
        self._plan()
        code, _out, err = self._main("start-plan", "hawk")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("E_NEEDS_USER_YES", err)
        self.assertEqual(self._starts(), [])

    def test_a_tapped_budget_choice_starts_at_that_budget(self):
        self._plan("--budget-choices", "100,500")
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes", "--budget", "500")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._starts()[0]["budgetUsd"], 500)

    def test_an_unquoted_budget_starts_nothing(self):
        self._plan()
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes", "--budget", "250")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("--budget 250", err)
        self.assertEqual(self._starts(), [])

    def test_one_named_setting_starts_alone(self):
        code, _out, err = self._main("plan", "hawk", "--variant", "lev3|leverage=3|half the leverage")
        self.assertEqual(code, 0, err)
        rec = next((self.root / deploy.PLAN_RECORD_DIR).glob("*.json"))
        rec.write_text(json.dumps({**json.loads(rec.read_text()), "at": time.time() - deploy.PLAN_MIN_GAP_S - 1}))
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, 0, err)
        self.assertEqual([v["settings"] for v in self._starts()[0]["variants"]], [{"leverage": 3}])

    def _call_start(self, **params):
        body = {"requestId": "r-1", "strategy": "hawk", "budgetUsd": 1000, "dryRun": False, "userSaidYes": True,
                "variants": [{"label": "as-designed", "settings": {}}], **params}
        return self._main("call", "senpi.simulations.startBatch", "--params", json.dumps(body))

    def test_a_catalog_start_through_call_needs_its_plan_too(self):
        code, _out, err = self._call_start()
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn("deploy.py start-plan hawk --user-said-yes", err)
        self.assertEqual(self.calls, [])

    def test_a_catalog_start_through_call_after_its_plan_is_sent(self):
        self._plan()
        code, _out, err = self._call_start()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self._starts()), 1)

    def test_a_dry_run_through_call_needs_no_plan(self):
        code, _out, err = self._call_start(dryRun=True)
        self.assertEqual(code, 0, err)

    def test_a_variation_of_a_running_simulation_through_call_needs_no_plan(self):
        body = {"requestId": "r-2", "parentId": "simulation-1", "budgetUsd": 1000, "dryRun": False,
                "userSaidYes": True, "variants": [{"label": "lev3", "settings": {"leverage": 3}}]}
        code, _out, err = self._main("call", "senpi.simulations.startBatch", "--params", json.dumps(body))
        self.assertEqual(code, 0, err)

    def test_budgets_that_differ_through_call_need_a_plan_like_any_catalog_start(self):
        self._plan()
        code, _out, err = self._call_start(variants=[{"label": "as-designed", "settings": {}},
                                                     {"label": "lev3", "budgetUsd": 1001, "settings": {"leverage": 3}}])
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn("a budget of its own", err)
        self.assertEqual(self._starts(), [])

    def test_more_than_five_simulations_through_call_are_refused_quote_or_start(self):
        ten = [{"label": f"v{i}", "settings": {"leverage": i + 1}} for i in range(10)]
        variation = {"requestId": "r-3", "parentId": "simulation-1", "budgetUsd": 1000, "dryRun": False,
                     "userSaidYes": True, "variants": ten}
        for call in (lambda: self._call_start(variants=ten), lambda: self._call_start(variants=ten, dryRun=True),
                     lambda: self._main("call", "senpi.simulations.startBatch", "--params", json.dumps(variation))):
            code, _out, err = call()
            self.assertEqual(code, deploy.EXIT_CODES["refused"])
            self.assertIn("[E_BATCH_TOO_LARGE]", err)
            self.assertIn("at most 5", err)
            self.assertIn("second batch", err)
        self.assertEqual(self.calls, [])

    def test_five_simulations_through_call_are_quoted(self):
        five = [{"label": f"v{i}", "settings": {"leverage": i + 1}} for i in range(5)]
        code, _out, err = self._call_start(variants=five, dryRun=True)
        self.assertEqual(code, 0, err)

    def test_the_plan_tells_the_agent_to_start_with_start_plan(self):
        _code, _out, err = self._main("plan", "hawk", "--design", "d", "--trades", "t")
        self.assertIn("deploy.py start-plan hawk --user-said-yes", err)
        self.assertNotIn("senpi.simulations.startBatch", err)


if __name__ == "__main__":
    unittest.main()
