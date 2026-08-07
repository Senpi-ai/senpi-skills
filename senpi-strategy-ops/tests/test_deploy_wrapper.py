#!/usr/bin/env python3
"""Hermetic unit tests for the deploy.py wrapper's drive-the-verb loop.

No openclaw, no MCP, no network — `_cli.run_cli` is stubbed and every input is a plain dict. The
loop these tests pin is the one that broke in review: the wrapper polls `deploy status` with the
EXPLICIT deployId, so a controller that answers an id-addressed running job with `interrupted`
would make every wrapper-driven deploy exit within seconds of starting. Run:

    python3 senpi-strategy-ops/tests/test_deploy_wrapper.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import sys
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli    # noqa: E402
import deploy  # noqa: E402


def _args(**kw):
    base = dict(budget=None, decision_model=None, tick_wait=None, max_wait=60,
                json=False, dry_run=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _pkg(dir_="/pkg/spider", pid="spider"):
    return types.SimpleNamespace(dir=dir_, id=pid, version="1.0.0", instances=[])


class FakeCli:
    """Records every argv and replays queued (rc, stdout, stderr) triples."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(list(args))
        return self.responses.pop(0) if self.responses else (0, "", "")

    def argv_for(self, method):
        return [c for c in self.calls if method in c]


def _ok(payload):
    return (0, json.dumps(payload), "")


class StartDeploy(unittest.TestCase):
    def setUp(self):
        self._real = _cli.run_cli

    def tearDown(self):
        _cli.run_cli = self._real

    def test_builds_the_verb_argv_with_every_flag(self):
        fake = FakeCli([_ok({"deployId": "dpl-a1b2c3d4", "phase": "reconcile"})])
        _cli.run_cli = fake
        did = deploy.start_deploy(
            _pkg(), _args(budget=500.0, decision_model="samurai-light", tick_wait=0), lambda m: None)
        self.assertEqual(did, "dpl-a1b2c3d4")
        argv = fake.calls[0]
        self.assertEqual(argv[:4], ["openclaw", "senpi", "deploy", "-p"])
        self.assertIn("--json", argv)
        self.assertIn("--budget", argv)
        self.assertEqual(argv[argv.index("--budget") + 1], "500")
        self.assertEqual(argv[argv.index("--decision-model") + 1], "samurai-light")
        self.assertEqual(argv[argv.index("--tick-wait") + 1], "0")
        # `--max-wait` is the JOB's wallet-ACTIVE budget as well as this script's poll budget: a
        # wrapper that only polls longer leaves the caller believing they raised a budget they didn't.
        self.assertEqual(argv[argv.index("--max-wait") + 1], "60")

    def test_max_wait_reaches_the_verb_not_just_the_poll_loop(self):
        fake = FakeCli([_ok({"deployId": "dpl-a1b2c3d4"})])
        _cli.run_cli = fake
        deploy.start_deploy(_pkg(), _args(budget=500.0, max_wait=900), lambda m: None)
        argv = fake.calls[0]
        self.assertEqual(argv[argv.index("--max-wait") + 1], "900")

    def test_a_refused_start_relays_the_verbs_own_text_and_exits_nonzero(self):
        _cli.run_cli = FakeCli([(1, "", "[E_FUNDS_BELOW_FLOOR] Requested $500.00 …")])
        with self.assertRaises(SystemExit) as ctx:
            deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        self.assertNotEqual(ctx.exception.code, 0)

    def test_dry_run_prints_the_command_and_never_calls_the_cli(self):
        fake = FakeCli([])
        _cli.run_cli = fake
        with self.assertRaises(SystemExit) as ctx:
            deploy.start_deploy(_pkg(), _args(budget=500.0, dry_run=True), lambda m: None)
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(fake.calls, [])


