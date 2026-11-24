#!/usr/bin/env python3
"""Runtime calls go through `senpi call` first, and fall back to `openclaw gateway call` ONLY when
senpi provably sent nothing (runtime split + gateway sleep, Task 18).

`senpi call senpi.<method> --json --timeout <ms> --params '<json>'` is the runtime daemon's CLI
(senpi-trading-runtime `src/cli/standalone.ts` + `src/cli/daemon-call.ts`). It prints exactly what
`openclaw gateway call --json` prints on 10.x, plus `sent: "nothing" | "maybe"` in every error:

  - `gateway_request_error`: the daemon answered — a refusal (today's code + message) or
    `unknown method: <method>`;
  - `gateway_transport_error`: no answer — kind `unreachable` (nothing sent) or `timeout` /
    `closed` (may have been sent).

On a box without the daemon, `/usr/local/bin/senpi` exits 1 with "not installed on this box yet"
(senpi-agent `scripts/senpi-launcher.sh`). The envelopes below are copied from daemon-call.ts.

Run:  python3 -m pytest senpi-strategy-ops/tests/test_senpi_call.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import signal
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import _cli  # noqa: E402
import deploy  # noqa: E402


def _env(error):
    return (1, json.dumps({"ok": False, "error": error}, indent=2) + "\n", "", False, None)


# ---- what `senpi call` prints (daemon-call.ts), as `_spawn` answers ----
REFUSED = _env({"type": "gateway_request_error", "code": "E_DEPLOY_IN_PROGRESS",
                "message": "[E_DEPLOY_IN_PROGRESS] a deploy is already running (dpl-1)", "sent": "nothing"})
NOT_FOUND_READ = _env({"type": "gateway_request_error", "code": "NOT_FOUND",
                       "message": "[NOT_FOUND] No deploy has been started on this agent.", "sent": "nothing"})
UNKNOWN_METHOD = _env({"type": "gateway_request_error", "code": "UNKNOWN_METHOD",
                       "message": "unknown method: senpi.deploy.start", "sent": "nothing"})
UNREACHABLE = _env({"type": "gateway_transport_error", "kind": "unreachable",
                    "message": "runtime restarting or not running, retry (nothing was sent)", "sent": "nothing"})
TIMEOUT_MAYBE = _env({"type": "gateway_transport_error", "kind": "timeout",
                      "message": "gateway timeout after 30000ms; the request may have been sent: check "
                                 "status before retrying", "sent": "maybe"})
CLOSED_MAYBE = _env({"type": "gateway_transport_error", "kind": "closed",
                     "message": "runtime restarting or not running, retry (the request may have been sent: "
                                "check status before retrying)", "sent": "maybe"})
UNREADABLE_MAYBE = _env({"type": "cli_error", "message": "unreadable answer from the runtime daemon (HTTP 502)",
                         "sent": "maybe"})
BAD_ARGS = _env({"type": "cli_error", "message": "error: --params must be a JSON object", "sent": "nothing"})
WRITE_FAILED_MAYBE = _env({"type": "gateway_request_error", "code": "INTERNAL_ERROR",
                           "message": "boom", "sent": "maybe"})
NO_SENT_FIELD = _env({"type": "gateway_transport_error", "kind": "closed", "message": "socket hang up"})
NOT_INSTALLED = (1, "", "senpi: the runtime daemon is not installed on this box yet (it is installed on a "
                        "box running RUNTIME_MODE=daemon)\n", False, None)
OLD_SENPI = (1, "", "error: unknown command 'call'\n", False, None)
API_SKEW = (1, "", "senpi: this CLI speaks runtime API v1, the daemon speaks v3; update the runtime\n",
            False, None)
NO_BINARY = (-1, "", "", False, FileNotFoundError())
OUR_TIMEOUT = (-1, "", "", True, None)
KILLED = (-signal.SIGKILL, "", _cli.killed_note(-signal.SIGKILL), False, None)
OK = (0, json.dumps({"deployId": "dpl-1"}, indent=2) + "\n", "", False, None)
OPENCLAW_OK = (0, json.dumps({"deployId": "dpl-oc"}), "", False, None)


class Fake:
    """`_cli._spawn`, answering per CLI (`senpi` / `openclaw`) from its own queue."""

    def __init__(self, senpi, openclaw=()):
        self.q = {"senpi": list(senpi), "openclaw": list(openclaw)}
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        return self.q[argv[0]].pop(0)

    def clis(self):
        return [a[0] for a, _t in self.calls]


class _Base(unittest.TestCase):
    def setUp(self):
        self._spawn = _cli._spawn

    def tearDown(self):
        _cli._spawn = self._spawn

    def call(self, senpi, openclaw=(), method="senpi.deploy.start", **kw):
        fake = Fake(senpi, openclaw)
        _cli._spawn = fake
        return _cli.gateway_call(method, {"packageDir": "/p"}, **kw), fake


class SenpiAnswers(_Base):
    """senpi answered (or may have): its answer is final, openclaw is never run."""

    def test_ok_is_senpis_payload(self):
        r, fake = self.call([OK])
        self.assertEqual((r["kind"], r["payload"], r["via"]), ("ok", {"deployId": "dpl-1"}, "senpi"))
        self.assertEqual(fake.clis(), ["senpi"])
        self.assertEqual(r["argv"][:3], ["senpi", "call", "senpi.deploy.start"])

    def test_a_refusal_is_relayed_with_its_code_and_never_falls_back(self):
        # The daemon marks an answered refusal `sent: nothing` (nothing was applied). It is still THE
        # answer: on a box whose gateway sleeps, a fallback would turn it into a transport error.
        for answer, code in ((REFUSED, "E_DEPLOY_IN_PROGRESS"), (NOT_FOUND_READ, "NOT_FOUND")):
            r, fake = self.call([answer])
            self.assertEqual((r["kind"], r["code"], r["via"]), ("refused", code, "senpi"), answer)
            self.assertTrue(r["message"].startswith(f"[{code}]"))
            self.assertEqual(r["sent"], "nothing")
            self.assertEqual(fake.clis(), ["senpi"])

    def test_maybe_sent_never_falls_back(self):
        # The one rule that keeps a deploy from being sent twice.
        cases = {"transport timeout": (TIMEOUT_MAYBE, "transport"),
                 "connection closed": (CLOSED_MAYBE, "transport"),
                 "unreadable answer": (UNREADABLE_MAYBE, "error"),
                 "write failed mid-way": (WRITE_FAILED_MAYBE, "error"),
                 "envelope without sent": (NO_SENT_FIELD, "transport"),
                 "our wall clock": (OUR_TIMEOUT, "timeout"),
                 "SIGKILL": (KILLED, "unreadable"),
                 "rc 0 not json": ((0, "garbage", "", False, None), "unreadable"),
                 "crash text": ((1, "", "TypeError: x is undefined\n", False, None), "unreadable")}
        for name, (answer, kind) in cases.items():
            r, fake = self.call([answer], openclaw=[OPENCLAW_OK])
            self.assertEqual(fake.clis(), ["senpi"], name)
            self.assertEqual((r["kind"], r["via"]), (kind, "senpi"), name)
            self.assertIsNone(r["fallback_from"], name)

    def test_a_killed_senpi_keeps_its_rc_for_the_out_of_memory_stop(self):
        r, _fake = self.call([KILLED], openclaw=[OPENCLAW_OK])
        self.assertTrue(_cli.is_sigkill(r["rc"]))


class NothingSentFallsBack(_Base):
    """senpi provably reached nothing: the same call goes to `openclaw gateway call`, once."""

    def test_every_nothing_sent_shape_falls_back(self):
        for name, answer in {"no daemon on this box": NOT_INSTALLED, "daemon unreachable": UNREACHABLE,
                             "unknown method": UNKNOWN_METHOD, "no senpi binary": NO_BINARY,
                             "senpi without `call`": OLD_SENPI, "API version skew": API_SKEW,
                             "senpi rejected its arguments": BAD_ARGS}.items():
            r, fake = self.call([answer], openclaw=[OPENCLAW_OK])
            self.assertEqual(fake.clis(), ["senpi", "openclaw"], name)
            self.assertEqual((r["kind"], r["payload"], r["via"]), ("ok", {"deployId": "dpl-oc"}, "openclaw"),
                             name)
            self.assertTrue(r["fallback_from"], name)
            argv = fake.calls[1][0]
            self.assertEqual(argv[:4], ["openclaw", "gateway", "call", "senpi.deploy.start"], name)
            self.assertEqual(json.loads(argv[argv.index("--params") + 1]), {"packageDir": "/p"}, name)

    def test_the_fallback_answer_is_classified_like_any_other(self):
        refused_5x = (1, "", "Gateway call failed: GatewayClientRequestError: [E_X] no\n", False, None)
        r, _fake = self.call([NOT_INSTALLED], openclaw=[refused_5x])
        self.assertEqual((r["kind"], r["via"]), ("refused", "openclaw"))
        r, _fake = self.call([NOT_INSTALLED], openclaw=[OUR_TIMEOUT])
        self.assertEqual((r["kind"], r["via"]), ("timeout", "openclaw"))

    def test_the_fallback_runs_inside_the_same_wall_clock(self):
        r, fake = self.call([NOT_INSTALLED], openclaw=[OPENCLAW_OK], rpc_timeout_ms=30000, timeout_s=40)
        (_a1, t1), (a2, t2) = fake.calls
        self.assertEqual(t1, 40)
        self.assertLessEqual(t2, 40)
        self.assertLessEqual(int(a2[a2.index("--timeout") + 1]), 30000)

    def test_openclaw_missing_too_keeps_senpis_own_answer(self):
        r, fake = self.call([UNKNOWN_METHOD], openclaw=[NO_BINARY])
        self.assertEqual(fake.clis(), ["senpi", "openclaw"])
        self.assertEqual((r["kind"], r["via"]), ("unknown_method", "senpi"))

    def test_neither_cli_ran_is_spawn_failed(self):
        r, _fake = self.call([NO_BINARY], openclaw=[NO_BINARY])
        self.assertEqual(r["kind"], "spawn_failed")
        self.assertIn("senpi", r["message"])

    def test_the_gate_itself(self):
        nothing = (NOT_INSTALLED, UNREACHABLE, UNKNOWN_METHOD, NO_BINARY, OLD_SENPI, API_SKEW, BAD_ARGS)
        maybe = (OK, REFUSED, NOT_FOUND_READ, TIMEOUT_MAYBE, CLOSED_MAYBE, UNREADABLE_MAYBE,
                 WRITE_FAILED_MAYBE, NO_SENT_FIELD, KILLED)
        for a in nothing:
            self.assertTrue(_cli.senpi_sent_nothing(a[0], a[1], a[2], a[4]), a)
        for a in maybe:
            self.assertFalse(_cli.senpi_sent_nothing(a[0], a[1], a[2], a[4]), a)


class DeployStartNeverTwice(_Base):
    """deploy.py on top of gateway_call: one START out, whatever senpi said."""

    def setUp(self):
        super().setUp()
        self._sleep = deploy.time.sleep
        deploy.time.sleep = lambda s: None

    def tearDown(self):
        deploy.time.sleep = self._sleep
        super().tearDown()

    def _start(self, fake):
        _cli._spawn = fake
        pkg = SimpleNamespace(id="spider", dir=Path("/pkg/spider"), instances=[])
        a = SimpleNamespace(budget=300.0, decision_model=None, tick_wait=None, max_wait=None,
                            dry_run=False, json=False)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                return deploy.start_deploy(pkg, a, lambda m: None), err.getvalue()
            except SystemExit as e:
                return ("exit", e.code), err.getvalue()

    def _starts(self, fake):
        return [a for a, _t in fake.calls if "senpi.deploy.start" in a]

    def test_a_start_that_may_have_been_sent_is_recovered_never_resent(self):
        not_found = _env({"type": "gateway_request_error", "code": "NOT_FOUND",
                          "message": "[NOT_FOUND] No deploy has been started on this agent.",
                          "sent": "nothing"})
        fake = Fake([TIMEOUT_MAYBE] + [not_found] * deploy.RECOVER_READS, openclaw=[OPENCLAW_OK] * 5)
        out, err = self._start(fake)
        self.assertEqual(out, ("exit", 1))
        self.assertEqual(len(self._starts(fake)), 1)
        self.assertNotIn("openclaw", fake.clis())
        self.assertIn("MAY BE RUNNING", err)

    def test_a_refused_start_is_relayed_and_never_resent(self):
        fake = Fake([REFUSED], openclaw=[OPENCLAW_OK])
        out, err = self._start(fake)
        self.assertEqual(out, ("exit", deploy.EXIT_CODES["refused"]))
        self.assertIn("[E_DEPLOY_IN_PROGRESS]", err)
        self.assertEqual(fake.clis(), ["senpi"])

    def test_a_box_without_the_daemon_starts_through_openclaw_once(self):
        fake = Fake([NOT_INSTALLED], openclaw=[OPENCLAW_OK])
        out, _err = self._start(fake)
        self.assertEqual(out, "dpl-oc")
        self.assertEqual([a[0] for a in self._starts(fake)], ["senpi", "openclaw"])


class StartRequestIdCapability(_Base):
    """deploy.py still reads `deploy.capabilities.deployStartRequestId` from the runtime's health —
    the daemon's `senpi.getHealthStatus` keeps it."""

    def _caps(self, senpi, openclaw=()):
        fake = Fake(senpi, openclaw)
        _cli._spawn = fake
        return deploy.start_request_ids_supported(lambda m: None), fake

    def test_the_daemons_health_advertises_request_ids(self):
        health = (0, json.dumps({"deploy": {"capabilities": {"deployStartRequestId": True}}}), "", False, None)
        ok, fake = self._caps([health])
        self.assertIs(ok, True)
        self.assertEqual(fake.calls[0][0][:3], ["senpi", "call", "senpi.getHealthStatus"])

    def test_without_the_capability_no_id_is_relied_on(self):
        ok, _fake = self._caps([(0, json.dumps({"deploy": {"capabilities": {}}}), "", False, None)])
        self.assertIs(ok, False)

    def test_no_daemon_reads_the_capability_through_openclaw(self):
        health = (0, json.dumps({"deploy": {"capabilities": {"deployStartRequestId": True}}}), "", False, None)
        ok, fake = self._caps([NOT_INSTALLED], openclaw=[health])
        self.assertIs(ok, True)
        self.assertEqual(fake.clis(), ["senpi", "openclaw"])

    def test_an_unreadable_health_is_unknown_not_false(self):
        ok, _fake = self._caps([TIMEOUT_MAYBE])
        self.assertIsNone(ok)


