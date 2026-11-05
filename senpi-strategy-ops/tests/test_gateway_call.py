#!/usr/bin/env python3
"""Tests for the direct gateway call (`_cli.gateway_call`) and the spawn under it (`_cli._spawn`).

deploy.py and close.py call `openclaw gateway call <method> --json --timeout <ms> --params <json>`
instead of the `openclaw senpi …` CLI verbs: on OpenClaw 10.x every verb loads the runtime's whole
dependency tree first (30-160 s per call), while `gateway call` loads no plugins and answers in
about 1 s with the same request and credentials.

The fixtures are the real answers recorded on Box A (report 2026-10-28-deploy-py-direct-gateway-call):
5.12 writes a gateway error to STDERR as text (`evidence/01-probe-responses-5.12.txt`), 10.10 writes a
JSON envelope to STDOUT and nothing to stderr (`evidence/p2/20-t1-1010.txt`, OpenClaw
`src/gateway/call.ts`).

Run:  python3 senpi-strategy-ops/tests/test_gateway_call.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import _cli  # noqa: E402
import deploy  # noqa: E402

M = "senpi.deploy.status"

# ---- 5.12: rc 1, stdout empty, the error on stderr (verbatim from the Box A probe) ----
E512_NOT_FOUND = ("Gateway call failed: GatewayClientRequestError: [NOT_FOUND] No deploy has been "
                  "started on this agent. Start one: openclaw senpi deploy -p <package-dir> --budget <usd>\n")
E512_UNKNOWN = "Gateway call failed: GatewayClientRequestError: unknown method: senpi.noSuchMethodProbe\n"
E512_TRANSPORT = ("Gateway call failed: GatewayTransportError: gateway timeout after 1ms\n"
                  "Gateway target: ws://127.0.0.1:18789\nSource: local loopback\n"
                  "Config: /data/.openclaw/openclaw.json\nBind: loopback\n")
E512_NON_TAXONOMY = ("Gateway call failed: GatewayClientRequestError: No running runtime found for id "
                     "no-such-runtime-probe\n")


def _pretty(obj):
    # 10.10 prints the envelope pretty-printed, as the Box A capture shows.
    return json.dumps(obj, indent=2)


# ---- 10.10: rc 1, the envelope on STDOUT, stderr empty (verbatim from the Box A probe) ----
J1010_NOT_FOUND = _pretty({"ok": False, "error": {
    "type": "gateway_request_error", "code": "NOT_FOUND",
    "message": "[NOT_FOUND] No deploy has been started on this agent. Start one: openclaw senpi deploy "
               "-p <package-dir> --budget <usd>", "retryable": False}})
J1010_UNKNOWN = _pretty({"ok": False, "error": {
    "type": "gateway_request_error", "code": "INVALID_REQUEST",
    "message": "unknown method: senpi.noSuchMethodProbe", "retryable": False}})
J1010_TRANSPORT = _pretty({"ok": False, "error": {
    "type": "gateway_transport_error", "kind": "timeout", "message": "gateway timeout after 1ms",
    "timeoutMs": 1}, "gateway": {"url": "ws://127.0.0.1:18789", "urlSource": "local loopback",
                                 "bindDetail": "Bind: loopback"}})
J1010_CLI_ERROR = _pretty({"ok": False, "error": {"type": "cli_error",
                                                  "message": "--params must be valid JSON."}})
J1010_CREDENTIALS = _pretty({"ok": False, "error": {"type": "gateway_credentials_required",
                                                    "message": "gateway token required"}})


class FakeSpawn:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, argv, timeout):
        self.calls.append((argv, timeout))
        return self.answers.pop(0)


class Classification(unittest.TestCase):
    """Every answer shape on both hosts maps to one typed `kind`, never to bare text."""

    def setUp(self):
        self._real = _cli._spawn

    def tearDown(self):
        _cli._spawn = self._real

    def call(self, answer, method=M):
        _cli._spawn = FakeSpawn([answer])
        return _cli.gateway_call(method, {})

    def test_unknown_method_is_read_off_the_first_line_when_warnings_trail_it(self):
        # 5.x: the message is the rest of stderr, and with OPENCLAW_NO_RESPAWN warning lines can
        # follow it. Matching the whole tail turned a missing method into `error` (lost-reply path).
        err = ("Gateway call failed: GatewayClientRequestError: unknown method: senpi.deploy.start\n"
               "[plugins] warning: forced retirement after 5000ms: 1 still-running call(s)\n")
        r = self.call((1, "", err, False, None), "senpi.deploy.start")
        self.assertEqual(r["kind"], "unknown_method")

    def test_ok_is_the_raw_payload_on_both_hosts(self):
        r = self.call((0, '{\n  "runtimes": []\n}\n', "", False, None), "senpi.listRuntimes")
        self.assertEqual((r["kind"], r["payload"]), ("ok", {"runtimes": []}))

    def test_a_refusal_on_both_hosts(self):
        for answer in ((1, "", E512_NOT_FOUND, False, None), (1, J1010_NOT_FOUND, "", False, None)):
            r = self.call(answer)
            self.assertEqual(r["kind"], "refused", answer)
            self.assertTrue(r["message"].startswith("[NOT_FOUND] No deploy has been started"), r)
        self.assertEqual(self.call((1, J1010_NOT_FOUND, "", False, None))["code"], "NOT_FOUND")

    def test_unknown_method_on_both_hosts(self):
        for answer in ((1, "", E512_UNKNOWN, False, None), (1, J1010_UNKNOWN, "", False, None)):
            self.assertEqual(self.call(answer, "senpi.noSuchMethodProbe")["kind"], "unknown_method")

    def test_unknown_method_names_the_method_we_called(self):
        # Another method's "unknown method" text is not our answer.
        self.assertEqual(self.call((1, "", E512_UNKNOWN, False, None), M)["kind"], "error")

    def test_transport_on_both_hosts(self):
        for answer in ((1, "", E512_TRANSPORT, False, None), (1, J1010_TRANSPORT, "", False, None)):
            r = self.call(answer)
            self.assertEqual((r["kind"], r["transport"]), ("transport", "timeout"), answer)

    def test_a_non_taxonomy_answer_and_a_warning_code_are_errors_not_refusals(self):
        self.assertEqual(self.call((1, "", E512_NON_TAXONOMY, False, None), "senpi.getHealthStatus")["kind"],
                         "error")
        self.assertEqual(self.call((1, "", "Gateway call failed: GatewayClientRequestError: [W_X] w\n",
                                    False, None))["kind"], "error")
        self.assertEqual(self.call((1, J1010_CREDENTIALS, "", False, None))["kind"], "error")

    def test_openclaw_rejecting_the_command_line_sent_nothing(self):
        self.assertEqual(self.call((1, "", "error: unknown option '--params'\n", False, None))["kind"],
                         "cli_parse")
        rejected = _pretty({"ok": False, "error": {"type": "cli_error",
                                                   "message": "error: unknown option '--params'"}})
        self.assertEqual(self.call((1, rejected, "", False, None))["kind"], "cli_parse")

    def test_a_10x_cli_error_is_an_error_unless_it_is_a_command_line_rejection(self):
        # `cli_error` is OpenClaw's catch-all for any unclassified failure during the call
        # (`formatCliJsonFailure`), so the request may have been sent: `error`, which a START recovers.
        # The recorded `--params must be valid JSON.` is one too; this script never sends invalid JSON.
        self.assertEqual(self.call((1, J1010_CLI_ERROR, "", False, None))["kind"], "error")
        other = _pretty({"ok": False, "error": {"type": "cli_error", "message": "socket hang up"}})
        self.assertEqual(self.call((1, other, "", False, None))["kind"], "error")

    def test_timeout_spawn_failure_and_garbage(self):
        self.assertEqual(self.call((-1, "", "", True, None))["kind"], "timeout")
        self.assertEqual(self.call((-1, "", "", False, FileNotFoundError()))["kind"], "spawn_failed")
        self.assertEqual(self.call((1, "", "segfault\n", False, None))["kind"], "unreadable")
        self.assertEqual(self.call((0, "not json", "", False, None))["kind"], "unreadable")

    def test_the_10x_envelope_is_read_before_the_5x_stderr(self):
        r = self.call((1, J1010_NOT_FOUND, E512_UNKNOWN, False, None))
        self.assertEqual(r["kind"], "refused")

    def test_the_argv_is_the_same_on_every_host_and_always_carries_a_timeout(self):
        fake = FakeSpawn([(0, "{}", "", False, None)])
        _cli._spawn = fake
        r = _cli.gateway_call("senpi.deploy.start", {"packageDir": "/p", "budget": 30.0},
                              rpc_timeout_ms=30000, timeout_s=40)
        argv, wall = fake.calls[0]
        self.assertEqual(argv, ["openclaw", "gateway", "call", "senpi.deploy.start", "--json",
                                "--timeout", "30000", "--params", '{"packageDir": "/p", "budget": 30.0}'])
        self.assertEqual(wall, 40)
        self.assertEqual(r["argv"], argv)

    def test_a_non_finite_param_is_refused_before_spawning(self):
        _cli._spawn = FakeSpawn([])
        for bad in (float("nan"), float("inf")):
            self.assertEqual(_cli.gateway_call("senpi.deploy.start", {"budget": bad})["kind"], "cli_parse")


class RuntimeTaxonomyParity(unittest.TestCase):
    """The two readers this repo ports from the runtime must answer as the runtime does. Cases are
    copied from senpi-trading-runtime `src/deploy/__tests__/taxonomy.test.ts` (`isRefusalMessage`)
    and `src/cli/deploy-render.test.ts` (`exitCodeForDeploy`, D-12) at main `99a23b8b`."""

    def test_is_refusal_message_matches_the_runtime(self):
        cases = {
            "[INVALID_REQUEST] Nothing was created.": True,
            "[E_ROLLBACK_INCOMPLETE] wallet 0x… ": True,
            "  [E_DEPLOY_IN_PROGRESS] deploy dpl-1": True,
            "[E_MIN_10] budget too small": True,
            "[404] not found": False,
            "connection refused": False,
            None: False,
            "[W_BUDGET_BELOW_STRATEGY_MIN] …": False,
            "[W_BUDGET_UNRESOLVED] …": False,
            "[E_INSTALL_INDETERMINATE] …": True,
            "[E_WALLET_INSTALL_IN_FLIGHT] …": True,
        }
        for message, want in cases.items():
            self.assertEqual(_cli.is_refusal_message(message), want, message)

    def test_exit_code_for_matches_the_runtime(self):
        done = lambda overall: {"state": {"status": "done", "overall": overall}}  # noqa: E731
        for overall, code in (("live", 0), ("refused", 2), ("failed", 3), ("installed-unobserved", 4),
                              ("interrupted", 5), ("pending", 6), ("something-new", 1)):
            self.assertEqual(deploy.exit_code_for(done(overall)), code, overall)
        self.assertEqual(deploy.exit_code_for({"state": {"status": "running", "phase": "install"}}), 6)
        self.assertEqual(deploy.exit_code_for({"state": {"status": "interrupted"}}), 5)
        self.assertEqual(deploy.exit_code_for({}), 1)
        self.assertEqual(deploy.exit_code_for({"state": {}}), 1)
        self.assertEqual(deploy.exit_code_for({"state": {"status": "done"}}), 1)

    def test_a_non_string_overall_never_reads_as_success(self):
        # `["live"]` coerced onto the map's "live" key answered 0 (runtime `deploy-render.ts`).
        for overall in (["live"], {"v": "live"}, 0):
            self.assertEqual(deploy.exit_code_for({"state": {"status": "done", "overall": overall}}), 1)


def _write_fake_openclaw(d, body):
    """An `openclaw` on PATH that runs `body` (Python) — so the REAL `_spawn` and kill path run."""
    exe = Path(d) / "openclaw"
    exe.write_text(f"#!{sys.executable}\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return dict(os.environ, PATH=f"{d}{os.pathsep}{os.environ.get('PATH', '')}")


def _gone(pid):
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return st == "" or st.startswith("Z")


_LEADER_EXITS_ON_TERM = (
    "import subprocess,sys,time\n"
    "m=subprocess.Popen([sys.executable,'-c',"
    "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])\n"
    "open(sys.argv[1],'w').write(str(m.pid))\n"
    "time.sleep(60)\n")


def member_tree_is_gone_after_spawn(test, spawn):
    """Run a leader (default SIGTERM: exits) whose child ignores SIGTERM, time it out, and assert the
    child is gone — a check on the process, not on elapsed time."""
    with tempfile.TemporaryDirectory() as d:
        pidf = os.path.join(d, "member.pid")
        t0 = time.time()
        spawn([sys.executable, "-c", _LEADER_EXITS_ON_TERM, pidf], 1.5)
        test.assertLess(time.time() - t0, 6)                # no 5 s block on a pipe the member held
        mpid = int(open(pidf).read())
        deadline = time.time() + 3
        while not _gone(mpid) and time.time() < deadline:
            time.sleep(0.1)
        alive = not _gone(mpid)
        if alive:
            os.kill(mpid, 9)
        test.assertFalse(alive, "a SIGTERM-ignoring group member outlived the timeout")


class ProcessGroupKill(unittest.TestCase):
    """On our timeout the call's whole process group is killed: SIGTERM, 2 s, SIGKILL, reap. On
    OpenClaw 10.x a non-TTY `openclaw` respawns its worker detached into its own session, so a kill
    of our child alone left the worker running — and a timed-out START delivered after the script
    had exited (RT566-08). `OPENCLAW_NO_RESPAWN=1` keeps `openclaw` one process; the group kill is
    the backstop for an `openclaw` that ignores it."""

    def test_a_timeout_kills_the_calls_process_group(self):
        with tempfile.TemporaryDirectory() as d:
            pidf = os.path.join(d, "grandchild.pid")
            script = (f"import subprocess,time;p=subprocess.Popen(['sleep','60']);"
                      f"open({pidf!r},'w').write(str(p.pid));time.sleep(60)")
            t0 = time.time()
            rc, _o, _e, timed, _x = _cli._spawn([sys.executable, "-c", script], 1.5)
            self.assertTrue(timed)
            self.assertEqual(rc, -1)
            self.assertLess(time.time() - t0, 10)
            deadline = time.time() + 5
            gpid = int(open(pidf).read())
            while not _gone(gpid) and time.time() < deadline:
                time.sleep(0.1)
            self.assertTrue(_gone(gpid), "the call's own child outlived the timeout")

    def test_a_child_that_ignores_sigterm_is_killed_too(self):
        script = "import signal,time;signal.signal(signal.SIGTERM, signal.SIG_IGN);print('up',flush=True);time.sleep(60)"
        t0 = time.time()
        _rc, _o, _e, timed, _x = _cli._spawn([sys.executable, "-c", script], 1.0)
        self.assertTrue(timed)
        self.assertLess(time.time() - t0, 10)               # 1 s budget + 2 s SIGTERM grace + reap

    def test_a_group_member_that_ignores_sigterm_dies_even_when_the_leader_exits_on_it(self):
        # The leader exits on SIGTERM at once; its child ignores SIGTERM and holds the pipes. The
        # group SIGKILL must still be sent, or the member outlives the call (and the read blocks on it).
        member_tree_is_gone_after_spawn(self, lambda argv, t: _cli._spawn(argv, t))

    def test_the_child_gets_no_respawn_one_log_level_and_a_closed_stdin(self):
        _rc, out, _e, _t, _x = _cli._spawn([sys.executable, "-c",
                                            "import os,sys;print(os.environ.get('OPENCLAW_NO_RESPAWN'),"
                                            "os.environ.get('SENPI_LOG_LEVEL'), sys.stdin.read()=='')"], 10)
        self.assertEqual(out.strip(), "1 error True")

    def test_the_child_runs_in_its_own_session(self):
        _rc, out, _e, _t, _x = _cli._spawn([sys.executable, "-c",
                                            "import os;print(os.getsid(0)==os.getpid())"], 10)
        self.assertEqual(out.strip(), "True")

    def test_a_hung_gateway_call_fails_within_its_budget(self):
        # The REAL path end to end: a fake `openclaw` that never answers.
        with tempfile.TemporaryDirectory() as d:
            env = _write_fake_openclaw(d, "import time\ntime.sleep(60)")
            old = os.environ.copy()
            os.environ.clear()
            os.environ.update(env)
            try:
                t0 = time.time()
                r = _cli.gateway_call(M, {}, rpc_timeout_ms=1000, timeout_s=1)
            finally:
                os.environ.clear()
                os.environ.update(old)
        self.assertEqual(r["kind"], "timeout")
        self.assertLess(time.time() - t0, 10)
        self.assertIn("process group was killed", r["message"])

    def test_run_cli_gets_the_same_spawn_and_kill(self):
        t0 = time.time()
        rc, out, err = _cli.run_cli([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
        self.assertEqual((rc, out), (-1, ""))
        self.assertIn("timed out after 1s", err)
        self.assertLess(time.time() - t0, 10)


def _gw(kind, payload=None, message=None):
    return {"kind": kind, "payload": payload, "message": message, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


class StatusRead(unittest.TestCase):
    """Only the gateway's own `[NOT_FOUND]` refusal is a positive "no job". A runtime without the
    method cannot prove there is no job, so `unknown_method` is could-not-check."""

    def setUp(self):
        self._gc = _cli.gateway_call

    def tearDown(self):
        _cli.gateway_call = self._gc

    def test_only_not_found_is_no_job(self):
        cases = [(_gw("refused", message="[NOT_FOUND] No deploy has been started"), True),
                 (_gw("unknown_method", message="unknown method: senpi.deploy.status"), False),
                 (_gw("transport", message="closed"), False),
                 (_gw("timeout"), False),
                 (_gw("error", message="upstream said [NOT_FOUND] somewhere"), False),
                 (_gw("refused", message="[E_OTHER] x"), False)]
        for ans, want in cases:
            _cli.gateway_call = lambda *a, **k: ans
            snap, _tail, no_job = deploy.read_status(None)
            self.assertIsNone(snap)
            self.assertEqual(no_job, want, ans)

    def test_the_read_is_id_addressed_when_it_has_an_id(self):
        calls = []
        _cli.gateway_call = lambda method, params=None, **k: calls.append((method, params, k)) or _gw("ok", {})
        deploy.read_status("dpl-1")
        deploy.read_status(None)
        self.assertEqual(calls, [
            ("senpi.deploy.status", {"deployId": "dpl-1"}, {"rpc_timeout_ms": 30000, "timeout_s": 40}),
            ("senpi.deploy.status", {}, {"rpc_timeout_ms": 30000, "timeout_s": 40})])


def _pkg(pid="spider", d="/data/workspace/strategies/spider"):
    return SimpleNamespace(id=pid, dir=d, version="1.0.0", instances=[])


def _args(**kw):
    base = dict(budget=None, decision_model=None, tick_wait=None, max_wait=None, dry_run=False, json=False)
    base.update(kw)
    return SimpleNamespace(**base)


class OutputAndExitCodesUnchanged(unittest.TestCase):
    """Callers parse `deploy.py`'s stdout and branch on its exit code; moving to the gateway must not
    change either. Goldens of today's success (`--json`) and START refusal output."""

    def setUp(self):
        self._gc, self._sleep = _cli.gateway_call, deploy.time.sleep
        deploy.time.sleep = lambda s: None

    def tearDown(self):
        _cli.gateway_call, deploy.time.sleep = self._gc, self._sleep

    def test_a_live_deploy_prints_the_snapshot_document_and_exits_zero(self):
        snap = {"meta": {"deployId": "dpl-1", "packageId": "spider", "version": "1.0.0"},
                "state": {"status": "done", "overall": "live"},
                "report": {"overall": "live", "next": "python3 deploy.py verify spider"}}
        answers = [_gw("ok", {"deployId": "dpl-1"}), _gw("ok", snap)]
        _cli.gateway_call = lambda *a, **k: answers.pop(0)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = deploy.run_deploy(_pkg(), _args(budget=30.0, max_wait=150, json=True), lambda m: None)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), json.dumps(snap, indent=2) + "\n")

    def test_a_start_refusal_prints_the_gateways_text_alone_and_exits_two(self):
        msg = "[E_FUNDS_BELOW_FLOOR] Requested $500.00 but only $12.00 is free. Nothing was created."
        _cli.gateway_call = lambda *a, **k: _gw("refused", message=msg)
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            deploy.start_deploy(_pkg(), _args(budget=500.0), lambda m: None)
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(err.getvalue(), msg + "\n")

    def test_deploy_start_and_status_go_through_openclaw_gateway_call(self):
        # A full create through `main`, at the spawn level: no `openclaw senpi deploy` argv anywhere.
        real_spawn, ensure, validate = _cli._spawn, deploy.ensure_pkg, deploy.full_validate
        _cli.gateway_call = self._gc
        calls = []
        answers = [(0, json.dumps({"statuses": []}), "", False, None),     # K2 capability read
                   (0, json.dumps({"deployId": "dpl-1"}), "", False, None),
                   (0, json.dumps({"meta": {"deployId": "dpl-1", "packageId": "spider"},
                                   "state": {"status": "done", "overall": "live"}}), "", False, None)]
        _cli._spawn = lambda argv, timeout: calls.append(argv) or answers.pop(0)
        deploy.ensure_pkg = lambda arg, ref, log: _pkg()
        deploy.full_validate = lambda pkg: []
        try:
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                deploy.main(["deploy.py", "create", "spider", "--budget", "30"])
        finally:
            _cli._spawn, deploy.ensure_pkg, deploy.full_validate = real_spawn, ensure, validate
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual([c[:4] for c in calls], [["openclaw", "gateway", "call", "senpi.getHealthStatus"],
                                                  ["openclaw", "gateway", "call", "senpi.deploy.start"],
                                                  ["openclaw", "gateway", "call", "senpi.deploy.status"]])
        self.assertFalse(any(c[:3] == ["openclaw", "senpi", "deploy"] for c in calls))


if __name__ == "__main__":
    unittest.main()