class WaitForTerminal(unittest.TestCase):
    """The poll loop always passes the explicit deployId — the shape the C1/C2 bug broke."""

    def setUp(self):
        self._real_cli, self._real_sleep = _cli.run_cli, deploy.time.sleep
        deploy.time.sleep = lambda _s: None

    def tearDown(self):
        _cli.run_cli, deploy.time.sleep = self._real_cli, self._real_sleep

    def test_polls_with_the_explicit_id_until_the_job_is_terminal(self):
        fake = FakeCli([
            _ok({"state": {"status": "running", "phase": "create"}}),
            _ok({"state": {"status": "running", "phase": "install"}}),
            _ok({"state": {"status": "done", "overall": "live"}}),
        ])
        _cli.run_cli = fake
        snap = deploy.wait_for_terminal("dpl-a1b2c3d4", 600, lambda m: None)

        self.assertEqual(snap["state"], {"status": "done", "overall": "live"})
        self.assertEqual(len(fake.calls), 3)
        for argv in fake.calls:
            self.assertIn("senpi.deploy.status" if "senpi.deploy.status" in argv else "status", argv)
            self.assertIn("dpl-a1b2c3d4", argv)  # every poll is id-addressed
            self.assertIn("--json", argv)

    def test_a_running_job_is_never_treated_as_terminal(self):
        # If the gateway answered an id-addressed running job with `interrupted`, this loop would
        # return on the first poll and the wrapper would report a finished deploy seconds in.
        fake = FakeCli([_ok({"state": {"status": "running", "phase": "create"}})] * 40)
        _cli.run_cli = fake
        snap = deploy.wait_for_terminal("dpl-a1b2c3d4", 0, lambda m: None)
        self.assertEqual(snap["state"]["status"], "running")

    def test_an_interrupted_snapshot_is_terminal(self):
        _cli.run_cli = FakeCli([_ok({"state": {"status": "interrupted"}})])
        snap = deploy.wait_for_terminal("dpl-a1b2c3d4", 600, lambda m: None)
        self.assertEqual(snap["state"], {"status": "interrupted"})

    def test_unreadable_status_never_crashes_the_loop(self):
        _cli.run_cli = FakeCli([(1, "", "gateway down"), _ok({"state": {"status": "done", "overall": "live"}})])
        snap = deploy.wait_for_terminal("dpl-a1b2c3d4", 600, lambda m: None)
        self.assertEqual(snap["state"]["overall"], "live")


class RunDeployExitCodes(unittest.TestCase):
    def setUp(self):
        self._real_cli, self._real_sleep = _cli.run_cli, deploy.time.sleep
        deploy.time.sleep = lambda _s: None

    def tearDown(self):
        _cli.run_cli, deploy.time.sleep = self._real_cli, self._real_sleep

    def _run(self, overall):
        _cli.run_cli = FakeCli([
            _ok({"deployId": "dpl-a1b2c3d4", "phase": "reconcile"}),
            _ok({"state": {"status": "done", "overall": overall}}),
            (0, f"deploy dpl-a1b2c3d4 — done — {overall}", ""),
        ])
        return deploy.run_deploy(_pkg(), _args(budget=500.0), lambda m: None)

    # D-12: one exit code per terminal status, so a caller can branch without parsing the report.
    # 1 stays reserved for internal/transport errors, which is also the unknown-status fallback —
    # a status this wrapper has never heard of must never read as success.

    def test_live_exits_zero(self):
        self.assertEqual(self._run("live"), 0)

    def test_installed_unobserved_exits_four(self):
        self.assertEqual(self._run("installed-unobserved"), 4)

    def test_refused_exits_two(self):
        self.assertEqual(self._run("refused"), 2)

    def test_failed_exits_three(self):
        self.assertEqual(self._run("failed"), 3)

    def test_pending_exits_six(self):
        self.assertEqual(self._run("pending"), 6)

    def test_an_unknown_overall_exits_one_never_zero(self):
        self.assertEqual(self._run("something-new"), 1)

    def test_a_snapshot_with_no_state_exits_one_not_pending(self):
        # A broken gateway contract is an internal error, not "still running".
        self.assertEqual(deploy.exit_code_for({}), 1)
        self.assertEqual(deploy.exit_code_for({"state": {}}), 1)
        self.assertEqual(deploy.exit_code_for(None), 1)

    def test_an_interrupted_job_exits_five(self):
        _cli.run_cli = FakeCli([
            _ok({"deployId": "dpl-a1b2c3d4", "phase": "reconcile"}),
            _ok({"state": {"status": "interrupted"}}),
            (0, "deploy dpl-a1b2c3d4 — interrupted by a gateway restart", ""),
        ])
        self.assertEqual(deploy.run_deploy(_pkg(), _args(budget=500.0), lambda m: None), 5)

    def test_a_job_still_running_when_the_wrapper_gives_up_exits_six(self):
        # The job continues in the background — "not finished" is the honest answer, not success.
        _cli.run_cli = FakeCli([
            _ok({"deployId": "dpl-a1b2c3d4", "phase": "reconcile"}),
            _ok({"state": {"status": "running", "phase": "create"}}),
            (0, "deploy dpl-a1b2c3d4 — running (phase: create)", ""),
        ])
        self.assertEqual(deploy.run_deploy(_pkg(), _args(budget=500.0, max_wait=0), lambda m: None), 6)


class BudgetArg(unittest.TestCase):
    def test_renders_a_bare_parseable_number(self):
        self.assertEqual(deploy.budget_arg(500.0), "500")
        self.assertEqual(deploy.budget_arg(298.5), "298.50")
        self.assertEqual(deploy.budget_arg(1_000_000.0), "1000000")
        for value in (500.0, 298.5, 1_000_000.0, 166.67):
            self.assertAlmostEqual(float(deploy.budget_arg(value)), value, places=2)

    def test_never_uses_grouping_or_a_currency_symbol(self):
        rendered = deploy.budget_arg(1234567.5)
        self.assertNotIn(",", rendered)
        self.assertNotIn("$", rendered)


if __name__ == "__main__":
    unittest.main()
