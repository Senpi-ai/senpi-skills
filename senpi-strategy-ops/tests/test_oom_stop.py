#!/usr/bin/env python3
"""Every money-path entry point, with its openclaw calls SIGKILLed, ends in the ONE out-of-memory stop.

Each round of review found another call site that missed the kill: a poll loop that said "poll now",
a re-validate that exited 2 (refused), a `--instance` close that read an unreadable inventory as "no
runtime" and steered at closing every instance. This test is the guard that ends that: it drives each
script's real `main` with `_cli._spawn` stubbed so the named calls answer and every other openclaw
call is killed by SIGKILL (rc -9), and holds every one to the shared stop (`_cli.oom_stop` /
`_cli.emit_oom`): exit `_cli.EXIT_OOM`, "stop here" and when to try again in the text, `oom: true` +
`killed_rc` in `--json`, and none of the steers that send the agent back into the box. A new entry
point or a new read on a money path belongs in `SCENARIOS`.

    python3 -m pytest senpi-strategy-ops/tests/test_oom_stop.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import _cli    # noqa: E402
import _journal  # noqa: E402
import close   # noqa: E402
import deploy  # noqa: E402
import status  # noqa: E402
from test_deploy_wrapper import (HEALTH_PLAIN, WALLET, _JournalDir, _ok, _proof_refused,  # noqa: E402
                                 _runtime_table, _status, _strategy)

KILL = (-9, "", _cli.killed_note(-9), False, None)
TIMED_OUT = (-1, "", "", True, None)
_NO_JOB = (1, "", "Gateway call failed: GatewayClientRequestError: [NOT_FOUND] No deploy has been "
                  "started on this agent.\n")
_LISTED = (0, _runtime_table(("spider-main", WALLET, "running")), "")

# Steers that send the agent back into a box that kills its calls, or misread the kill.
FORBIDDEN = (
    "Next: python3",            # poll now
    "Re-run this command",      # retry now
    "Retry,",                   # verify's could-not-check retry
    "Omit --instance",          # close every instance off an unreadable inventory
    "without its live runtime",  # an unreadable inventory read as "no runtime"
    "not available on this host",  # a killed probe read as "openclaw missing"
    "no openclaw",
    "did not PASS",             # a killed validate read as a verdict
)


class Box:
    """`_cli._spawn` stub. `answers` maps a gateway method (or a CLI verb: `--version`, `senpi
    validate`, `senpi update`) to an answer, or to a list of answers used in turn; anything not
    answered is SIGKILLed."""

    def __init__(self, answers):
        self.answers = {k: (list(v) if isinstance(v, list) else v) for k, v in answers.items()}
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(list(args))
        if list(args[:3]) == ["openclaw", "gateway", "call"]:
            key = args[3]
        else:
            key = args[1] if args[1].startswith("--") else " ".join(args[1:3])
        a = self.answers.get(key)
        if isinstance(a, list):
            a = a.pop(0) if a else None
        if a is None:
            return KILL
        return tuple(a) if len(a) == 5 else (*a, False, None)

    def sent(self, method):
        return [c for c in self.calls if c[:4] == ["openclaw", "gateway", "call", method]]


class FakeMCP:
    closed = []

    def mcp_call(self, tool, timeout=15, **kw):
        if tool == "strategy_close":
            FakeMCP.closed.append(kw.get("strategyId"))
            return {"success": True}
        assert tool == "strategy_list", tool
        return {"strategies": [_strategy()]}


def _stale(_pkg_dir):
    return _status(_proof_refused("runtime_version_changed"))


# (name, argv builder (pkg dir -> argv), answers). Every openclaw call not answered is killed.
SCENARIOS = [
    ("deploy.py create — the pre-START read", lambda d: ["deploy.py", "create", "spider", "--budget", "30", "--max-wait", "1"],
     {}),
    ("deploy.py create — START and its recovery reads", lambda d: ["deploy.py", "create", "spider", "--budget", "30", "--max-wait", "1"],
     {"senpi.getHealthStatus": HEALTH_PLAIN}),
    ("deploy.py create — the poll loop", lambda d: ["deploy.py", "create", "spider", "--budget", "30", "--max-wait", "1"],
     {"senpi.getHealthStatus": HEALTH_PLAIN, "senpi.deploy.start": _ok({"deployId": "dpl-a1b2c3d4"})}),
    ("deploy.py create — the stale-proof re-validate",
     lambda d: ["deploy.py", "create", "spider", "--budget", "30", "--max-wait", "1"],
     {"senpi.getHealthStatus": HEALTH_PLAIN, "senpi.deploy.start": _ok({"deployId": "dpl-a1b2c3d4"}),
      "senpi.deploy.status": "STALE"}),
    ("deploy.py status", lambda d: ["deploy.py", "status"], {}),
    ("deploy.py verify — the runtime list", lambda d: ["deploy.py", "verify", "spider"], {}),
    ("deploy.py verify — the empty-health retry", lambda d: ["deploy.py", "verify", "spider"],
     {"senpi.listRuntimes": _LISTED, "senpi.getHealthStatus": [_ok({"statuses": []})],
      "senpi.deploy.status": _NO_JOB}),
    ("deploy.py update (plan)", lambda d: ["deploy.py", "update", str(d), "--id", "spider-main"], {}),
    ("deploy.py update --apply", lambda d: ["deploy.py", "update", str(d), "--id", "spider-main", "--apply"], {}),
    ("close.py <pkg> — the inventory", lambda d: ["close.py", "spider"], {}),
    ("close.py <pkg> — the delete", lambda d: ["close.py", "spider"],
     {"--version": (0, "openclaw 10.10", ""), "senpi.listRuntimes": _LISTED}),
    ("close.py <pkg> --instance — the inventory", lambda d: ["close.py", "spider", "--instance", "main"], {}),
    ("status.py — openclaw --version", lambda d: ["status.py"], {}),
    ("status.py — the health read", lambda d: ["status.py"],
     {"--version": (0, "openclaw 10.10", ""), "senpi.listRuntimes": _LISTED}),
]


class EveryMoneyPathStopsOnASigkill(_JournalDir, unittest.TestCase):

    def setUp(self):
        self._saved = (_cli._spawn, deploy.time.sleep, deploy.ensure_pkg, deploy.full_validate,
                       deploy.local_pkg, getattr(deploy, "MCPClient", None), close.MCPClient,
                       close._pkg.load, status.MCPClient)
        deploy.time.sleep = lambda _s: None
        pkg = self._use_temp_root()
        (Path(pkg.dir) / "main").mkdir(parents=True)
        (Path(pkg.dir) / "main" / deploy.PROOF_FILE).write_text("{}")
        pkg.instances = [types.SimpleNamespace(name="main", runtime_name="spider-main",
                                               runtime_path=Path(pkg.dir) / "main" / "runtime.yaml"),
                         types.SimpleNamespace(name="hedge", runtime_name="spider-hedge",
                                               runtime_path=Path(pkg.dir) / "hedge" / "runtime.yaml")]
        deploy.ensure_pkg = lambda arg, ref, log: pkg
        deploy.full_validate = lambda p: []
        deploy.local_pkg = lambda arg: pkg
        deploy.MCPClient = close.MCPClient = status.MCPClient = FakeMCP
        close._pkg.load = lambda arg: types.SimpleNamespace(id="spider", version="1.0.0",
                                                            instances=pkg.instances,
                                                            dir=Path(tempfile.mkdtemp()))
        FakeMCP.closed = []

    def tearDown(self):
        (_cli._spawn, deploy.time.sleep, deploy.ensure_pkg, deploy.full_validate, deploy.local_pkg,
         mcp, close.MCPClient, close._pkg.load, status.MCPClient) = self._saved
        if mcp is not None:
            deploy.MCPClient = mcp

    def _drive(self, argv, answers):
        if answers.get("senpi.deploy.status") == "STALE":
            answers = dict(answers, **{"senpi.deploy.status": _stale(self.pkg.dir)})
        box = Box(answers)
        _cli._spawn = box
        main = {"deploy.py": deploy.main, "close.py": close.main, "status.py": status.main}[argv[0]]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue(), box

    def test_every_entry_point_ends_in_the_one_out_of_memory_stop(self):
        for name, argv_for, answers in SCENARIOS:
            for as_json in (False, True):
                argv = argv_for(self.pkg.dir) + (["--json"] if as_json else [])
                with self.subTest(name, json=as_json):
                    code, out, err, box = self._drive(argv, dict(answers))
                    text = out + err
                    self.assertEqual(code, _cli.EXIT_OOM, text)
                    self.assertIn("out of memory", text)
                    self.assertIn("stop here", text)
                    self.assertIn("after the box restarts or memory frees", text)   # the way back
                    for steer in FORBIDDEN:
                        self.assertNotIn(steer, text)
                    self.assertEqual(FakeMCP.closed, [])              # never a strategy_close
                    if "pre-START" in name:
                        self.assertEqual(box.sent("senpi.deploy.start"), [])   # nothing dispatched
                    if as_json:
                        doc = json.loads(out)
                        self.assertIs(doc["oom"], True)
                        self.assertEqual(doc["killed_rc"], -9)
                        self.assertNotEqual((doc.get("state") or {}).get("overall"), "refused")


class _BoxCase(EveryMoneyPathStopsOnASigkill):
    """The same harness, for the paths that need more than `SCENARIOS` can say (a journal entry, a
    budget that runs out mid-call, a second signal)."""

    def test_every_entry_point_ends_in_the_one_out_of_memory_stop(self):
        pass   # run once, in the parent class

    def _open_request(self, budget=30.0):
        rid = "dpl-req-20261028T000000Z-deadbeef"
        _journal.write("spider", {"packageId": "spider", "requestId": rid,
                                  "sentAt": "2026-10-28T00:00:00Z", "verb": "create",
                                  "paramsKey": {"packageDir": str(self.pkg.dir), "budget": budget},
                                  "resolved": None})
        return rid

    def _assert_oom_stop(self, code, out, err, as_json=False):
        text = out + err
        self.assertEqual(code, _cli.EXIT_OOM, text)
        self.assertIn("stop here", text)
        self.assertIn("after the box restarts or memory frees", text)
        for steer in FORBIDDEN + ("Poll until closed",):
            self.assertNotIn(steer, text)
        if as_json:
            doc = json.loads(out)
            self.assertIs(doc["oom"], True)
            self.assertEqual(doc["killed_rc"], -9)


class StartKilledWithNoReadMade(_BoxCase):
    """A SIGKILLed START that the budget left no recovery read for: the kill is all that is known."""

    def test_a_killed_start_with_no_read_made_is_the_stop_and_never_says_the_reads_found_nothing(self):
        saved = deploy._budget_spent
        self.addCleanup(setattr, deploy, "_budget_spent", saved)
        for as_json in (False, True):
            with self.subTest(json=as_json):
                started = []
                box = Box({"senpi.getHealthStatus": HEALTH_PLAIN})

                def spawn(args, timeout=60, box=box, started=started):
                    if args[:4] == ["openclaw", "gateway", "call", "senpi.deploy.start"]:
                        started.append(True)
                    return box(args, timeout)
                deploy._budget_spent = lambda started=started: bool(started)
                argv = ["deploy.py", "create", "spider", "--budget", "30"] + (["--json"] if as_json else [])
                _cli._spawn = spawn
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                        self.assertRaises(SystemExit) as ctx:
                    deploy.main(argv)
                self.assertEqual(box.sent("senpi.deploy.status"), [])     # no read was made
                self._assert_oom_stop(ctx.exception.code, out.getvalue(), err.getvalue(), as_json)
                self.assertIn("no read of the job record could be made", err.getvalue())
                self.assertNotIn("found no job", out.getvalue() + err.getvalue())


class JobRecordReadKilled(_BoxCase):
    """Every caller of `deploy._why_a_job_may_be_running` gets the read's rc and stops on a SIGKILL."""

    def test_close_with_nothing_live_and_a_killed_job_record_read(self):
        rid = self._open_request()
        saved = FakeMCP.mcp_call
        self.addCleanup(setattr, FakeMCP, "mcp_call", saved)
        FakeMCP.mcp_call = lambda self_, tool, timeout=15, **kw: {
            "strategies": [_strategy(status="CLOSED")]}
        for as_json in (False, True):
            with self.subTest(json=as_json):
                code, out, err, box = self._drive(["close.py", "spider"] + (["--json"] if as_json else []), {})
                self._assert_oom_stop(code, out, err, as_json)
                self.assertTrue(box.sent("senpi.deploy.status"))
                self.assertIn("NOT torn down", out + err)
                self.assertIsNone(_journal.read("spider")["resolved"])    # the request stays open
                self.assertEqual(_journal.read("spider")["requestId"], rid)

    def test_a_triggered_close_whose_job_record_read_is_killed(self):
        self._open_request()
        for as_json in (False, True):
            with self.subTest(json=as_json):
                FakeMCP.closed = []
                answers = {"--version": (0, "openclaw 10.10", ""),
                           "senpi.listRuntimes": [_LISTED, _ok({"runtimes": []})],
                           "senpi.deleteRuntime": _ok({"deleted": True})}
                code, out, err, _box = self._drive(["close.py", "spider"] + (["--json"] if as_json else []),
                                                   answers)
                self._assert_oom_stop(code, out, err, as_json)
                self.assertEqual(FakeMCP.closed, ["str-1234abcd"])          # the close did go out
                self.assertIsNone(_journal.read("spider")["resolved"])
                if as_json:
                    self.assertIn("openDeployRequest", json.loads(out))

    def test_create_with_changed_params_and_a_killed_job_record_read(self):
        self._open_request(budget=99.0)    # an earlier START at another budget, never answered
        for as_json in (False, True):
            with self.subTest(json=as_json):
                code, out, err, box = self._drive(
                    ["deploy.py", "create", "spider", "--budget", "30"] + (["--json"] if as_json else []),
                    {"senpi.getHealthStatus": HEALTH_PLAIN})   # a runtime that does not honour ids
                self._assert_oom_stop(code, out, err, as_json)
                self.assertEqual(box.sent("senpi.deploy.start"), [])    # nothing dispatched
                self.assertIsNone(_journal.read("spider")["resolved"])


