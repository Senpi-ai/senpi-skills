#!/usr/bin/env python3
"""A simulation start reaches "running" without waits nobody needed.

- The validate before a simulation start measures (`--measure`), so the deploy job does not run the
  scanner again to size it. A live deploy validates as before.
- A package's instance directories are validated side by side, and their messages stay in dir order.
- `deploy.py batch-wait <requestId>` reads the batch until no row is still starting, in one command.

    python3 -m pytest senpi-strategy-ops/tests/test_sim_start_speed.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import sys
import threading
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import _cli    # noqa: E402
import deploy  # noqa: E402
from test_deploy_wrapper import FakeCli, _ok, _proof_refused, _status  # noqa: E402


class _RunCli:
    """Stubs `_cli.run_cli`: answers per directory, records every argv and timeout."""

    def __init__(self, answers=None, barrier=None):
        self.answers, self.barrier = answers or {}, barrier
        self.calls, self.timeouts = [], []
        self.lock = threading.Lock()

    def __call__(self, args, timeout=60):
        with self.lock:
            self.calls.append(["openclaw", "senpi", *args[args.index("validate"):]])
            self.timeouts.append(timeout)
        if self.barrier is not None:
            self.barrier.wait()
        return self.answers.get(args[args.index("validate") + 1], (0, "PASS", ""))


class _Patched(unittest.TestCase):
    def setUp(self):
        saved = (_cli.run_cli, _cli._spawn, _cli.gateway_call, deploy.time.sleep, deploy.time.monotonic)
        self.addCleanup(lambda: (setattr(_cli, "run_cli", saved[0]), setattr(_cli, "_spawn", saved[1]),
                                 setattr(_cli, "gateway_call", saved[2]),
                                 setattr(deploy.time, "sleep", saved[3]),
                                 setattr(deploy.time, "monotonic", saved[4]),
                                 deploy.end_command_budget()))


class ASimulationValidateMeasures(_Patched):
    def test_a_simulation_validate_asks_for_the_measure(self):
        cli = _cli.run_cli = _RunCli()
        self.assertEqual(deploy.revalidate(["/pkg/hawk/main"], lambda m: None, measure=True)[0], True)
        self.assertEqual(cli.calls, [["openclaw", "senpi", "validate", "/pkg/hawk/main", "--stage", "live",
                                      "--measure"]])

    def test_a_live_validate_is_unchanged(self):
        cli = _cli.run_cli = _RunCli()
        deploy.revalidate(["/pkg/hawk/main"], lambda m: None)
        self.assertEqual(cli.calls, [["openclaw", "senpi", "validate", "/pkg/hawk/main", "--stage", "live"]])
        self.assertEqual(cli.timeouts, [deploy.VALIDATE_TIMEOUT])

    def test_a_measuring_validate_gets_room_for_the_probe(self):
        cli = _cli.run_cli = _RunCli()
        deploy.revalidate(["/pkg/hawk/main"], lambda m: None, measure=True)
        self.assertGreaterEqual(cli.timeouts[0], 3 * 120)

    def test_the_stale_proof_repair_of_a_simulation_measures(self):
        fake = _cli._spawn = FakeCli([_ok({"deployId": "dpl-a1b2c3d4"}),
                                      _status(_proof_refused("runtime_version_changed")),
                                      (0, "PASS", ""),
                                      _ok({"deployId": "dpl-e5f6a7b8"}),
                                      _status({"state": {"status": "done", "overall": "live"}})])
        deploy.time.sleep = lambda _s: None
        a = types.SimpleNamespace(budget=100.0, decision_model=None, tick_wait=None, max_wait=60, json=False,
                                  dry_run=False, simulation=True, user_said_yes=True, poll_budget=0)
        pkg = types.SimpleNamespace(dir="/pkg/spider", id="spider", version="1.0.0", instances=[])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = deploy.run_deploy(pkg, a, lambda m: None)
        self.assertEqual(code, 0)
        self.assertEqual(fake.argv_for("validate"),
                         [["openclaw", "senpi", "validate", "/pkg/spider", "--stage", "live", "--measure"]])


class InstancesValidateSideBySide(_Patched):
    DIRS = ["/pkg/spider/swing", "/pkg/spider/scalp"]

    def test_both_instances_run_at_once(self):
        # Each validate waits until the other has started: one after another never gets there.
        _cli.run_cli = _RunCli(barrier=threading.Barrier(2, timeout=5))
        ok, text, cut_short, killed = deploy.revalidate(self.DIRS, lambda m: None, measure=True)
        self.assertEqual((ok, text, cut_short, killed), (True, "", False, None))

    def test_messages_are_in_dir_order(self):
        _cli.run_cli = _RunCli()
        lines = []
        deploy.revalidate(self.DIRS, lines.append)
        self.assertEqual([l.split()[1] for l in lines], self.DIRS)

    def test_the_first_failing_dir_in_order_is_reported(self):
        _cli.run_cli = _RunCli({"/pkg/spider/swing": (1, "", "swing FAIL"),
                                "/pkg/spider/scalp": (1, "", "scalp FAIL")})
        ok, text, cut_short, killed = deploy.revalidate(self.DIRS, lambda m: None)
        self.assertFalse(ok)
        self.assertIn("swing FAIL", text)
        self.assertNotIn("scalp FAIL", text)
        self.assertEqual((cut_short, killed), (False, None))

    def test_a_kill_wins_over_a_plain_fail(self):
        _cli.run_cli = _RunCli({"/pkg/spider/swing": (1, "", "swing FAIL"),
                                "/pkg/spider/scalp": (-9, "", _cli.killed_note(-9))})
        ok, _text, _cut, killed = deploy.revalidate(self.DIRS, lambda m: None)
        self.assertFalse(ok)
        self.assertEqual(killed, -9)

    def test_every_dir_shares_the_command_budget(self):
        cli = _cli.run_cli = _RunCli({d: (-1, "", "timed out after 50s: x") for d in self.DIRS})
        clock = [1000.0]
        deploy.time.monotonic = lambda: clock[0]
        deploy.begin_command_budget(60)
        clock[0] += 7
        ok, _text, cut_short, killed = deploy.revalidate(self.DIRS, lambda m: None, measure=True)
        self.assertEqual(cli.timeouts, [60 - 7 - deploy.KILL_RESERVE_S] * 2)
        self.assertEqual((ok, cut_short, killed), (False, True, None))

    def test_a_spent_budget_runs_nothing(self):
        cli = _cli.run_cli = _RunCli()
        clock = [1000.0]
        deploy.time.monotonic = lambda: clock[0]
        deploy.begin_command_budget(60)
        clock[0] += 60
        ok, text, cut_short, _killed = deploy.revalidate(self.DIRS, lambda m: None)
        self.assertEqual(cli.calls, [])
        self.assertEqual((ok, cut_short), (False, True))
        self.assertIn("was not run", text)


def _row(label, phase, detail="", status="started", **kw):
    return {"label": label, "status": status, "phase": phase, "detail": detail, **kw}


def _answer(*rows):
    return {"requestId": "req-1", "batchId": "batch-x", "dryRun": False, "budgetUsd": 1000, "rows": list(rows),
            "box": {}, "creditsPerHourAfter": 30}


def _gw(kind="ok", payload=None, rc=0, message=None):
    return {"kind": kind, "payload": payload, "code": None, "message": message, "rc": rc, "stderr": "",
            "argv": None, "elapsed": 0, "transport": None}


class BatchWait(_Patched):
    """`batch-wait` reads `senpi.simulations.batchStatus` every few seconds until no row is queued,
    validating or deploying, then prints every row. It starts nothing."""

    def _run(self, answers, *extra):
        clock = [1000.0]
        deploy.time.monotonic = lambda: clock[0]
        deploy.time.sleep = lambda s: clock.__setitem__(0, clock[0] + s)
        self.calls = []

        def gateway_call(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append((method, params, clock[0]))
            clock[0] += 0.5
            return answers.pop(0) if len(answers) > 1 else answers[0]
        _cli.gateway_call = gateway_call
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                deploy.main(["deploy.py", "batch-wait", "req-1", *extra])
        self.clock = clock[0]
        return raised.exception.code, out.getvalue(), err.getvalue()

    def test_it_waits_until_every_row_is_running_and_prints_each(self):
        code, out, _err = self._run([
            _gw(payload=_answer(_row("base", "validating"), _row("lev3", "queued"))),
            _gw(payload=_answer(_row("base", "deploying"), _row("lev3", "validating"))),
            _gw(payload=_answer(_row("base", "live", "running", simulationId="simulation-1"),
                                _row("lev3", "live", "running")))])
        self.assertEqual(code, 0)
        self.assertEqual({m for m, _p, _t in self.calls}, {"senpi.simulations.batchStatus"})
        self.assertEqual(self.calls[0][1], {"requestId": "req-1"})
        self.assertEqual(len(self.calls), 3)
        self.assertLessEqual(self.calls[1][2] - self.calls[0][2], 5)
        self.assertIn("base", out)
        self.assertIn("lev3", out)
        self.assertIn("running", out)

    def test_a_refused_or_failed_row_has_its_own_exit(self):
        for row in (_row("lev3", "failed", "[E_SCANNER] it crashed"),
                    _row("lev3", "refused", "its start failed"),
                    _row("lev3", "interrupted", "the runtime restarted"),
                    _row("lev3", None, "box is full", status="BOX_FULL")):
            code, out, _err = self._run([_gw(payload=_answer(_row("base", "live", "running"), row))])
            self.assertEqual(code, deploy.BATCH_NOT_STARTED, row)
            self.assertIn(row["detail"], out)
        self.assertNotIn(deploy.BATCH_NOT_STARTED, (0, deploy.EXIT_PENDING, deploy.EXIT_INTERNAL))

    def test_it_stops_at_its_budget_with_the_still_running_exit(self):
        code, out, _err = self._run([_gw(payload=_answer(_row("base", "deploying", "deploying (install)")))],
                                    "--budget", "30")
        self.assertEqual(code, deploy.EXIT_PENDING)
        self.assertLessEqual(self.clock - 1000.0, 30)
        self.assertGreater(len(self.calls), 3)
        self.assertIn("deploying (install)", out)

    def test_the_default_budget_is_about_four_minutes(self):
        self._run([_gw(payload=_answer(_row("base", "deploying")))])
        self.assertTrue(200 <= self.clock - 1000.0 <= 260, self.clock - 1000.0)

    def test_json_is_one_document_with_the_rows(self):
        code, out, _err = self._run([_gw(payload=_answer(_row("base", "live", "running")))], "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["requestId"], "req-1")
        self.assertEqual(doc["outcome"], "started")
        self.assertEqual(doc["rows"][0]["label"], "base")

    def test_a_batch_that_was_never_started_is_could_not_check(self):
        code, _out, err = self._run([_gw(kind="refused", rc=1,
                                         message="[NOT_FOUND] no batch was started on this box with requestId req-1.")])
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("[NOT_FOUND]", err)

    def test_a_failed_read_is_retried_inside_the_budget(self):
        code, _out, _err = self._run([_gw(kind="timeout", rc=-1, message="no answer"),
                                      _gw(payload=_answer(_row("base", "live", "running")))])
        self.assertEqual(code, 0)

    def test_no_answer_at_all_is_could_not_check(self):
        code, _out, err = self._run([_gw(kind="timeout", rc=-1, message="no answer within 25s")], "--budget", "20")
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertIn("no answer within 25s", err)

    def test_a_killed_read_is_the_out_of_memory_stop(self):
        code, _out, err = self._run([_gw(kind="error", rc=-9, message="killed")])
        self.assertEqual(code, _cli.EXIT_OOM)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("out of memory", err)


if __name__ == "__main__":
    unittest.main()
