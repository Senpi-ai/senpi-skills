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
    package; defaulting the poll loop to 150 would give up on a job that is legitimately still funding."""

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

    def test_the_poll_budget_is_not_cut_to_the_verbs_default(self):
        self._create()
        self.assertEqual(self.waited, [deploy.POLL_BUDGET])
        self.assertGreater(deploy.POLL_BUDGET, deploy.DEFAULT_MAX_WAIT)

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