class InstanceWithAReadInventoryButNoRuntime(_BoxCase):
    """`--instance` that maps to no open strategy off a READ inventory: never a steer at a wallet
    another instance's runtime claims, never at all of the package, never past an open deploy START."""

    HEDGE_WALLET = "0x" + "be" * 20
    VERSION = {"--version": (0, "openclaw 10.10", "")}

    def _strategies(self, *rows):
        saved = FakeMCP.mcp_call
        self.addCleanup(setattr, FakeMCP, "mcp_call", saved)

        def call(self_, tool, timeout=15, **kw):
            if tool == "strategy_close":
                FakeMCP.closed.append(kw.get("strategyId"))
                return {"success": True}
            return {"strategies": [dict(_strategy(name=n, wallet=w), strategyId=sid) for sid, n, w in rows]}
        FakeMCP.mcp_call = call

    def test_a_wallet_another_instances_runtime_claims_is_never_listed(self):
        # spider-main runs on WALLET; hedge's runtime is gone and its wallet is orphaned.
        self._strategies(("str-main", "spider-main", WALLET), ("str-hedge", "spider-hedge", self.HEDGE_WALLET))
        code, _out, _err, _box = self._drive(["close.py", "spider", "--instance", "hedge"],
                                             dict(self.VERSION, **{"senpi.listRuntimes": _LISTED}))
        msg = str(code)
        self.assertIn("NOTHING WAS CLOSED", msg)
        self.assertIn("--strategy-id str-hedge", msg)
        self.assertNotIn("str-main", msg)                  # main's live sleeve is not a candidate
        self.assertIn(_cli.STATUS_PY, msg)
        self.assertNotIn("Omit --instance", msg)
        self.assertNotIn("close all", msg)
        self.assertEqual(FakeMCP.closed, [])

    def test_every_open_wallet_claimed_lists_nothing(self):
        self._strategies(("str-main", "spider-main", WALLET))
        code, _out, _err, _box = self._drive(["close.py", "spider", "--instance", "hedge"],
                                             dict(self.VERSION, **{"senpi.listRuntimes": _LISTED}))
        msg = str(code)
        self.assertIn("NOTHING WAS CLOSED", msg)
        self.assertNotIn("--strategy-id", msg)
        self.assertEqual(FakeMCP.closed, [])

    def test_a_runtime_on_a_wallet_with_no_open_strategy_lists_nothing_and_names_its_delete(self):
        self._strategies(("str-main", "spider-main", WALLET))
        listed = (0, _runtime_table(("spider-main", WALLET, "running"),
                                    ("spider-hedge", self.HEDGE_WALLET, "running")), "")
        code, _out, _err, box = self._drive(["close.py", "spider", "--instance", "hedge"],
                                            dict(self.VERSION, **{"senpi.listRuntimes": listed}))
        msg = str(code)
        self.assertIn("NOTHING WAS CLOSED", msg)
        self.assertIn("already closed", msg)
        self.assertNotIn("--strategy-id", msg)            # none of the open ones is hedge
        self.assertNotIn("str-main", msg)
        self.assertIn('senpi.deleteRuntime --json --timeout', msg)
        self.assertIn('"id": "spider-hedge"', msg)
        self.assertEqual(box.sent("senpi.deleteRuntime"), [])   # named, not run
        self.assertEqual(FakeMCP.closed, [])

    def test_an_open_deploy_start_refuses_before_any_strategy_id(self):
        # A lost START's job may have created hedge's wallet and not installed its runtime yet.
        self._strategies(("str-main", "spider-main", WALLET), ("str-hedge", "spider-hedge", self.HEDGE_WALLET))
        rid = self._open_request()
        running = _ok({"state": {"status": "running"},
                       "meta": {"deployId": "dpl-1", "packageId": "spider", "requestId": rid}})
        for job, want in ((running, 2), (None, _cli.EXIT_OOM)):     # None: the record read is killed
            with self.subTest(killed=job is None):
                answers = dict(self.VERSION, **{"senpi.listRuntimes": _LISTED})
                if job:
                    answers["senpi.deploy.status"] = job
                code, out, err, box = self._drive(["close.py", "spider", "--instance", "hedge"], answers)
                text = f"{code}{out}{err}"
                self.assertEqual(code, want, text)
                self.assertTrue(box.sent("senpi.deploy.status"))
                self.assertIn("NOT torn down", text)
                self.assertIn(rid, text)
                self.assertNotIn("--strategy-id", text)
                self.assertIn("close.py spider --instance hedge", text)
                self.assertIsNone(_journal.read("spider")["resolved"])
                self.assertEqual(FakeMCP.closed, [])