class AvailabilityProbe(unittest.TestCase):
    """`senpi --version` first; `openclaw --version` only when senpi does not answer."""

    def setUp(self):
        self._run = _cli.run_cli

    def tearDown(self):
        _cli.run_cli = self._run

    def _probe(self, answers):
        calls = []

        def fake(args, timeout=60):
            calls.append(list(args))
            return answers[args[0]]
        _cli.run_cli = fake
        why = []
        return _cli.runtime_cli_available(why=why), calls, why

    def test_senpi_answers(self):
        ok, calls, why = self._probe({"senpi": (0, "4.2.0\n", "")})
        self.assertTrue(ok)
        self.assertEqual(calls, [["senpi", "--version"]])
        self.assertEqual(why, [])

    def test_no_daemon_falls_back_to_openclaw(self):
        ok, calls, _why = self._probe({"senpi": NOT_INSTALLED[:3], "openclaw": (0, "2026.10.10\n", "")})
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in calls], ["senpi", "openclaw"])

    def test_neither_is_a_cause_naming_both(self):
        ok, _calls, why = self._probe({"senpi": (-1, "", "command not found: senpi"),
                                       "openclaw": (-1, "", "command not found: openclaw")})
        self.assertFalse(ok)
        self.assertIn("openclaw --version", why[0])
        self.assertIn("senpi --version", why[0])

    def test_a_killed_probe_is_out_of_memory_and_runs_nothing_else(self):
        ok, calls, why = self._probe({"senpi": (-signal.SIGKILL, "", "")})
        self.assertFalse(ok)
        self.assertEqual(calls, [["senpi", "--version"]])
        self.assertTrue(_cli.sigkilled(why))


