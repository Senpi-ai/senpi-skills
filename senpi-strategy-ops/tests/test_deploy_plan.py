#!/usr/bin/env python3
"""`deploy.py plan` quotes a strategy and its variations as simulations and prints the test plan the
agent relays as given: each variation carries every setting it changes, the plan carries the quoted credits an hour
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


def _chat(wrote_s_ago, **fields):
    """One chat as `sessions.list` lists it, the user's last message `wrote_s_ago` seconds ago."""
    return {"key": "agent:main:dashboard:x", "lastInteractionAt": (time.time() - wrote_s_ago) * 1000, **fields}


def _chats_answer(chats):
    """`sessions.list`. None: the user has just written (a later message than any plan in a test)."""
    if chats == "unreadable":
        return _answer("error", message="gateway closed")
    return _answer("ok", {"sessions": [_chat(0)] if chats is None else chats})


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
        orig_open = getattr(deploy, "_open_simulations", None)
        deploy._open_simulations = lambda: []
        self.addCleanup(lambda: setattr(deploy, "_open_simulations", orig_open))

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

    def test_a_variation_changing_two_settings_is_quoted_and_shown_with_both(self):
        code, out, err = self._run("hawk", "--design", "d", "--trades", "t",
                                   "--variant", "lev2-low|maxLeverage=2;minScore=3|leverage 2, lower score",
                                   "--variant", "lev3-low|maxLeverage=3; minScore=3|leverage 3, lower score")
        self.assertEqual(code, 0, err)
        self.assertIn("2. lev2-low: leverage 2, lower score (maxLeverage = 2, minScore = 3) · trades t ·", out)
        self.assertIn("3. lev3-low: leverage 3, lower score (maxLeverage = 3, minScore = 3) · trades t ·", out)
        [quote] = [p for m, p in self.calls if m == BATCH]
        self.assertEqual([v["settings"] for v in quote["variants"]],
                         [{}, {"maxLeverage": 2, "minScore": 3}, {"maxLeverage": 3, "minScore": 3}])

    def test_one_simulation_with_two_settings_shows_both(self):
        code, out, err = self._run("hawk", "--variant", "lev2-low|maxLeverage=2;minScore=3|a calmer entry")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("Simulation of hawk, on virtual money (nothing real moves): lev2-low: a calmer "
                                       "entry (maxLeverage = 2, minScore = 3) · $1,000"), out)

    def test_a_value_with_a_comma_stays_one_setting(self):
        code, out, err = self._run("hawk", "--variant", "majors|scanner.inputs.coins=BTC,ETH;maxLeverage=2|majors only")
        self.assertEqual(code, 0, err)
        [quote] = [p for m, p in self.calls if m == BATCH]
        self.assertEqual([v["settings"] for v in quote["variants"]],
                         [{"scanner.inputs.coins": "BTC,ETH", "maxLeverage": 2}])

    def test_settings_run_together_without_a_semicolon_are_refused_before_any_quote(self):
        for setting in ("scanner.inputs.minScore=7,leverage=3", "minScore=7 leverage=3", "minScore=3;minScore=4",
                        "minScore=3;", "minScore=3;;leverage=2"):
            self.calls.clear()
            code, out, err = self._run("hawk", "--design", "d", "--trades", "t",
                                       "--variant", f"both|{setting}|two things",
                                       "--variant", "ok|leverage=3|half leverage")
            self.assertEqual(code, deploy.EXIT_CODES["refused"], setting)
            self.assertEqual(out, "", setting)
            self.assertEqual(self.calls, [], setting)

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

    def test_a_variation_with_one_setting_at_the_designs_value_is_refused_even_beside_a_real_change(self):
        pkg = _write_pkg(self.root)
        for settings in ("minScore=3;pullbackThreshold=5", "pullbackThreshold=5;minScore=5"):
            self.calls.clear()
            code, out, err = self._run(str(pkg), "--design", "d", "--trades", "t",
                                       "--variant", f"mixed|{settings}|a mixed change",
                                       "--variant", "ok|minScore=7|pickier entries")
            self.assertEqual(code, deploy.EXIT_CODES["refused"], settings)
            self.assertIn("is already the design's own value", err)
            self.assertEqual(self.calls, [], settings)

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

    def test_a_package_of_two_books_counts_each_book_as_a_simulation(self):
        pkg = self.root / "duo"
        pkg.mkdir()
        self.start = _answer("ok", {"dryRun": True, "budgetUsd": 1000, "creditsPerHour": 20, "creditsPerDay": 480,
                                    "creditsPerHourAfter": 20,
                                    "instances": [{"name": "duo-long", "creditsPerHour": 10},
                                                  {"name": "duo-short", "creditsPerHour": 10}]})
        code, out, err = self._run(str(pkg), "--design", "fades BTC and ETH moves", "--trades", "a few times a week",
                                   *self.VARIANTS)
        self.assertEqual(code, 0, err)
        self.assertIn("It has 2 books (duo-long, duo-short), so each line below starts 2 simulations.", out)
        self.assertIn("All 3, 6 simulations: 60 credits an hour (1440 a day).", out)
        self.assertEqual(out.strip().splitlines()[-1], QUESTION)
        self.assertIn('count simulations, not variations: "3 variations, 2 books each: 6 simulations"', err)

    def _two_book_quote(self):
        self.start = _answer("ok", {"dryRun": True, "budgetUsd": 1000, "creditsPerHour": 20, "creditsPerDay": 480,
                                    "creditsPerHourAfter": 60,
                                    "instances": [{"name": "long"}, {"name": "short"}]})

    def _package(self, pkg_id, books):
        pkg = self.root / pkg_id
        pkg.mkdir()
        (pkg / "strategy.yaml").write_text(
            f"id: {pkg_id}\nversion: 1.0.0\ninstances:\n"
            + "".join(f"  - name: {b}\n    runtime: {b}/runtime.yaml\n" for b in books))
        return pkg

    def _open(self, rows):
        orig = deploy._open_simulations
        deploy._open_simulations = lambda: rows
        self.addCleanup(lambda: setattr(deploy, "_open_simulations", orig))

    def test_variations_of_a_running_design_quote_only_the_variations(self):
        pkg = self._package("duo", ["long", "short"])
        self._two_book_quote()
        self._open([{"strategyName": "duo-long", "strategyId": "simulation-L", "status": "ACTIVE"},
                    {"strategyName": "duo-short", "strategyId": "simulation-S", "status": "ACTIVE"}])
        code, out, err = self._run(str(pkg), "--design", "fades BTC and ETH moves", "--trades", "a few times a week",
                                   *self.VARIANTS)
        self.assertEqual(code, 0, err)
        self.assertNotIn("As designed", out)
        self.assertIn("2 variations of your running duo simulation, which keeps running as it is", out)
        self.assertIn("1. pickier:", out)
        self.assertIn("All 2, 4 simulations: 40 credits an hour (960 a day).", out)
        self.assertIn("--variant-of simulation-L", err)
        self.assertNotIn("start the design", err)

    def test_a_running_design_is_matched_by_its_wallet_names_not_its_book_names(self):
        pkg = self._package("duo", ["long", "short"])
        self._two_book_quote()
        self._open([{"strategyName": "long", "strategyId": "simulation-x", "status": "ACTIVE"},
                    {"strategyName": "short", "strategyId": "simulation-y", "status": "ACTIVE"}])
        code, out, err = self._run(str(pkg), "--design", "fades BTC and ETH moves", "--trades", "a few times a week",
                                   *self.VARIANTS)
        self.assertEqual(code, 0, err)
        self.assertIn("1. As designed:", out)

    def test_variations_of_a_running_one_book_design_quote_only_the_variations(self):
        pkg = self._package("solo", ["main"])
        self.start = _answer("ok", {"dryRun": True, "budgetUsd": 1000, "creditsPerHour": 10, "creditsPerDay": 240,
                                    "creditsPerHourAfter": 30, "instances": [{"name": "main"}]})
        self._open([{"strategyName": "solo", "strategyId": "simulation-S", "status": "ACTIVE"}])
        code, out, err = self._run(str(pkg), "--design", "buys BTC dips", "--trades", "a few times a week",
                                   *self.VARIANTS)
        self.assertEqual(code, 0, err)
        self.assertNotIn("As designed", out)
        self.assertIn("All 2: 20 credits an hour", out)
        self.assertIn("--variant-of simulation-S", err)

    def test_a_design_not_running_on_every_book_is_planned_as_before(self):
        pkg = self._package("duo", ["long", "short"])
        self._two_book_quote()
        self._open([{"strategyName": "duo-long", "strategyId": "simulation-L", "status": "ACTIVE"}])
        code, out, err = self._run(str(pkg), "--design", "fades BTC and ETH moves", "--trades", "a few times a week",
                                   *self.VARIANTS)
        self.assertIn("1. As designed:", out)
        self.assertIn("All 3, 6 simulations: 60 credits an hour", out)

    def test_an_unreadable_list_of_simulations_plans_as_before(self):
        pkg = self._package("duo", ["long", "short"])
        self._two_book_quote()
        self._open(None)
        code, out, err = self._run(str(pkg), "--design", "fades BTC and ETH moves", "--trades", "a few times a week",
                                   *self.VARIANTS)
        self.assertIn("1. As designed:", out)

    def test_one_simulation_of_two_books_says_it_starts_two(self):
        pkg = self.root / "duo"
        pkg.mkdir()
        self.start = _answer("ok", {"dryRun": True, "budgetUsd": 1000, "creditsPerHour": 20, "creditsPerDay": 480,
                                    "instances": [{"name": "duo-long"}, {"name": "duo-short"}]})
        code, out, err = self._run(str(pkg))
        self.assertEqual(code, 0, err)
        self.assertIn("It has 2 books (duo-long, duo-short), so it starts 2 simulations.", out)

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
        self.quotes = []
        self.quote_fails = False
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
        self.chats = None

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            if method == HEALTH:
                return _answer("ok", {"deploy": {"capabilities": {"simulationYesGate": True}}})
            if method == START and (params or {}).get("dryRun") is True:
                self.quotes.append((method, dict(params)))
                if self.quote_fails:
                    return _answer("error", None, "simulation-service is temporarily unavailable")
                return _answer("ok", {"dryRun": True, "budgetUsd": params.get("budget"), "creditsPerHour": 10,
                                      "creditsPerDay": 240, "creditsPerHourAfter": 10})
            if method == "sessions.list":
                return _chats_answer(self.chats)
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

    def _start(self):
        return self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes")

    def _refused_as_same_turn(self, code, err):
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn("end your turn", err)
        self.assertEqual(self.started, [])

    def test_a_start_in_the_same_turn_as_its_plan_starts_nothing(self):
        self.chats = [_chat(wrote_s_ago=30)]
        self._plan(age=0)
        self._refused_as_same_turn(*self._start()[::2])

    def test_waiting_inside_the_turn_that_quoted_it_still_starts_nothing(self):
        self.chats = [_chat(wrote_s_ago=120)]
        self._plan(age=60)
        self._refused_as_same_turn(*self._start()[::2])

    def test_a_yes_seconds_after_the_quote_starts_it(self):
        self.chats = [_chat(wrote_s_ago=0)]
        self._plan(age=3)
        code, _out, err = self._start()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.started), 1)

    def test_a_subagent_or_background_chat_is_not_the_user(self):
        self.chats = [_chat(wrote_s_ago=30), _chat(wrote_s_ago=0, spawnDepth=1),
                      _chat(wrote_s_ago=0, isBackground=True)]
        self._plan(age=5)
        self._refused_as_same_turn(*self._start()[::2])

    def test_when_the_chats_cannot_be_read_a_start_this_soon_is_the_same_turn(self):
        self.chats = "unreadable"
        self._plan(age=3)
        self._refused_as_same_turn(*self._start()[::2])

    def test_when_the_chats_cannot_be_read_a_later_start_goes_ahead(self):
        self.chats = "unreadable"
        self._plan(age=deploy.PLAN_MIN_GAP_S + 1)
        code, _out, err = self._start()
        self.assertEqual(code, 0, err)

    def test_a_stale_plan_does_not_count(self):
        self._plan(age=deploy.PLAN_RECORD_TTL_S + 1)
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)

    def test_a_variation_fork_of_a_started_simulation_needs_no_plan_of_its_own(self):
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes",
                                     "--variant-of", "simulation-11111111-2222-4333-8444-555555555555",
                                     "--batch", "b-1")
        self.assertEqual(code, 0, err)

    def _write_books(self, name="two-books"):
        pkg = self.root / name
        pkg.mkdir()
        (pkg / "strategy.yaml").write_text(
            f"id: {name}\ninstances:\n  - name: long\n    runtime: long/runtime.yaml\n"
            f"  - name: short\n    runtime: short/runtime.yaml\n")
        for book in ("long", "short"):
            (pkg / book).mkdir()
            (pkg / book / "runtime.yaml").write_text(RUNTIME_YAML)
        self.instances = ["long", "short"]
        return pkg

    def test_a_package_of_several_wallets_starts_nothing_without_its_plan(self):
        self._write_books()
        code, _out, err = self._main("create", "two-books", "--simulation", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertEqual(self.started, [])

    def test_a_package_of_several_wallets_is_quoted_by_its_id_and_starts_on_the_yes(self):
        self._write_books()
        self._plan(ref="two-books")
        self.assertEqual([p.get("dryRun") for m, p in self.quotes], [True])
        code, _out, err = self._main("create", "two-books", "--simulation", "--user-said-yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.started), 1)

    def test_a_failed_quote_lets_nothing_start(self):
        self.quote_fails = True
        code, out, err = self._main("plan", str(self.pkg_dir))
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertEqual(out, "")
        self.assertIn("could not be quoted", err)
        self._age(deploy.PLAN_MIN_GAP_S + 1)
        code, _out, err = self._main("create", str(self.pkg_dir), "--simulation", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertEqual(self.started, [])

    def test_runtime_is_the_same_start_and_needs_the_plan_too(self):
        code, _out, err = self._main("runtime", str(self.pkg_dir), "--simulation", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertEqual(self.started, [])

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
            if method == "sessions.list":
                return _chats_answer(self.chats)
            raise AssertionError(f"unexpected gateway call {method}")
        _cli.gateway_call = _gateway
        self.chats = None

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
        self.chats = [_chat(wrote_s_ago=40)]
        self._plan(age=30)
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn("end your turn", err)
        self.assertEqual(self._starts(), [])

    def test_the_same_quote_again_in_the_yes_turn_still_starts_it(self):
        self.chats = [_chat(wrote_s_ago=5)]
        self._plan(age=30)
        self._plan(age=0)
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self._starts()), 1)

    def test_a_different_quote_in_the_yes_turn_is_unseen(self):
        self.chats = [_chat(wrote_s_ago=5)]
        self._plan(age=30)
        self._plan("--variant", "lev3|leverage=3|less", age=0)
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("end your turn", err)
        self.assertEqual(self._starts(), [])

    def test_a_yes_sent_right_after_the_quote_starts_it(self):
        self.chats = [_chat(wrote_s_ago=0)]
        self._plan(age=4)
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self._starts()), 1)

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

    def test_every_quoted_setting_of_a_variation_is_recorded_and_started(self):
        self._plan("--variant", "lev2-low|maxLeverage=2;minScore=3|leverage 2, lower score",
                   "--variant", "lev4-low|maxLeverage=4;minScore=3|leverage 4, lower score")
        rec = json.loads(next((self.root / deploy.PLAN_RECORD_DIR).glob("*.json")).read_text())
        self.assertEqual(rec["variants"], [{"label": "lev2-low", "settings": {"maxLeverage": 2, "minScore": 3}},
                                           {"label": "lev4-low", "settings": {"maxLeverage": 4, "minScore": 3}}])
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, 0, err)
        self.assertEqual([v["settings"] for v in self._starts()[0]["variants"]],
                         [{}, {"maxLeverage": 2, "minScore": 3}, {"maxLeverage": 4, "minScore": 3}])

    def test_one_named_setting_starts_alone(self):
        code, _out, err = self._main("plan", "hawk", "--variant", "lev3|leverage=3|half the leverage")
        self.assertEqual(code, 0, err)
        rec = next((self.root / deploy.PLAN_RECORD_DIR).glob("*.json"))
        rec.write_text(json.dumps({**json.loads(rec.read_text()), "at": time.time() - deploy.PLAN_MIN_GAP_S - 1}))
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, 0, err)
        self.assertEqual([v["settings"] for v in self._starts()[0]["variants"]], [{"leverage": 3}])

    def test_a_start_the_runtime_refuses_says_so_and_sends_the_agent_back_to_plan(self):
        self._plan()
        orig = _cli.gateway_call
        no_quote = ("[E_NO_QUOTE] Nothing was started: this box answered no quote of this exact batch in the last "
                    "6 hours. Quote it with these params and \"dryRun\": true, show the user, and end your turn.")
        _cli.gateway_call = lambda method, params=None, **kw: (
            _answer("refused", None, no_quote) if method == BATCH and not (params or {}).get("dryRun")
            else orig(method, params, **kw))
        code, _out, err = self._main("start-plan", "hawk", "--user-said-yes")
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_QUOTE]", err)
        self.assertIn("deploy.py plan hawk", err)
        self.assertNotIn("batch-wait", err)

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

    def test_one_variant_with_its_own_budget_needs_its_plan(self):
        code, _out, err = self._call_start(variants=[{"label": "own-budget", "budgetUsd": 999}])
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn("`deploy.py plan hawk --budget 999`", err)
        self.assertIn("`deploy.py start-plan hawk --budget 999 --user-said-yes`", err)
        self.assertEqual(self.calls, [])

    def test_one_variant_with_its_own_budget_starts_after_a_plan_at_that_budget(self):
        self._plan("--budget", "999")
        code, _out, err = self._call_start(variants=[{"label": "own-budget", "budgetUsd": 999}])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self._starts()), 1)

    def test_a_dry_run_through_call_needs_no_plan(self):
        code, _out, err = self._call_start(dryRun=True)
        self.assertEqual(code, 0, err)

    def _age_call_quotes(self):
        for rec in (self.root / deploy.PLAN_RECORD_DIR).glob("call-*.json"):
            rec.write_text(json.dumps({"at": time.time() - deploy.PLAN_MIN_GAP_S - 1}))

    def _variation_call(self, **params):
        body = {"requestId": "r-2", "parentId": "simulation-1", "budgetUsd": 1000, "dryRun": False,
                "userSaidYes": True, "variants": [{"label": "lev3", "settings": {"leverage": 3}}], **params}
        return self._main("call", "senpi.simulations.startBatch", "--params", json.dumps(body))

    def test_a_variation_of_a_running_simulation_starts_after_its_dry_run_in_an_earlier_turn(self):
        self.chats = [_chat(wrote_s_ago=40)]
        code, _out, err = self._variation_call(requestId="q-2", dryRun=True)
        self.assertEqual(code, 0, err)
        code, _out, err = self._variation_call()
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("end your turn", err)
        self.assertEqual(self._starts(), [])
        self.chats = None
        code, _out, err = self._variation_call()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self._starts()), 1)

    def test_a_variation_of_a_running_simulation_without_its_dry_run_starts_nothing(self):
        code, _out, err = self._variation_call()
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_NO_PLAN]", err)
        self.assertIn('"dryRun": true', err)
        self.assertEqual(self._starts(), [])

    def test_budgets_that_differ_start_only_as_their_dry_run_quoted_them(self):
        self._plan()
        quoted = [{"label": "a", "budgetUsd": 60, "settings": {}}, {"label": "b", "budgetUsd": 80, "settings": {"leverage": 3}}]
        code, _out, err = self._call_start(variants=quoted)
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("no dry run of this exact batch", err)
        code, _out, err = self._call_start(variants=quoted, requestId="q-1", dryRun=True)
        self.assertEqual(code, 0, err)
        self._age_call_quotes()
        changed = [quoted[0], {**quoted[1], "budgetUsd": 1001}]
        code, _out, err = self._call_start(variants=changed)
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertEqual(self._starts(), [])
        code, _out, err = self._call_start(variants=quoted)
        self.assertEqual(code, 0, err)
        self.assertEqual([v["budgetUsd"] for v in self._starts()[0]["variants"]], [60, 80])

    def test_more_than_five_simulations_through_call_are_refused_quote_or_start(self):
        ten = [{"label": f"v{i}", "settings": {"leverage": i + 1}} for i in range(10)]
        variation = {"requestId": "r-3", "parentId": "simulation-1", "budgetUsd": 1000, "dryRun": False,
                     "userSaidYes": True, "variants": ten}
        for call, cap in ((lambda: self._call_start(variants=ten), "at most 5"),
                          (lambda: self._call_start(variants=ten, dryRun=True), "at most 5"),
                          (lambda: self._main("call", "senpi.simulations.startBatch", "--params", json.dumps(variation)),
                           "at most 4")):
            code, _out, err = call()
            self.assertEqual(code, deploy.EXIT_CODES["refused"])
            self.assertIn("[E_BATCH_TOO_LARGE]", err)
            self.assertIn(cap, err)
            self.assertIn("second batch", err)
        self.assertEqual(self.calls, [])

    def test_five_variations_of_a_running_simulation_through_call_are_refused(self):
        five = [{"label": f"v{i}", "settings": {"leverage": i + 1}} for i in range(5)]
        body = {"requestId": "r-5", "parentId": "simulation-1", "budgetUsd": 1000, "dryRun": True, "variants": five}
        code, _out, err = self._main("call", "senpi.simulations.startBatch", "--params", json.dumps(body))
        self.assertEqual(code, deploy.EXIT_CODES["refused"])
        self.assertIn("[E_BATCH_TOO_LARGE]", err)
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