class StatusExitZeroIsEarnedOnlyByAReadRuntimeList(_BoxCase):
    """`oom_wait` names status.py's exit 0 as the way back, so 0 must mean the runtime list was read."""

    def _status(self, answers, *args):
        code, out, err, _box = self._drive(["status.py", "--fast", *args], answers)
        return code, out + err

    def test_a_timed_out_runtime_list_is_not_exit_0(self):
        code, text = self._status({"--version": (0, "openclaw 10.10", ""), "senpi.listRuntimes": TIMED_OUT})
        self.assertEqual(code, _cli.STATUS_INVENTORY_UNREAD, text)
        self.assertNotEqual(code, 0)
        self.assertIn("runtime state is UNKNOWN", text)

    def test_no_openclaw_here_is_not_exit_0(self):
        none = (-1, "", "", False, FileNotFoundError("openclaw"))
        code, text = self._status({"--version": none, "senpi.listRuntimes": none})
        self.assertEqual(code, _cli.STATUS_INVENTORY_UNREAD, text)

    def test_a_read_runtime_list_is_exit_0(self):
        code, text = self._status({"--version": (0, "openclaw 10.10", ""), "senpi.listRuntimes": _LISTED})
        self.assertEqual(code, 0, text)
        code, text = self._status({"--version": (0, "openclaw 10.10", ""), "senpi.listRuntimes": _LISTED},
                                  "--json")
        self.assertEqual(code, 0, text)

    def test_the_way_back_names_exit_0_as_a_read_list(self):
        w = _cli.oom_wait()
        self.assertIn("exiting 0", w)
        self.assertIn("only once it has read the runtime list", w)
        self.assertIn(f"{_cli.STATUS_INVENTORY_UNREAD} = the list could not be read", w)

    def test_the_way_back_has_a_way_out_when_1_outlasts_the_memory(self):
        # A wedged plugin answers `senpi.listRuntimes` with an error forever: 1 never turns to 0.
        w = _cli.oom_wait()
        self.assertIn("keeps exiting 1", w)
        self.assertIn("openclaw plugins install @senpi-ai/runtime", w)
        self.assertNotIn("forever", w)