class RuntimeVerbs(unittest.TestCase):
    """`senpi <verb>` (validate, update, status/state -r); `openclaw senpi <verb>` only where senpi
    cannot run at all — the two are one implementation, so any other senpi answer is final."""

    def setUp(self):
        self._run = _cli.run_cli

    def tearDown(self):
        _cli.run_cli = self._run

    def _verb(self, answers, args=("update", "/pkg/spider/main", "--apply")):
        calls = []

        def fake(argv, timeout=60):
            calls.append(list(argv))
            return answers[argv[0]].pop(0)
        _cli.run_cli = fake
        return _cli.run_runtime_cli(list(args), timeout=60), calls

    def test_senpi_runs_the_verb(self):
        r, calls = self._verb({"senpi": [(0, "applied\n", "")]})
        self.assertEqual(r, (0, "applied\n", ""))
        self.assertEqual(calls, [["senpi", "update", "/pkg/spider/main", "--apply"]])

    def test_no_daemon_runs_openclaw_senpi_once(self):
        r, calls = self._verb({"senpi": [NOT_INSTALLED[:3]], "openclaw": [(0, "applied\n", "")]})
        self.assertEqual(r[0], 0)
        self.assertEqual(calls[1], ["openclaw", "senpi", "update", "/pkg/spider/main", "--apply"])

    def test_any_other_senpi_failure_is_final(self):
        for answer in ((1, "", "\nTip: the runtime daemon is restarting or not running; retry in a moment.\n"),
                       (2, "", "[E_UPDATE_PROOF_REQUIRED] no proof\n"), (-1, "", "timed out after 60s: senpi")):
            r, calls = self._verb({"senpi": [answer], "openclaw": [(0, "applied\n", "")]})
            self.assertEqual(r, answer)
            self.assertEqual(len(calls), 1, answer)


class Hints(unittest.TestCase):
    def test_a_manual_runtime_call_names_both_clis(self):
        h = _cli.runtime_call_hint("senpi.deleteRuntime", {"id": "spider-main"}, 90000)
        self.assertIn("`senpi call senpi.deleteRuntime --json --timeout 90000", h)
        self.assertIn("`openclaw gateway call senpi.deleteRuntime --json --timeout 90000", h)


if __name__ == "__main__":
    unittest.main()
