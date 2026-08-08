#!/usr/bin/env python3
"""Hermetic unit tests for the deploy.py wrapper's drive-the-verb loop.

No openclaw, no MCP, no network — `_cli.run_cli` is stubbed and every input is a plain dict. The
loop these tests pin is the one that broke in review: the wrapper polls `deploy status` with the
EXPLICIT deployId, so a controller that answers an id-addressed running job with `interrupted`
would make every wrapper-driven deploy exit within seconds of starting. Run:

    python3 senpi-strategy-ops/tests/test_deploy_wrapper.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import shutil
import sys
import tempfile
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


def _pkg(dir_="/pkg/spider", pid="spider", instances=()):
    return types.SimpleNamespace(dir=dir_, id=pid, version="1.0.0", instances=list(instances))


def _inst(name="main", runtime_name=None, pid="spider"):
    """A package instance as `verify` reads it: its name, and the runtime id its runtime.yaml declares."""
    return types.SimpleNamespace(name=name, runtime_name=runtime_name or f"{pid}-{name}")


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


def _status(snap):
    """A `deploy status` response as the verb REALLY answers it: the snapshot on stdout, and the
    JOB's D-12 code as the process exit code (`process.exitCode = exitCodeForDeploy(snap)`, set
    before the payload is printed, on the --json path too). `status` is a verdict surface, not a
    transport one — a stub that answers every status with rc=0 hides the entire refused/failed/
    pending class from these tests."""
    return (deploy.exit_code_for(snap), json.dumps(snap), "")


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
        _cli.run_cli = FakeCli([(2, "", "[E_FUNDS_BELOW_FLOOR] Requested $500.00 …")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        # The verb's own exit code is relayed as-is; only OUR transport failures become 1.
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("[E_FUNDS_BELOW_FLOOR]", err.getvalue())

    def test_a_spawn_failure_or_start_timeout_exits_one_not_refused(self):
        # `run_cli` returns rc=-1 for a spawn failure and for the 60s START_TIMEOUT. Neither is a gate
        # saying no: mapping them to 2 tells the agent "refused, nothing created" while a dispatched
        # job may be funding a wallet. Transport breakage is 1, per this wrapper's own contract.
        for rc, text in ((-1, _cli.SPAWN_FAILED_PREFIX + "openclaw"), (-1, "timed out after 60s: openclaw …")):
            _cli.run_cli = FakeCli([(rc, "", text)])
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
                deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
            self.assertEqual(ctx.exception.code, 1)

    def test_a_start_timeout_points_at_deploy_status_because_the_job_may_be_running(self):
        # The START call timed out AFTER the gateway was reached: same shape as the no-deployId case.
        # The exit code alone doesn't tell the agent that; the stderr it reads at failure time must.
        _cli.run_cli = FakeCli([(-1, "", "timed out after 60s: openclaw senpi deploy -p /pkg/spider")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("openclaw senpi deploy status", err.getvalue())
        self.assertIn("may be running", err.getvalue().lower())

    def test_a_spawn_failure_does_not_claim_a_job_may_be_running(self):
        # `openclaw` was never executed, so nothing was dispatched — pointing at `deploy status` here
        # would be a hedge over a known-false state (and the command it names cannot run either).
        _cli.run_cli = FakeCli([(-1, "", _cli.SPAWN_FAILED_PREFIX + "openclaw")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        self.assertEqual(ctx.exception.code, 1)
        self.assertNotIn("may be running", err.getvalue().lower())

    def test_a_start_with_no_deploy_id_exits_one_and_says_the_job_may_be_running(self):
        # The verb exited 0 — it accepted the deploy — but no deployId came back, so this wrapper
        # cannot follow the job. reconcile/create/fund may be in flight RIGHT NOW.
        _cli.run_cli = FakeCli([(0, "started, watching…", "")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        self.assertEqual(ctx.exception.code, 1)
        msg = err.getvalue()
        self.assertIn("openclaw senpi deploy status", msg)
        self.assertIn("may be running", msg.lower())
        self.assertIn("unknown", msg.lower())
        self.assertNotIn("refused", msg.lower())          # never "a gate said no"
        self.assertNotIn("nothing was created", msg.lower())

    def test_a_runtime_without_the_deploy_verb_teaches_the_plugin_update(self):
        # A box whose plugin predates `senpi deploy` (a wedged self-update — a real fleet class)
        # answers the START call with its CLI parser's unknown-command error. Relayed bare, that read
        # as transport breakage: an unsteered exit 1 whose taught handling is "the job may be running,
        # read deploy status" — over a box where nothing was dispatched and where that command does
        # not exist either.
        for rc, text in ((1, "error: unknown command 'deploy'"),
                         (1, "Unknown argument: deploy"),
                         (2, "Unknown arguments: deploy, budget")):
            _cli.run_cli = FakeCli([(rc, "", text)])
            err = io.StringIO()
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
                deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
            msg = err.getvalue()
            # 1, never the parser's own 2: "the question could not be answered", not "a gate said no".
            self.assertEqual(ctx.exception.code, 1, text)
            self.assertIn(text, msg)                            # the CLI's own words, still relayed
            self.assertIn("openclaw plugins install @senpi-ai/runtime", msg)  # the taught update path
            self.assertIn("nothing was dispatched", msg.lower())
            self.assertNotIn("may be running", msg.lower())     # nothing is

    def test_a_verb_refusal_is_never_read_as_a_missing_verb(self):
        # A message carrying a bracketed [CODE] is the VERB answering, whatever words follow it.
        _cli.run_cli = FakeCli([(2, "", "[INVALID_REQUEST] unknown argument for deploy: --bogus")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        self.assertEqual(ctx.exception.code, 2)
        self.assertNotIn("plugins install", err.getvalue())

    def test_dry_run_prints_the_command_and_never_calls_the_cli(self):
        fake = FakeCli([])
        _cli.run_cli = fake
        with self.assertRaises(SystemExit) as ctx:
            deploy.start_deploy(_pkg(), _args(budget=500.0, dry_run=True), lambda m: None)
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(fake.calls, [])


class ForwardedMaxWaitDefault(unittest.TestCase):
    """One flag, one meaning, one clock each: the number we FORWARD is the verb's wallet-ACTIVE budget
    (and it also sizes the job's wall-clock watchdog), while how long this script polls is its own
    budget. Defaulting the forwarded flag to 600 silently tripled the job cap on a multi-instance
    package. The poll budget has its own ceiling: an agent's tool harness kills the exec at ~180s, so
    a 600s foreground poll lost the report AND the exit code while the detached job ran on."""

    # The agent tool/session timeout the whole detached design exists to stay under (~180s).
    TOOL_TIMEOUT = 180

    def setUp(self):
        self._cli_real = _cli.run_cli
        self._ensure, self._validate, self._wait = (
            deploy.ensure_pkg, deploy.full_validate, deploy.wait_for_terminal)
        deploy.ensure_pkg = lambda arg, ref, log: _pkg()
        deploy.full_validate = lambda pkg: []
        self.waited = []
        deploy.wait_for_terminal = lambda did, budget, log: (
            self.waited.append(budget) or {"state": {"status": "done", "overall": "live"}})

    def tearDown(self):
        _cli.run_cli = self._cli_real
        (deploy.ensure_pkg, deploy.full_validate,
         deploy.wait_for_terminal) = (self._ensure, self._validate, self._wait)

    def _create(self, *extra):
        fake = FakeCli([_ok({"deployId": "dpl-a1b2c3d4"}), (0, "done — live", "")])
        _cli.run_cli = fake
        with self.assertRaises(SystemExit) as ctx:
            deploy.main(["deploy.py", "create", "spider", "--budget", "300", *extra])
        return fake.calls[0], ctx.exception.code

    def test_the_forwarded_default_is_the_verbs_own_150(self):
        argv, code = self._create()
        self.assertEqual(argv[argv.index("--max-wait") + 1], "150")
        self.assertEqual(code, 0)

    def test_an_explicit_value_is_forwarded_verbatim(self):
        argv, _code = self._create("--max-wait", "900")
        self.assertEqual(argv[argv.index("--max-wait") + 1], "900")

    def test_the_default_poll_budget_returns_inside_the_tool_timeout(self):
        # A job still running at the lapse is the PENDING path — exit 6, snapshot printed, "watch it
        # with deploy status" — which is the honest outcome. A budget past the harness timeout is
        # not: the exec is killed, so the agent gets neither the report nor a code.
        self.assertLess(deploy.POLL_BUDGET, self.TOOL_TIMEOUT)
        self._create()
        self.assertEqual(self.waited, [deploy.POLL_BUDGET])

    def test_a_longer_explicit_max_wait_widens_the_poll_budget_too(self):
        self._create("--max-wait", "900")
        self.assertEqual(self.waited, [900])

    def test_a_shorter_explicit_max_wait_shortens_the_poll_budget_too(self):
        # Explicit wins in BOTH directions: a caller asking for a fast return must get one, not a
        # 600s floor they have no flag to lower.
        argv, _code = self._create("--max-wait", "30")
        self.assertEqual(argv[argv.index("--max-wait") + 1], "30")
        self.assertEqual(self.waited, [30])


class WaitForTerminal(unittest.TestCase):
    """The poll loop always passes the explicit deployId — the shape the C1/C2 bug broke."""

    def setUp(self):
        self._real_cli, self._real_sleep = _cli.run_cli, deploy.time.sleep
        deploy.time.sleep = lambda _s: None

    def tearDown(self):
        _cli.run_cli, deploy.time.sleep = self._real_cli, self._real_sleep

    def test_polls_with_the_explicit_id_until_the_job_is_terminal(self):
        fake = FakeCli([
            _status({"state": {"status": "running", "phase": "create"}}),
            _status({"state": {"status": "running", "phase": "install"}}),
            _status({"state": {"status": "done", "overall": "live"}}),
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
        fake = FakeCli([_status({"state": {"status": "running", "phase": "create"}})] * 40)
        _cli.run_cli = fake
        snap = deploy.wait_for_terminal("dpl-a1b2c3d4", 0, lambda m: None)
        self.assertEqual(snap["state"]["status"], "running")

    def test_an_interrupted_snapshot_is_terminal(self):
        _cli.run_cli = FakeCli([_status({"state": {"status": "interrupted"}})])
        snap = deploy.wait_for_terminal("dpl-a1b2c3d4", 600, lambda m: None)
        self.assertEqual(snap["state"], {"status": "interrupted"})

    def test_unreadable_status_never_crashes_the_loop(self):
        _cli.run_cli = FakeCli([(1, "", "gateway down"),
                                _status({"state": {"status": "done", "overall": "live"}})])
        snap = deploy.wait_for_terminal("dpl-a1b2c3d4", 600, lambda m: None)
        self.assertEqual(snap["state"]["overall"], "live")


class JsonModeStdout(unittest.TestCase):
    """In `--json` mode stdout is a MACHINE surface: exactly one parseable JSON document, on every
    path. The still-running trailer was printed to the same stdout right after the snapshot, so
    `json.loads` broke on precisely the outcome an agent has to parse to decide whether to keep
    watching. Prose belongs on stderr there."""

    def setUp(self):
        self._real_cli, self._real_sleep = _cli.run_cli, deploy.time.sleep
        deploy.time.sleep = lambda _s: None

    def tearDown(self):
        _cli.run_cli, deploy.time.sleep = self._real_cli, self._real_sleep

    def _run_json(self, snap):
        self.addCleanup(setattr, deploy, "POLL_BUDGET", deploy.POLL_BUDGET)
        deploy.POLL_BUDGET = 0          # one poll, then report whatever the job is doing
        _cli.run_cli = FakeCli([_ok({"deployId": "dpl-a1b2c3d4"}), _status(snap)])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = deploy.run_deploy(_pkg(), _args(budget=500.0, max_wait=0, json=True),
                                     lambda m: None)
        return code, out.getvalue(), err.getvalue()

    def test_a_still_running_job_leaves_stdout_parseable(self):
        code, out, err = self._run_json({"state": {"status": "running", "phase": "create"}})
        self.assertEqual(code, 6)
        self.assertEqual(json.loads(out)["state"]["status"], "running")   # ONE document, nothing after
        self.assertIn("Still running", err)                                # the steer is still said
        self.assertIn("openclaw senpi deploy status", err)

    def test_a_terminal_job_leaves_stdout_parseable(self):
        code, out, _err = self._run_json({"state": {"status": "done", "overall": "live"}})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"]["overall"], "live")


class StatusIsAVerdictNotATransport(unittest.TestCase):
    """`deploy status` exits with the JOB's D-12 code while printing the snapshot. Reading that code
    as a health signal made `status_snapshot` discard every non-live snapshot: a refused deploy then
    polled as unreadable for the whole budget and reported a transport error (exit 1) instead of the
    refusal, and `deploy.py status` after one printed "No deploy job recorded — start one: create
    <id> --budget <usd>", steering at a FUNDED deploy over a job that had just been refused."""

    def setUp(self):
        self._real_cli, self._real_sleep = _cli.run_cli, deploy.time.sleep
        deploy.time.sleep = lambda _s: None

    def tearDown(self):
        _cli.run_cli, deploy.time.sleep = self._real_cli, self._real_sleep

    def test_a_snapshot_is_read_whatever_exit_code_status_carries(self):
        for snap in ({"state": {"status": "done", "overall": "refused"}},
                     {"state": {"status": "done", "overall": "failed"}},
                     {"state": {"status": "interrupted"}},
                     {"state": {"status": "running", "phase": "create"}}):
            _cli.run_cli = FakeCli([_status(snap)])
            self.assertEqual(deploy.status_snapshot("dpl-a1b2c3d4"), snap)

    def test_only_a_call_that_produced_no_snapshot_is_unreadable(self):
        for response in ((-1, "", _cli.SPAWN_FAILED_PREFIX + "openclaw"),
                         (-1, "", "timed out after 30s: openclaw senpi deploy status"),
                         (1, "", "[NOT_FOUND] No deploy has been started on this agent.")):
            _cli.run_cli = FakeCli([response])
            self.assertIsNone(deploy.status_snapshot(None))

    def test_a_refused_job_is_relayed_with_its_own_exit_code_not_polled_to_a_transport_error(self):
        rendered = ("deploy dpl-a1b2c3d4 — done — refused\n"
                    "  preflight: [E_FUNDS_BELOW_FLOOR] no budget is valid — nothing was created")
        refused = {"state": {"status": "done", "overall": "refused"}}
        fake = FakeCli([_ok({"deployId": "dpl-a1b2c3d4"}), _status(refused), (2, rendered, "")])
        _cli.run_cli = fake
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = deploy.run_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        self.assertEqual(code, 2)
        self.assertIn(rendered, out.getvalue())
        self.assertNotIn("could not read", (out.getvalue() + err.getvalue()).lower())
        # One poll: the job was terminal on the first read, not re-read for the whole budget.
        polls = [c for c in fake.calls if "status" in c and "--json" in c]
        self.assertEqual(len(polls), 1)


class RunDeployExitCodes(unittest.TestCase):
    def setUp(self):
        self._real_cli, self._real_sleep = _cli.run_cli, deploy.time.sleep
        deploy.time.sleep = lambda _s: None

    def tearDown(self):
        _cli.run_cli, deploy.time.sleep = self._real_cli, self._real_sleep

    def _run(self, overall):
        snap = {"state": {"status": "done", "overall": overall}}
        _cli.run_cli = FakeCli([
            _ok({"deployId": "dpl-a1b2c3d4", "phase": "reconcile"}),
            _status(snap),
            (deploy.exit_code_for(snap), f"deploy dpl-a1b2c3d4 — done — {overall}", ""),
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
            _status({"state": {"status": "interrupted"}}),
            (5, "deploy dpl-a1b2c3d4 — interrupted by a gateway restart", ""),
        ])
        self.assertEqual(deploy.run_deploy(_pkg(), _args(budget=500.0), lambda m: None), 5)

    def test_a_job_still_running_when_the_wrapper_gives_up_exits_six(self):
        # The job continues in the background — "not finished" is the honest answer, not success.
        self.addCleanup(setattr, deploy, "POLL_BUDGET", deploy.POLL_BUDGET)
        deploy.POLL_BUDGET = 0
        _cli.run_cli = FakeCli([
            _ok({"deployId": "dpl-a1b2c3d4", "phase": "reconcile"}),
            _status({"state": {"status": "running", "phase": "create"}}),
            (6, "deploy dpl-a1b2c3d4 — running (phase: create)", ""),
        ])
        self.assertEqual(deploy.run_deploy(_pkg(), _args(budget=500.0, max_wait=0), lambda m: None), 6)


class StructuralGateRefusal(unittest.TestCase):
    """The wrapper's own pre-deploy pass is a GATE: a `full_validate` failure on create/runtime is
    deterministic, nothing was created, and re-running refuses identically — D-12's `2`. It exited
    `1`, whose taught meaning is the opposite ("the question could not be answered; the job may well
    be running: read `deploy status`"), which invites a blind retry of a refusal. (`verify` is not in
    this set: it deploys nothing, so there is no money for a pre-deploy gate to stand in front of.)"""

    def setUp(self):
        self._cli_real = _cli.run_cli
        self._ensure, self._validate = deploy.ensure_pkg, deploy.full_validate
        self._universe = deploy.universe_report        # hermetic: `validate` would hit the network
        deploy.ensure_pkg = lambda arg, ref, log: _pkg()
        deploy.full_validate = lambda pkg: ["instance main: set runtime name: spider-main"]
        deploy.universe_report = lambda pkg: ([], None)

    def tearDown(self):
        _cli.run_cli = self._cli_real
        deploy.ensure_pkg, deploy.full_validate = self._ensure, self._validate
        deploy.universe_report = self._universe

    def test_a_structural_refusal_exits_two_and_starts_nothing(self):
        for cmd in ("create", "runtime"):
            fake = FakeCli([])
            _cli.run_cli = fake
            err = io.StringIO()
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
                deploy.main(["deploy.py", cmd, "spider", "--budget", "300"])
            self.assertEqual(ctx.exception.code, 2, cmd)
            self.assertIn("to fix before deploy", err.getvalue())
            self.assertEqual(fake.calls, [], cmd)   # nothing dispatched, so nothing to go read

    def test_validate_keeps_the_same_refusal_code(self):
        # One gate, one code: the standalone preflight and the pre-deploy pass are the same check.
        _cli.run_cli = FakeCli([])
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
            deploy.main(["deploy.py", "validate", "spider"])
        self.assertEqual(ctx.exception.code, 2)


class StatusSubcommand(unittest.TestCase):
    """`status` reads the agent's LAST deploy job — there is one record per agent and it is not
    package-addressed. So a named package must be held against the snapshot, never decorated with it."""

    def setUp(self):
        self._real = _cli.run_cli

    def tearDown(self):
        _cli.run_cli = self._real

    @staticmethod
    def _snap(package_id="polar", overall="live"):
        return {"meta": {"deployId": "dpl-a1b2c3d4", "packageId": package_id},
                "state": {"status": "done", "overall": overall}}

    def _run(self, argv, responses):
        _cli.run_cli = FakeCli(responses)
        err, out = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            deploy.main(argv)
        return ctx.exception.code, out.getvalue(), err.getvalue()

    def test_a_package_that_is_not_the_last_jobs_package_is_refused(self):
        # `deploy.py status spider` right after a polar deploy used to print polar's report and polar's
        # exit code under a spider prompt — an invitation to bind the wrong package's verdict.
        code, out, err = self._run(["deploy.py", "status", "spider"], [_status(self._snap("polar"))])
        # 1, not 2: 2 is "the deploy was refused, nothing created", which is false here — polar's
        # deploy may be live. This is "could not answer the question you asked".
        self.assertEqual(code, 1)
        self.assertIn("polar", err)
        self.assertIn("spider", err)
        self.assertNotIn("live", out)
        # 1 is also the retry class, so the refusal must say retrying is pointless and that it is
        # reporting no deploy state at all.
        self.assertIn("no deploy state", err.lower())
        self.assertIn("will refuse again", err.lower())

    def test_the_named_package_matching_the_job_reports_the_jobs_code(self):
        code, _out, _err = self._run(
            ["deploy.py", "status", "spider"],
            [_status(self._snap("spider")), (0, "deploy dpl-a1b2c3d4 — done — live", "")])
        self.assertEqual(code, 0)

    def test_a_package_dir_path_matches_on_its_basename(self):
        code, _out, _err = self._run(
            ["deploy.py", "status", "/data/workspace/strategies/spider"],
            [_status(self._snap("spider")), (0, "deploy dpl-a1b2c3d4 — done — live", "")])
        self.assertEqual(code, 0)

    def test_status_takes_no_package_at_all(self):
        code, _out, _err = self._run(
            ["deploy.py", "status"],
            [_status(self._snap("polar")), (0, "deploy dpl-a1b2c3d4 — done — live", "")])
        self.assertEqual(code, 0)

    def test_an_argument_that_names_no_package_id_never_refuses_against_an_empty_name(self):
        # `Path(".").name` is "" — comparing that to the job's packageId refused naming '' as the
        # package the caller asked for, which is a refusal built out of nothing.
        for arg in (".", "/", "./"):
            code, _out, err = self._run(
                ["deploy.py", "status", arg],
                [_status(self._snap("polar")), (0, "deploy dpl-a1b2c3d4 — done — live", "")])
            self.assertEqual(code, 0, f"{arg!r} must not refuse")
            self.assertNotIn("refusing", err.lower())
            self.assertIn("names no package", err.lower())   # said out loud, not silently accepted

    def test_ref_on_status_is_a_legible_refusal_not_an_argparse_usage_error(self):
        # argparse's own usage error exits 2 — "the deploy was refused" in the taught map. `status`
        # resolves no package, so --ref is meaningless here and must say so at exit 1 instead.
        fake = FakeCli([])
        _cli.run_cli = fake
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            deploy.main(["deploy.py", "status", "spider", "--ref", "some-branch"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("--ref", err.getvalue())
        self.assertEqual(fake.calls, [])

    def test_a_snapshot_that_names_no_package_is_flagged_never_silently_bound(self):
        snap = {"meta": {"deployId": "dpl-a1b2c3d4"}, "state": {"status": "done", "overall": "live"}}
        code, _out, err = self._run(
            ["deploy.py", "status", "spider"], [_status(snap), (0, "deploy dpl-a1b2c3d4 — done — live", "")])
        self.assertEqual(code, 0)
        self.assertIn("spider", err)  # says out loud that it could not be confirmed

    def test_a_non_live_job_is_reported_never_read_as_no_job_recorded(self):
        # `status` after a refused (or failed, or still-running) job used to print "No deploy job
        # recorded on this agent. Start one: create <id> --budget <usd>" — a fabricated absence, and
        # a steer at a FUNDED deploy over a job that had just been refused.
        for overall, want_code in (("refused", 2), ("failed", 3)):
            rendered = f"deploy dpl-a1b2c3d4 — done — {overall}"
            code, out, err = self._run(
                ["deploy.py", "status"],
                [_status(self._snap("polar", overall)), (want_code, rendered, "")])
            self.assertEqual(code, want_code)
            self.assertIn(rendered, out)
            self.assertNotIn("no deploy job", err.lower())

    def test_a_still_running_job_reports_pending_not_an_absent_job(self):
        snap = {"meta": {"deployId": "dpl-a1b2c3d4", "packageId": "polar"},
                "state": {"status": "running", "phase": "create"}}
        code, out, err = self._run(
            ["deploy.py", "status"], [_status(snap), (6, "deploy dpl-a1b2c3d4 — running (phase: create)", "")])
        self.assertEqual(code, 6)
        self.assertIn("running", out)
        self.assertNotIn("no deploy job", err.lower())

    def test_a_status_call_that_produced_no_snapshot_relays_the_verbs_own_words(self):
        # The one real absence: the verb answers `[NOT_FOUND]` (carrying its own start command) and
        # prints no snapshot. Relay THAT — never a locally-composed absence, and never a `create
        # <id> --budget <usd>` this wrapper invented on top of it.
        not_found = "[NOT_FOUND] No deploy has been started on this agent. Start one: senpi deploy -p <dir> --budget <usd>"
        code, _out, err = self._run(["deploy.py", "status"], [(1, "", not_found)])
        self.assertEqual(code, 1)
        self.assertIn("[NOT_FOUND]", err)
        self.assertIn("openclaw senpi deploy status", err)

    def test_an_unreadable_status_call_never_claims_there_is_no_job(self):
        code, _out, err = self._run(
            ["deploy.py", "status"], [(-1, "", _cli.SPAWN_FAILED_PREFIX + "openclaw")])
        self.assertEqual(code, 1)
        self.assertIn("command not found", err)
        self.assertNotIn("no deploy job recorded", err.lower())


class UniverseGateOwnership(unittest.TestCase):
    """The live-universe invariant belongs to the VERB now (`[E_UNIVERSE_NOT_LIVE]`, pre-money,
    fail-closed on an unreadable instrument list). This wrapper's own fail-open preflight is gone:
    keeping a gate that proceeds when the list is unreachable in FRONT of one that refuses is the
    two-producer drift the move exists to end."""

    def setUp(self):
        self._real_cli, self._real_sleep = _cli.run_cli, deploy.time.sleep
        deploy.time.sleep = lambda _s: None

    def tearDown(self):
        _cli.run_cli, deploy.time.sleep = self._real_cli, self._real_sleep

    def test_action_subcommands_have_no_local_universe_gate(self):
        self.assertFalse(hasattr(deploy, "universe_preflight"))

    def test_universe_refusal_relays_verbatim_with_exit_2(self):
        # The verb's refusal reaches the wrapper through the ordinary status relay: no local
        # re-derivation, no second wording — the report is printed exactly as the verb rendered it.
        rendered = ("deploy dpl-a1b2c3d4 — done — refused\n"
                    "  reconcile: [E_UNIVERSE_NOT_LIVE] 1 hardcoded instrument(s) in this package "
                    "are not live on Hyperliquid. Nothing was created — \"xyz:NASDAQ\"")
        _cli.run_cli = FakeCli([
            _ok({"deployId": "dpl-a1b2c3d4", "phase": "reconcile"}),
            _status({"state": {"status": "done", "overall": "refused"}}),
            (2, rendered, ""),
        ])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = deploy.run_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        self.assertEqual(code, 2)
        self.assertIn(rendered, out.getvalue())


class ValidateUniverseHook(unittest.TestCase):
    """`validate` is the taught step-0 preflight, so it reports a dead universe locally — using the
    SAME `validate_universe` predicates the verb ports, so the two cannot disagree on one live list.
    It is a report, never the invariant: the verb enforces before money moves, so an unreachable
    instrument list here is a LOUD note, never a silent pass and never a blocked deploy."""

    def setUp(self):
        self._real_cli = _cli.run_cli
        self._ensure, self._full_validate = deploy.ensure_pkg, deploy.full_validate
        deploy.ensure_pkg = lambda arg, ref, log: _pkg()
        deploy.full_validate = lambda pkg: []
        self._saved_vu = sys.modules.get("validate_universe")

    def tearDown(self):
        _cli.run_cli = self._real_cli
        deploy.ensure_pkg, deploy.full_validate = self._ensure, self._full_validate
        if self._saved_vu is None:
            sys.modules.pop("validate_universe", None)
        else:
            sys.modules["validate_universe"] = self._saved_vu

    def _install_vu(self, *, unknown=(), raises=None, scan_raises=None):
        module = types.ModuleType("validate_universe")

        def package_tickers(pkg_dir):
            if scan_raises is not None:
                raise scan_raises
            return {"BTC", "xyz:NASDAQ"}

        module.package_tickers = package_tickers
        module.unknown_tickers = lambda tickers, live: list(unknown)

        def live_instruments():
            if raises is not None:
                raise raises
            return {"BTC"}

        module.live_instruments = live_instruments
        sys.modules["validate_universe"] = module

    def _run_validate(self, *extra):
        _cli.run_cli = FakeCli([])
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            deploy.main(["deploy.py", "validate", "spider", *extra])
        return ctx.exception.code, out.getvalue(), err.getvalue()

    def test_a_dead_name_is_a_validate_error_at_exit_2(self):
        self._install_vu(unknown=["xyz:NASDAQ"])
        code, out, err = self._run_validate()
        self.assertEqual(code, 2)
        self.assertIn("xyz:NASDAQ", err)
        self.assertIn("E_UNIVERSE_NOT_LIVE", err)   # names the refusal the deploy verb will raise
        self.assertNotIn("deploy-ready", out)

    def test_an_unreachable_instrument_list_is_a_loud_note_never_a_silent_pass(self):
        self._install_vu(raises=RuntimeError("no SENPI_AUTH_TOKEN"))
        code, out, err = self._run_validate()
        self.assertEqual(code, 0)          # it never blocks: the verb owns the money-path gate
        self.assertIn("no SENPI_AUTH_TOKEN", err)
        self.assertIn("instrument list", err)          # names WHAT could not be read
        self.assertIn("nothing about the universe", err.lower())
        self.assertIn("senpi deploy", err)  # …and says who does enforce it
        self.assertIn("deploy-ready", out)  # the structural verdict still stands, with the note beside it

    def test_a_package_scan_failure_reads_as_a_package_problem_not_a_network_one(self):
        # The try used to wrap the package read too, so malformed YAML in the user's own package was
        # reported as "the live list could not be read" — pointing at the network and at the verb for
        # a problem sitting in a file they own.
        self._install_vu(scan_raises=ValueError("while scanning runtime.yaml: bad indent"))
        code, out, err = self._run_validate()
        self.assertEqual(code, 0)
        self.assertIn("bad indent", err)
        self.assertIn("scan this package", err)
        self.assertNotIn("instrument list", err)
        # A PyYAML error raised from a string carries no path, so the note must not promise a
        # filename it cannot produce — it points at the directory it DOES know instead.
        self.assertNotIn("the file it names", err)
        self.assertIn("/pkg/spider", err)
        self.assertIn("deploy-ready", out)

    def test_a_live_universe_leaves_the_clean_output_unchanged(self):
        self._install_vu(unknown=[])
        code, out, err = self._run_validate()
        self.assertEqual(code, 0)
        self.assertIn("deploy-ready", out)
        self.assertEqual(err, "")

    def test_json_carries_the_universe_error_and_the_note(self):
        self._install_vu(unknown=["xyz:NASDAQ"])
        code, out, _err = self._run_validate("--json")
        payload = json.loads(out)
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "invalid")
        self.assertTrue(any("xyz:NASDAQ" in e for e in payload["errors"]))

        self._install_vu(raises=RuntimeError("no SENPI_AUTH_TOKEN"))
        code, out, _err = self._run_validate("--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "valid")
        self.assertIn("no SENPI_AUTH_TOKEN", payload["note"])


WALLET = "0x1234567890abcdef1234567890abcdef12345678"
OTHER_WALLET = "0xfeedfacefeedfacefeedfacefeedfacefeedface"


def _strategy(name="spider-main", wallet=WALLET, status="ACTIVE", skill="spider", funded=300,
              initial_budget=None):
    """One `strategy_list` record.

    `skill=None` drops `strategyMetadata` entirely — the real shape of a wallet the backend never
    attributed, where `strategy_skill` falls back to the strategy NAME. `funded=None` drops
    `totalFunded`, so only `initialBudget` (the REQUESTED figure) is left to read."""
    s = {"strategyId": "str-1234abcd", "strategyName": name, "status": status,
         "strategyWalletAddress": wallet}
    if funded is not None:
        s["totalFunded"] = funded
    if initial_budget is not None:
        s["initialBudget"] = initial_budget
    if skill is not None:
        s["strategyMetadata"] = {"skillName": skill}
    return s


def _runtime_table(*rows):
    """`runtime list` as the CLI really prints it: header, then id / wallet / source / status."""
    text = "ID            WALLET                                      SOURCE   STATUS\n"
    for name, wallet, status in rows:
        text += f"{name}   {wallet}   package   {status}\n"
    return text


class RouterCli:
    """Routes a stubbed `run_cli` by argv shape rather than a queue: `verify` composes several
    different read-only surfaces and their ORDER is an implementation detail — what is never an
    implementation detail is that none of them is `openclaw senpi deploy -p …`."""

    NO_DEPLOY_JOB = (1, "", "[NOT_FOUND] No deploy has been started on this agent.")

    def __init__(self, runtime_list=None, status_json=None, deploy_status=None):
        self.calls = []
        self.runtime_list = runtime_list if runtime_list is not None else (
            0, _runtime_table(("spider-main", WALLET, "running")), "")
        self.status_json = status_json if status_json is not None else _ok(
            {"statuses": [{"name": "spider-main", "overallHealth": "healthy"}]})
        self.deploy_status = deploy_status if deploy_status is not None else self.NO_DEPLOY_JOB

    def __call__(self, args, timeout=60):
        self.calls.append(list(args))
        if args[:4] == ["openclaw", "senpi", "runtime", "list"]:
            return self.runtime_list
        if args[:4] == ["openclaw", "senpi", "deploy", "status"]:
            return self.deploy_status
        if args[:3] == ["openclaw", "senpi", "status"]:
            return self.status_json
        raise AssertionError(f"verify made an unexpected CLI call: {args}")

    @property
    def deploy_dispatches(self):
        """Every call that STARTS the deploy verb (`senpi deploy -p <pkg>`) — must always be empty."""
        return [c for c in self.calls if c[:3] == ["openclaw", "senpi", "deploy"] and "-p" in c]


_UNSET = object()


class FakeMCP:
    def __init__(self, strategies=(), raises=None, payload=_UNSET):
        self.strategies, self.raises, self.payload = list(strategies), raises, payload

    def mcp_call(self, tool, timeout=15, **kw):
        if self.raises is not None:
            raise self.raises
        assert tool == "strategy_list", tool
        if self.payload is not _UNSET:
            return self.payload          # a shape the extractor may not recognise
        return {"strategies": self.strategies}


class VerifyHarness:
    """Drives `deploy.py verify spider` against stubbed read-only surfaces (mixed into a TestCase)."""

    def setUp(self):
        self._cli_real, self._ensure = _cli.run_cli, deploy.ensure_pkg
        self._local = deploy.local_pkg
        self._mcp = getattr(deploy, "MCPClient", None)
        # `verify` resolves LOCALLY (it fetches nothing) — `ensure_pkg` stays stubbed to a raiser so a
        # regression that routes verify back through the fetching resolver fails here, loudly.
        deploy.ensure_pkg = lambda arg, ref, log: self.fail("verify resolved through the FETCH path")
        deploy.local_pkg = lambda arg: self.pkg
        self.pkg = _pkg(instances=[_inst("main")])

    def tearDown(self):
        _cli.run_cli, deploy.ensure_pkg = self._cli_real, self._ensure
        deploy.local_pkg = self._local
        if self._mcp is not None:
            deploy.MCPClient = self._mcp

    def _verify(self, *extra, strategies=(_strategy(),), mcp_raises=None, mcp_payload=_UNSET,
                **router_kw):
        router = RouterCli(**router_kw)
        _cli.run_cli = router
        deploy.MCPClient = lambda *a, **k: FakeMCP(strategies, mcp_raises, mcp_payload)
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            deploy.main(["deploy.py", "verify", "spider", *extra])
        return ctx.exception.code, out.getvalue(), err.getvalue(), router


class VerifyIsAReadOnlyCheck(VerifyHarness, unittest.TestCase):
    """`deploy.py verify <id>` NEVER starts the deploy verb.

    The regression this pins: verify was converged onto the money-moving verb, so an agent following
    an older transcript or habit ("just re-check it: deploy.py verify spider") against a package whose
    funded wallet was deliberately left runtime-less mid-triage got the runtime installed and the
    wallet opening real positions — from a command documented for years as a pure check. Verify is a
    composite of read-only surfaces (`strategy_list` + `runtime list` + `senpi status --json`, plus a
    verbatim relay of the last deploy job's warns) that only ever QUOTES what it read, and it fails
    CLOSED: a read it could not make is `could-not-check` (1), never a verdict."""

    # ---- (a) the clean case: verified, and NOTHING was dispatched ----

    def test_a_live_healthy_package_verifies_and_starts_no_deploy(self):
        code, out, _err, router = self._verify()
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED", out)
        self.assertEqual(router.deploy_dispatches, [])      # the core pin

    def test_the_json_verdict_is_one_parseable_document_on_stdout(self):
        code, out, _err, router = self._verify("--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["verdict"], "verified")
        self.assertEqual(payload["id"], "spider")
        self.assertEqual(router.deploy_dispatches, [])

    # ---- (b) funded wallet, no runtime: the resume is NAMED, never RUN ----

    def test_a_funded_wallet_with_no_runtime_is_not_verified_and_names_the_resume(self):
        code, out, err, router = self._verify(
            runtime_list=(0, _runtime_table(), ""),
            status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertIn("NOT VERIFIED", text)
        self.assertIn("deploy.py runtime spider", text)
        # The step it names moves money — say so where it is named, not in a doc the agent never reads.
        self.assertIn("installs", text.lower())
        self.assertIn("starts trading", text.lower())
        self.assertEqual(router.deploy_dispatches, [])      # named, not run
        self.assertNotIn("close.py", text)                  # never a teardown command

    # ---- (c) no wallet at all: create is named (with a budget), still nothing dispatched ----

    def test_no_live_wallet_is_not_verified_and_names_create(self):
        code, out, err, router = self._verify(
            strategies=(), runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertIn("NOT VERIFIED", text)
        self.assertIn("deploy.py create spider --budget", text)
        self.assertEqual(router.deploy_dispatches, [])

    def test_an_unnamed_package_wallet_never_steers_at_create(self):
        # Wallets for this package EXIST but none carries this instance's name (a create-time
        # name-rejection fallback). Steering at `create` there funds a SECOND wallet beside a
        # possibly-live funded one — the refusal `_recover_wallet` existed to make. (A
        # single-instance package has no such ambiguity: its lone live wallet IS the instance.)
        self.pkg = _pkg(instances=[_inst("swing"), _inst("scalp")])
        code, out, err, router = self._verify(
            strategies=(_strategy(name="unnamed-fallback"),),
            runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertNotIn("--budget", text)
        self.assertIn("status.py spider", text)
        self.assertEqual(router.deploy_dispatches, [])

    # ---- (d) a read that failed is COULD NOT CHECK, never a verdict ----

    def test_an_unreadable_status_call_is_could_not_check_not_a_verdict(self):
        code, out, err, router = self._verify(status_json=(1, "", "gateway error"))
        text = out + err
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", text)
        self.assertNotIn("NOT VERIFIED", text)
        self.assertNotIn("✓", text)
        self.assertIn("openclaw senpi status", text)        # names the read that failed
        self.assertEqual(router.deploy_dispatches, [])

    def test_an_unreadable_runtime_list_is_could_not_check(self):
        code, out, err, router = self._verify(runtime_list=(1, "", "openclaw: gateway not reachable"))
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", out + err)
        self.assertEqual(router.deploy_dispatches, [])

    def test_an_unreadable_strategy_list_is_could_not_check(self):
        code, out, err, router = self._verify(mcp_raises=RuntimeError("no SENPI_AUTH_TOKEN"))
        text = out + err
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", text)
        self.assertIn("strategy_list", text)
        self.assertIn("no SENPI_AUTH_TOKEN", text)
        self.assertEqual(router.deploy_dispatches, [])

    def test_could_not_check_says_so_in_json_too(self):
        code, out, _err, _router = self._verify("--json", status_json=(1, "", "gateway error"))
        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(payload["verdict"], "could-not-check")
        self.assertTrue(payload["unreadable"])

    # ---- (e) health is QUOTED, never re-derived ----

    def test_a_degraded_runtime_is_not_verified_and_quotes_the_health_string(self):
        code, out, err, router = self._verify(
            status_json=_ok({"statuses": [{"name": "spider-main", "overallHealth": "degraded"}]}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertIn("NOT VERIFIED", text)
        self.assertIn("degraded", text)
        self.assertIn("status.py spider", text)             # triage, not a resume
        self.assertNotIn("deploy.py runtime spider", text)
        self.assertEqual(router.deploy_dispatches, [])

    def test_an_unproven_runtime_is_not_verified(self):
        # The runtime's own fail-closed `unknown` (no tick has proven the scanner yet) is not health.
        code, out, err, _router = self._verify(
            status_json=_ok({"statuses": [{"name": "spider-main", "overallHealth": "unknown"}]}))
        self.assertEqual(code, 3)
        self.assertIn("unknown", (out + err))

    def test_a_stopped_runtime_is_not_verified_and_quotes_its_listed_status(self):
        code, out, err, router = self._verify(
            runtime_list=(0, _runtime_table(("spider-main", WALLET, "stopped")), ""),
            status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertIn("stopped", text)
        self.assertEqual(router.deploy_dispatches, [])

    # ---- (f) the last deploy job's warns are relayed verbatim; no job is not a failure ----

    def test_a_deploy_snapshot_warn_is_relayed_verbatim(self):
        warn = ("[W_BUDGET_PARTIAL_FUND] main (0x1234…5678) funded $60.00 of requested $500.00 (12%)")
        snap = {"meta": {"deployId": "dpl-a1b2c3d4", "packageId": "spider"},
                "state": {"status": "done", "overall": "live"}, "partialFundNote": warn}
        code, out, err, _router = self._verify(deploy_status=(0, json.dumps(snap), ""))
        self.assertEqual(code, 0)
        self.assertIn(warn, out + err)

    def test_a_warn_is_read_off_a_snapshot_however_deploy_status_exited(self):
        # `deploy status` sets the JOB's D-12 code before printing the snapshot, on --json too.
        warn = "[W_BUDGET_BELOW_STRATEGY_MIN] scalp $12.00 (needs $13.50)"
        snap = {"meta": {"packageId": "spider"}, "state": {"status": "done", "overall": "failed"},
                "minBudgetNote": warn}
        code, out, err, _router = self._verify(deploy_status=(3, json.dumps(snap), ""))
        self.assertEqual(code, 0)          # the JOB's verdict is not this check's verdict
        self.assertIn(warn, out + err)

    def test_no_deploy_snapshot_is_not_a_failure(self):
        code, out, _err, _router = self._verify(deploy_status=RouterCli.NO_DEPLOY_JOB)
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED", out)

    def test_an_unreadable_job_read_is_could_not_check_never_a_money_steer(self):
        # `read_status(None)` answers None for a spawn failure, the STATUS_TIMEOUT AND the verb's own
        # `[NOT_FOUND]`, and `deploy_job_facts` mapped all three to "no job". Every money steer this
        # check emits is conditioned on that bit, so an unreadable read let verify name `runtime
        # spider` — a SECOND deploy — while a job may have been funding the wallet right then.
        code, out, err, router = self._verify(
            deploy_status=(-1, "", _cli.SPAWN_FAILED_PREFIX + "openclaw"),
            runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", text)
        self.assertNotIn("NOT VERIFIED", text)
        self.assertIn("deploy status", text)                # names the read that failed
        self.assertNotIn("deploy.py runtime spider", text)  # the money steer it used to reach
        self.assertEqual(router.deploy_dispatches, [])

    def test_a_status_timeout_on_the_job_read_is_could_not_check_too(self):
        code, out, err, _router = self._verify(deploy_status=(-1, "", "timed out after 60s: openclaw …"))
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", out + err)

    def test_the_verbs_own_no_job_answer_still_verifies(self):
        # The one no-snapshot case that IS an answer: `[NOT_FOUND]`, no deploy has ever run here.
        code, out, _err, _router = self._verify(deploy_status=RouterCli.NO_DEPLOY_JOB)
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED", out)

    def test_another_packages_deploy_job_is_never_relayed_under_this_package(self):
        # One deploy-job record per agent, not package-addressed: polar's warn under a `verify spider`
        # prompt invites binding the wrong package's facts.
        warn = "[W_BUDGET_PARTIAL_FUND] main (0x…) funded $60.00 of requested $500.00 (12%)"
        snap = {"meta": {"packageId": "polar"}, "state": {"status": "done", "overall": "live"},
                "partialFundNote": warn}
        code, out, err, _router = self._verify(deploy_status=(0, json.dumps(snap), ""))
        self.assertEqual(code, 0)
        self.assertNotIn("W_BUDGET_PARTIAL_FUND", out + err)

    # ---- (g) the argument surface: a read-only check HONOURS no money flag ----
    #
    # It used to REJECT them, and that rejection is what `VerifyIgnoresTheFlagsItNoLongerHas` below
    # replaced: argparse's usage error exits 2, the code D-12 teaches as "refused". The invariant the
    # rejection was protecting — a `--budget` on a check never funds anything — is pinned there by the
    # same `deploy_dispatches == []` assertion every case in this class makes.

    # ---- multi-instance: every instance must be live, and each names its own next step ----

    def test_a_multi_instance_package_names_the_instance_that_is_missing(self):
        self.pkg = _pkg(instances=[_inst("swing"), _inst("scalp")])
        code, out, err, router = self._verify(
            strategies=(_strategy(name="spider-swing"),),
            runtime_list=(0, _runtime_table(("spider-swing", WALLET, "running")), ""),
            status_json=_ok({"statuses": [{"name": "spider-swing", "overallHealth": "healthy"}]}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertIn("scalp", text)
        self.assertEqual(router.deploy_dispatches, [])

    # ---- (h) the steer is chosen against the STATUS actually read ----

    def test_a_closing_wallet_with_no_runtime_never_steers_at_the_resume(self):
        # The exact window close.py's doctrine path opens: positions closing, runtime already gone.
        # The verb adopts CLOSING_POSITIONS as live (only CLOSING_DONE is dead), so an obedient agent
        # following a `runtime <id>` steer here reinstalls a runtime on a strategy being torn down.
        for status in ("CLOSING_POSITIONS", "PAUSED"):
            code, out, err, router = self._verify(
                strategies=(_strategy(status=status),),
                runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}))
            text = out + err
            self.assertEqual(code, 3, status)
            self.assertNotIn("deploy.py runtime spider", text)
            self.assertIn(status, text)                     # the status is QUOTED, not paraphrased
            self.assertIn("status.py spider", text)         # read-only triage
            self.assertEqual(router.deploy_dispatches, [])

    def test_a_closing_wallet_with_a_healthy_runtime_is_never_live_and_healthy(self):
        # Same root: a teardown-state strategy whose runtime is still registered and healthy used to
        # exit 0 "VERIFIED — live and healthy".
        code, out, err, router = self._verify(strategies=(_strategy(status="CLOSING_POSITIONS"),))
        text = out + err
        self.assertEqual(code, 3)
        self.assertNotIn("live and healthy", text)
        self.assertIn("CLOSING_POSITIONS", text)

    def test_an_active_wallet_still_verifies(self):
        # The guard above must not swallow the happy path.
        code, out, _err, _router = self._verify(strategies=(_strategy(status="ACTIVE"),))
        self.assertEqual(code, 0)

    # ---- (i) a deploy job already running is never answered with a second dispatch ----

    @staticmethod
    def _running_job():
        snap = {"meta": {"packageId": "spider"}, "state": {"status": "running", "phase": "fund"}}
        return (deploy.EXIT_PENDING, json.dumps(snap), "")

    def test_a_running_job_replaces_the_create_steer_with_deploy_status(self):
        code, out, err, router = self._verify(
            strategies=(), runtime_list=(0, _runtime_table(), ""),
            status_json=_ok({"statuses": []}), deploy_status=self._running_job())
        text = out + err
        self.assertEqual(code, 3)
        self.assertNotIn("deploy.py create spider --budget", text)
        self.assertIn("openclaw senpi deploy status", text)
        self.assertIn("RUNNING", text)
        self.assertEqual(router.deploy_dispatches, [])

    def test_a_running_job_replaces_the_resume_steer_with_deploy_status(self):
        code, out, err, router = self._verify(
            runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}),
            deploy_status=self._running_job())
        text = out + err
        self.assertEqual(code, 3)
        self.assertNotIn("deploy.py runtime spider", text)
        self.assertIn("openclaw senpi deploy status", text)
        self.assertEqual(router.deploy_dispatches, [])

    def test_a_running_job_is_reported_in_json_too(self):
        code, out, _err, _router = self._verify(
            "--json", strategies=(), runtime_list=(0, _runtime_table(), ""),
            status_json=_ok({"statuses": []}), deploy_status=self._running_job())
        payload = json.loads(out)
        self.assertEqual(code, 3)
        self.assertTrue(payload["deploy_job_running"])

    def test_another_packages_running_job_never_changes_this_packages_steer(self):
        snap = {"meta": {"packageId": "polar"}, "state": {"status": "running", "phase": "fund"}}
        code, out, err, _router = self._verify(
            strategies=(), runtime_list=(0, _runtime_table(), ""),
            status_json=_ok({"statuses": []}),
            deploy_status=(deploy.EXIT_PENDING, json.dumps(snap), ""))
        self.assertEqual(code, 3)
        self.assertIn("deploy.py create spider --budget", out + err)

    # ---- (j) attribution WIDENS the candidate set; it never shrinks it ----

    def test_an_unattributed_wallet_is_matched_by_its_derived_name(self):
        # `strategy_skill` falls back to tradingStrategyName, which for a multi-instance package can
        # never equal the package id ("spider-swing" != "spider") — so an attribution-GATED match
        # dropped a live funded wallet out of the check and reported "nothing is funded here".
        self.pkg = _pkg(instances=[_inst("swing"), _inst("scalp")])
        code, out, err, _router = self._verify(
            strategies=(_strategy(name="spider-swing", skill=None),
                        _strategy(name="spider-scalp", skill=None, wallet=OTHER_WALLET)),
            runtime_list=(0, _runtime_table(("spider-swing", WALLET, "running"),
                                            ("spider-scalp", OTHER_WALLET, "running")), ""),
            status_json=_ok({"statuses": [{"name": "spider-swing", "overallHealth": "healthy"},
                                          {"name": "spider-scalp", "overallHealth": "healthy"}]}))
        text = out + err
        self.assertEqual(code, 0)
        self.assertNotIn("nothing is funded here", text)

    def test_an_unattributed_funded_wallet_is_never_reported_as_nothing_funded(self):
        # swing IS funded, just unattributed. Reporting it as "nothing is funded here" is a false
        # quoted fact — and the steer that follows it (`create --budget`) funds a SECOND wallet
        # beside the live one.
        self.pkg = _pkg(instances=[_inst("swing"), _inst("scalp")])
        code, out, err, router = self._verify(
            strategies=(_strategy(name="spider-swing", skill=None),),
            runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertNotIn("no live strategy named 'spider-swing'", text)
        self.assertIn("funded and not trading", text)        # swing was matched to its wallet
        self.assertEqual(router.deploy_dispatches, [])

    # ---- (k) an unreadable strategy_list SHAPE is not an empty backend ----

    def test_a_strategy_list_shape_it_cannot_navigate_is_could_not_check(self):
        code, out, err, router = self._verify(
            mcp_payload={"ok": True, "records": {"count": 0}},
            runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", text)
        self.assertIn("strategy_list", text)
        self.assertNotIn("nothing is funded here", text)
        self.assertEqual(router.deploy_dispatches, [])

    def test_a_genuinely_empty_strategy_list_is_still_a_verdict(self):
        # [] is an ANSWER — only an unnavigable shape is a failed read.
        code, out, err, _router = self._verify(
            mcp_payload={"strategies": []},
            runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertIn("nothing is funded here", text)

    # ---- (l) health is only what the runtime published AS health ----

    def test_a_bare_run_state_entry_is_not_proof_of_health(self):
        # A `status --json` entry carrying {name, status: "running"} and no health field used to
        # render VERIFIED for a runtime no tick has ever proven.
        code, out, err, router = self._verify(
            status_json=_ok({"statuses": [{"name": "spider-main", "status": "running"}]}))
        text = out + err
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", text)
        self.assertNotIn("VERIFIED", text)
        self.assertEqual(router.deploy_dispatches, [])

    def test_a_broken_run_state_downgrades_even_when_no_health_field_was_published(self):
        # The other side of the same rule: a run state can never PROMOTE, but positive broken
        # evidence is believed wherever it is found. `_raw_health` declared the row unreadable before
        # `health_verdict` ever ran, so `{name, status: "failed"}` — a runtime the box says is
        # broken — rendered "COULD NOT CHECK — retry" instead of NOT VERIFIED with the evidence.
        code, out, err, router = self._verify(
            status_json=_ok({"statuses": [{"name": "spider-main", "status": "failed"}]}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertIn("NOT VERIFIED", text)
        self.assertNotIn("COULD NOT CHECK", text)
        self.assertIn("failed", text)                        # the evidence, quoted
        self.assertIn("status.py spider", text)              # triage, never a redeploy
        self.assertEqual(router.deploy_dispatches, [])

    def test_an_entry_with_neither_health_nor_classifiable_evidence_stays_unreadable(self):
        code, out, err, _router = self._verify(
            status_json=_ok({"statuses": [{"name": "spider-main"}]}))
        text = out + err
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", text)

    # ---- (m1) the could-not-check document is the same schema as the other two ----

    def test_the_could_not_check_document_carries_deploy_job_running(self):
        # exit 0 / 3 / 1 must be ONE total schema — a key that appears only on the happy paths makes
        # a caller's `payload["deploy_job_running"]` a KeyError exactly when the answer is "unknown".
        code, out, _err, _router = self._verify("--json", status_json=(1, "", "gateway error"))
        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("deploy_job_running", payload)
        self.assertIsNone(payload["deploy_job_running"])     # the job state was never read

    def test_a_could_not_check_after_the_job_read_reports_what_it_read(self):
        snap = {"meta": {"packageId": "spider"}, "state": {"status": "running"}}
        code, out, _err, _router = self._verify(
            "--json", deploy_status=(6, json.dumps(snap), ""),
            status_json=_ok({"statuses": [{"name": "spider-main"}]}))
        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(payload["verdict"], "could-not-check")
        self.assertTrue(payload["deploy_job_running"])

    # ---- (m) the funded figure is the BACKEND's, or it is UNKNOWN ----

    def test_a_requested_budget_is_never_printed_as_funded(self):
        code, out, err, _router = self._verify(
            strategies=(_strategy(funded=None, initial_budget=500),),
            runtime_list=(0, _runtime_table(), ""), status_json=_ok({"statuses": []}))
        text = out + err
        self.assertEqual(code, 3)
        self.assertNotIn("$500", text)                       # the REQUESTED figure
        self.assertIn("UNKNOWN", text)
        self.assertIn("status.py spider", text)              # how to read the real one


def _row_text(text, instance):
    """One instance's block of verify's prose report: its `- <name>:` line plus its `Next:` line."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith(f"- {instance}:"))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("- ")),
               len(lines))
    return "\n".join(lines[start:end])


# A DIFFERENT package, single-instance, whose own id is exactly the name `spider`'s `swing` sleeve
# derives — the collision this class is about.
FOREIGN_PKG = "spider-swing"


class VerifyAttributionDisambiguatesButNeverShrinks(VerifyHarness, unittest.TestCase):
    """Attribution decides BETWEEN same-named candidates; it never removes the only one.

    Two opposite regressions meet here. Gating the match BEHIND attribution dropped an unattributed
    funded wallet out of the check entirely and printed "nothing is funded here" over it, steered at
    `create --budget` (a double-fund). Removing the gate adopted ANOTHER package's identically-named
    wallet: single-instance package `spider-swing` is live and healthy, `spider` (never deployed
    here) declares an instance `swing` and derives the SAME wallet name — and verify rendered
    `swing: OK — live and healthy` against that package's wallet, runtime and health, double-booking
    one pot of money as two packages' sleeve."""

    # A third wallet, so the disambiguation case can tell WHICH candidate was adopted.
    THIRD_WALLET = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"

    def setUp(self):
        super().setUp()
        # Two instances: only a multi-instance package derives `<id>-<instance>` names at all.
        self.pkg = _pkg(instances=[_inst("swing"), _inst("scalp")])

    @staticmethod
    def _scalp():
        """scalp is genuinely deployed in every case here, so the row under test is always swing."""
        return _strategy(name="spider-scalp", wallet=OTHER_WALLET)

    @staticmethod
    def _running(*rows):
        """`runtime list` + `status --json` for the given (runtime name, wallet) pairs, all healthy."""
        return {"runtime_list": (0, _runtime_table(*[(n, w, "running") for n, w in rows]), ""),
                "status_json": _ok({"statuses": [{"name": n, "overallHealth": "healthy"}
                                                 for n, _w in rows]})}

    def _swing_row(self, payload):
        return next(r for r in payload["instances"] if r["instance"] == "swing")

    # ---- (a) a foreign-attributed same-named wallet is NOT this instance's ----

    def test_another_packages_identically_named_wallet_is_never_adopted(self):
        code, out, err, router = self._verify(
            strategies=(_strategy(name="spider-swing", skill=FOREIGN_PKG, wallet=WALLET),
                        self._scalp()),
            **self._running(("spider-swing", WALLET), ("spider-scalp", OTHER_WALLET)))
        text = out + err
        self.assertEqual(code, 3)                            # not verified — and not could-not-check
        self.assertIn("NOT VERIFIED", text)
        swing = _row_text(text, "swing")
        self.assertIn(repr("spider-swing"), swing)           # the name this sleeve wanted…
        self.assertIn(FOREIGN_PKG, swing)                    # …and who the backend says owns it
        self.assertIn("spider", swing)
        # Nothing of the foreign wallet's is rendered as this sleeve's.
        self.assertNotIn("swing: OK", text)
        self.assertNotIn("live and healthy", text)
        self.assertNotIn("healthy", swing)
        self.assertNotIn(WALLET[:10], swing)
        self.assertNotIn("$300", swing)
        # Read-only triage — never a create on a taken name, never a teardown.
        self.assertIn("status.py spider", swing)
        self.assertNotIn("--budget", text)
        self.assertNotIn("close.py", text)
        self.assertEqual(router.deploy_dispatches, [])

    def test_a_single_instance_packages_taken_name_is_a_collision_too(self):
        # want == the bare package id here, and no wallet is attributed to `spider` at all.
        self.pkg = _pkg(instances=[_inst("main")])
        code, out, err, router = self._verify(
            strategies=(_strategy(name="spider", skill="polar"),))
        text = out + err
        self.assertEqual(code, 3)
        self.assertIn("polar", text)
        self.assertIn("status.py spider", text)
        self.assertNotIn("--budget", text)
        self.assertEqual(router.deploy_dispatches, [])

    # ---- (b) an unattributed same-named wallet is STILL this instance's (round-1, pinned) ----

    def test_an_unattributed_same_named_wallet_is_still_adopted(self):
        code, out, err, router = self._verify(
            strategies=(_strategy(name="spider-swing", skill=None, wallet=WALLET), self._scalp()),
            **self._running(("spider-swing", WALLET), ("spider-scalp", OTHER_WALLET)))
        self.assertEqual(code, 0, out + err)
        self.assertIn("VERIFIED", out)
        self.assertEqual(router.deploy_dispatches, [])

    # ---- (c) a wallet attributed to THIS package is adopted ----

    def test_a_same_attributed_wallet_is_adopted(self):
        code, out, err, _router = self._verify(
            strategies=(_strategy(name="spider-swing", skill="spider", wallet=WALLET),
                        self._scalp()),
            **self._running(("spider-swing", WALLET), ("spider-scalp", OTHER_WALLET)))
        self.assertEqual(code, 0, out + err)
        self.assertIn("VERIFIED", out)

    # ---- (d) the disambiguation payoff: the foreign one is excluded, the other one is adopted ----

    def test_a_foreign_candidate_beside_an_unattributed_one_leaves_the_unattributed_one(self):
        code, out, _err, _router = self._verify(
            "--json",
            strategies=(_strategy(name="spider-swing", skill=FOREIGN_PKG, wallet=WALLET),
                        _strategy(name="spider-swing", skill=None, wallet=self.THIRD_WALLET),
                        self._scalp()),
            **self._running(("spider-swing", self.THIRD_WALLET), ("spider-scalp", OTHER_WALLET)))
        payload = json.loads(out)
        swing = self._swing_row(payload)
        self.assertEqual(code, 0)                            # not the "2 live strategies match" tie
        self.assertTrue(swing["ok"])
        self.assertEqual(swing["wallet"], self.THIRD_WALLET)  # the one that could be this sleeve's
        self.assertIsNone(swing["collision"])

    # ---- (e) --json says the same thing, and borrows nothing from the foreign record ----

    def test_the_json_document_carries_the_collision_and_borrows_nothing(self):
        code, out, _err, _router = self._verify(
            "--json",
            strategies=(_strategy(name="spider-swing", skill=FOREIGN_PKG, wallet=WALLET),
                        self._scalp()),
            **self._running(("spider-swing", WALLET), ("spider-scalp", OTHER_WALLET)))
        payload = json.loads(out)
        swing = self._swing_row(payload)
        self.assertEqual(code, 3)
        self.assertEqual(payload["verdict"], "not-verified")
        self.assertFalse(swing["ok"])
        self.assertEqual(swing["collision"],
                         {"name": "spider-swing", "attributed_to": [FOREIGN_PKG]})
        for borrowed in ("wallet", "status", "funded", "health", "runtime"):
            self.assertIsNone(swing[borrowed], borrowed)
        self.assertIsNone(swing["unreadable"])               # a collision is a VERDICT, not a failed read


class VerifyIgnoresTheFlagsItNoLongerHas(VerifyHarness, unittest.TestCase):
    """A stale-transcript invocation (`verify spider --max-wait 300`) still gets the CHECK.

    The regression this pins: when `verify` stopped taking the deploy flags, argparse answered every
    pre-3.1.0 transcript with `unrecognized arguments` and exit **2** — the one code D-12 defines as
    "refused: a gate said no, nothing was created, retrying refuses identically". An agent branching
    on that map reports "the deploy was refused" about a package that may be perfectly live, and
    never re-runs without the stale flag — on exactly the stale-transcript path this rewrite exists
    to protect. So the five removed flags are ACCEPTED and IGNORED: the verdict, the reads and the
    exit code are the ones the flagless command renders, and stderr says which flag was dropped and
    where the current contract is written down."""

    STALE = (("--budget", "500"), ("--max-wait", "300"), ("--tick-wait", "0"),
             ("--decision-model", "samurai-light"), ("--dry-run",))

    def _clean(self):
        return self._verify()

    def test_every_removed_flag_is_accepted_and_changes_nothing(self):
        base_code, base_out, _base_err, base_router = self._clean()
        for stale in self.STALE:
            code, out, err, router = self._verify(*stale)
            self.assertEqual(code, base_code, stale)            # never argparse's 2
            self.assertEqual(out, base_out, stale)              # same verdict, verbatim
            self.assertEqual(router.calls, base_router.calls, stale)   # the same reads, no more
            self.assertEqual(router.deploy_dispatches, [], stale)
            self.assertIn(stale[0], err, stale)                 # the flag is NAMED
            self.assertIn("ignored", err.lower(), stale)

    def test_the_warning_points_at_where_the_current_contract_lives(self):
        _code, _out, err, _router = self._verify("--max-wait", "300")
        self.assertIn("verify --help", err)
        self.assertIn("SKILL.md", err)
        self.assertIn("lifecycle.md", err)

    def test_a_clean_invocation_warns_about_nothing(self):
        _code, _out, err, _router = self._clean()
        self.assertNotIn("obsolete", err.lower())
        self.assertNotIn("ignored", err.lower())

    def test_a_valued_flag_consumes_its_value_and_leaves_the_package_intact(self):
        # `--budget 500` must not leave `500` to be read as the package (or as a second positional).
        seen = []
        deploy.local_pkg = lambda arg: seen.append(arg) or self.pkg
        for argv in (("--budget", "500"), ("--max-wait", "300")):
            seen.clear()
            code, out, _err, _router = self._verify(*argv)
            self.assertEqual(code, 0, argv)
            self.assertEqual(seen, ["spider"], argv)
            self.assertIn("VERIFIED", out, argv)

    def test_all_five_together_are_ignored_in_one_warning(self):
        flat = [tok for stale in self.STALE for tok in stale]
        code, out, err, router = self._verify(*flat)
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED", out)
        self.assertEqual(router.deploy_dispatches, [])
        for flag, *_ in self.STALE:
            self.assertIn(flag, err)

    def test_json_stdout_is_still_exactly_one_document(self):
        code, out, err, _router = self._verify("--json", "--budget", "500")
        payload = json.loads(out)                               # the pin: stdout stays clean
        self.assertEqual(code, 0)
        self.assertEqual(payload["verdict"], "verified")
        self.assertIn("--budget", err)                          # the warning went to stderr

    def test_dry_run_with_json_renders_the_verdict_instead_of_refusing_the_pair(self):
        # `--dry-run --json` is refused on the money path (there is no JSON rendering of a plan).
        # On `verify` both are ignorable/real: there is no plan, so the read-only verdict is the
        # honest answer — and the refusal must not fire off a flag this command already dropped.
        code, out, err, _router = self._verify("--dry-run", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["verdict"], "verified")
        self.assertIn("--dry-run", err)

    def test_a_typo_is_still_an_error_and_never_a_verdict(self):
        # Only the five NAMED flags are accepted-and-ignored. A mistyped one is a command that does
        # not mean what its author thought — swallowing it would render a verdict over a typo.
        code, out, err, router = self._verify("--bugdet", "500")
        self.assertNotEqual(code, 0)
        self.assertNotIn("VERIFIED", out + err)
        self.assertEqual(router.calls, [])                      # nothing was read
        self.assertIn("--bugdet", err)


class VerifyResolvesTheLocalPackageOnly(unittest.TestCase):
    """`verify` promises "read-only / nothing was changed", so it must not download and WRITE a
    package under the durable strategies root just to have an instance list to check."""

    def setUp(self):
        self._resolve, self._fetch = deploy._pkg.resolve_pkg_dir, deploy._fetch.fetch_package
        self._cli_real = _cli.run_cli

    def tearDown(self):
        deploy._pkg.resolve_pkg_dir, deploy._fetch.fetch_package = self._resolve, self._fetch
        _cli.run_cli = self._cli_real

    def test_a_missing_local_package_fetches_nothing_and_renders_no_verdict(self):
        fetched = []
        deploy._pkg.resolve_pkg_dir = lambda arg: Path("/nonexistent-root") / str(arg)
        deploy._fetch.fetch_package = lambda *a, **k: fetched.append(a)
        fake = FakeCli([])
        _cli.run_cli = fake
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            deploy.main(["deploy.py", "verify", "spider"])
        text = out.getvalue() + err.getvalue()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(fetched, [])                        # the core pin: no network, no disk write
        self.assertEqual(fake.calls, [])
        self.assertIn("COULD NOT CHECK", text)
        self.assertIn("not on disk", text)

    def _malformed_pkg(self):
        """A package dir that EXISTS with a strategy.yaml `_pkg.load` cannot model."""
        root = Path(tempfile.mkdtemp(prefix="verify-badpkg-"))
        self.addCleanup(shutil.rmtree, root, True)
        (root / "spider").mkdir()
        (root / "spider" / "strategy.yaml").write_text("id: spider\ninstances: [\n")  # unclosed flow seq
        deploy._pkg.resolve_pkg_dir = lambda arg: root / Path(str(arg)).name
        return root / "spider"

    def _run_verify(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            deploy.main(["deploy.py", "verify", "spider", *extra])
        return ctx.exception.code, out.getvalue(), err.getvalue()

    def test_a_malformed_package_is_could_not_check_not_a_traceback(self):
        # Reproduced in review: `local_pkg` -> `_pkg.load` raises BadPackage straight through
        # `main`, so `verify <dir>` on a broken strategy.yaml exited 1 with a raw traceback and an
        # EMPTY stdout — no verdict, no teaching, and nothing a caller could parse.
        pkg_dir = self._malformed_pkg()
        _cli.run_cli = FakeCli([])
        code, out, err = self._run_verify()
        text = out + err
        self.assertEqual(code, 1)
        self.assertIn("COULD NOT CHECK", text)
        self.assertIn("strategy.yaml", text)                 # names the file
        self.assertIn(str(pkg_dir), text)                    # …and where it is
        self.assertNotIn("Traceback", text)

    def test_a_malformed_package_still_emits_the_json_document(self):
        self._malformed_pkg()
        _cli.run_cli = FakeCli([])
        code, out, _err = self._run_verify("--json")
        payload = json.loads(out)                            # stdout used to be empty
        self.assertEqual(code, 1)
        self.assertEqual(payload["verdict"], "could-not-check")
        self.assertEqual(payload["id"], "spider")
        self.assertTrue(payload["unreadable"])
        self.assertIn("deploy_job_running", payload)

    def test_the_package_dir_argument_form_shares_the_same_handling(self):
        pkg_dir = self._malformed_pkg()
        _cli.run_cli = FakeCli([])
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            deploy.main(["deploy.py", "verify", str(pkg_dir)])
        text = out.getvalue() + err.getvalue()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("COULD NOT CHECK", text)
        self.assertNotIn("Traceback", text)


class DryRunHasNoJsonRendering(unittest.TestCase):
    """`--dry-run --json` printed the prose plan and no JSON at all, so a JSON caller parsed
    nothing. Refuse the combination instead of emitting a document that isn't the plan."""

    def setUp(self):
        self._cli_real, self._ensure = _cli.run_cli, deploy.ensure_pkg
        deploy.ensure_pkg = lambda arg, ref, log: self.fail("the package was resolved (or FETCHED)")

    def tearDown(self):
        _cli.run_cli, deploy.ensure_pkg = self._cli_real, self._ensure

    def test_the_combination_is_refused_before_anything_is_resolved(self):
        for cmd in ("create", "runtime"):
            fake = FakeCli([])
            _cli.run_cli = fake
            out, err = io.StringIO(), io.StringIO()
            with self.assertRaises(SystemExit) as ctx, \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                deploy.main(["deploy.py", cmd, "spider", "--budget", "300", "--dry-run", "--json"])
            self.assertEqual(ctx.exception.code, 1, cmd)
            self.assertEqual(fake.calls, [], cmd)
            self.assertEqual(out.getvalue(), "", cmd)        # never a fake/empty JSON document
            self.assertIn("--dry-run", err.getvalue())
            self.assertIn("JSON", err.getvalue())


class WalletNameMatchesTheVerbsSanitizer(unittest.TestCase):
    """`_sanitize_strategy_name` must be the verb's `sanitizeStrategyName`
    (senpi-trading-runtime `src/deploy/package.ts`) in Python. A name verify derives differently
    from the one the deploy CREATED reports a live deploy as NOT VERIFIED."""

    def test_edge_underscores_survive_exactly_as_the_verb_keeps_them(self):
        # The verb trims `/^-+|-+$/` — hyphens ONLY. This used to `.strip("-_")`.
        self.assertEqual(deploy._sanitize_strategy_name("_spider_"), "_spider_")
        self.assertEqual(deploy._sanitize_strategy_name("__spider__main"), "__spider__main")

    def test_edge_hyphens_are_trimmed(self):
        self.assertEqual(deploy._sanitize_strategy_name("--spider--"), "spider")
        self.assertEqual(deploy._sanitize_strategy_name("-_spider_-"), "_spider_")

    def test_whitespace_becomes_hyphens_and_junk_is_dropped(self):
        self.assertEqual(deploy._sanitize_strategy_name("my spider  v2!"), "my-spider-v2")

    def test_the_cap_is_forty_chars_applied_last(self):
        self.assertEqual(deploy._sanitize_strategy_name("a" * 50), "a" * 40)


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