# ---- every documented exit-code map for deploy / verify / update / close / status carries 9 ----

REPO = HERE.parent.parent
# A map of these scripts' codes, by the words that open it. Each must name 9 and what it means.
MAP_ANCHORS = (
    re.compile(r"`0`\**\s+verified"),                       # verify: 0 verified / 3 / 1
    re.compile(r"`0`\**\s+live\s+·\s+\**`2`\**\s+refused"),  # D-12 inline: 0 live · 2 refused · …
    re.compile(r"`0`\**\s+planned/applied"),                # update
)
TABLE_ANCHOR = re.compile(r"^\|\s*`0`\s*\|\s*`live`", re.M)  # D-12 table
WINDOW = 900
# Where a map lives today; a map moved or dropped must fail here, not pass by finding nothing.
EXPECTED = {"CLAUDE.md", "senpi-strategy-ops/SKILL.md", "senpi-strategy-ops/references/lifecycle.md",
            "senpi-strategy-ops/references/liveness-verification.md",
            "senpi-strategy-ops/references/editing-a-live-strategy.md",
            "senpi-trading-runtime/references/runtime-cli.md"}


def _skill_docs():
    yield REPO / "CLAUDE.md"
    for p in sorted(REPO.glob("*/SKILL.md")):
        yield p
    for p in sorted(REPO.glob("*/references/**/*.md")):
        yield p


