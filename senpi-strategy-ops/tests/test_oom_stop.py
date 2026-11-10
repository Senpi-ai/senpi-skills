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
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import _cli    # noqa: E402
import close   # noqa: E402
import deploy  # noqa: E402
import status  # noqa: E402
from test_deploy_wrapper import (HEALTH_PLAIN, WALLET, _JournalDir, _ok, _proof_refused,  # noqa: E402
                                 _runtime_table, _status, _strategy)

KILL = (-9, "", _cli.killed_note(-9), False, None)
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


if __name__ == "__main__":
    unittest.main()