class EveryExitMapCarriesNine(unittest.TestCase):

    def test_every_documented_map_lists_9_as_the_out_of_memory_stop(self):
        seen = set()
        for path in _skill_docs():
            text = path.read_text()
            rel = str(path.relative_to(REPO))
            spans = [(m.start(), m.start() + WINDOW) for a in MAP_ANCHORS for m in a.finditer(text)]
            spans += [(m.start(), text.find("\n\n", m.start()) % (len(text) + 1)) for m in TABLE_ANCHOR.finditer(text)]
            for start, end in spans:
                seen.add(rel)
                window = re.sub(r"\s*\n>?\s*", " ", text[start:end])   # one line, no `>` quoting
                with self.subTest(rel, at=text[start:start + 40]):
                    self.assertRegex(window, r"`9`", f"{rel}: an exit map without 9")
                    self.assertIn("out of memory", window)
                    self.assertRegex(window, r"`status\.py` exits `0`")
        self.assertLessEqual(EXPECTED, seen)

    def test_run_sh_handles_9_with_the_stop_and_never_the_poll(self):
        sh = (HERE.parent / "scripts" / "run.sh").read_text()
        start = sh.index('WHERE="')
        block = sh[start:sh.index("esac", start) + len("esac")]
        for rc, want in ((9, 9), (1, 1)):
            with self.subTest(rc=rc):
                p = subprocess.run(["bash", "-c", "set -euo pipefail; ID=spider; ROOT=/r; BRANCH=b; "
                                    f"BUDGET=30; RC={rc}\n" + block], capture_output=True, text=True)
                self.assertEqual(p.returncode, want, p.stderr)
                if rc == 9:
                    # deploy.py's report is the one producer: exit 9 also comes before any START
                    # ("NOTHING WAS DISPATCHED"), so run.sh claims nothing of its own.
                    self.assertIn("out of memory", p.stderr)
                    self.assertIn("relay its report above as written", p.stderr)
                    self.assertIn("don't re-run or poll now", p.stderr)
                    for claim in ("may already be funded", "UNKNOWN", "outcome is unknown",
                                  "read it until", "The job's own record"):
                        self.assertNotIn(claim, p.stderr)
        self.assertRegex(sh.split("set -euo pipefail")[0], r"\b9 is the out-of-memory stop")


if __name__ == "__main__":
    unittest.main()
