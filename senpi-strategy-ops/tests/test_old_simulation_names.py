#!/usr/bin/env python3
"""What the names from before the simulation rename do now: nothing of their own.

`--paper` fails like any unknown option, before a call goes out. A `paper-<uuid>` id is not a
simulation id: no world lists it, so a close by that id finds nothing, closes nothing and points
at no flag.

    python3 -m pytest senpi-strategy-ops/tests/test_old_simulation_names.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli      # noqa: E402
import close     # noqa: E402
import deploy    # noqa: E402
import status    # noqa: E402
from test_simulation_world import FakeMCP, _CloseHarness, _live_row, _simulation_row  # noqa: E402

OLD_ID = "paper-11111111-2222-4333-8444-555555555555"


class OldFlagIsAnUnknownOption(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig = _cli.gateway_call, status.MCPClient
        _cli.gateway_call = lambda method, *a, **k: self.calls.append(method)
        status.MCPClient = lambda *a, **k: self.calls.append("MCPClient")

    def tearDown(self):
        _cli.gateway_call, status.MCPClient = self._orig

    def _refused(self, main, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(argv)
        return ctx.exception.code, err.getvalue()

    def test_deploy_create_and_runtime_refuse_it(self):
        for argv in (["deploy.py", "create", "omega", "--budget", "30", "--paper"],
                     ["deploy.py", "runtime", "omega", "--paper"]):
            code, err = self._refused(deploy.main, argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("unrecognized arguments: --paper", err)
        self.assertEqual(self.calls, [])

    def test_status_refuses_it(self):
        code, err = self._refused(status.main, ["status.py", "--paper"])
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments: --paper", err)
        self.assertEqual(self.calls, [])


class CloseRefusesTheOldFlag(_CloseHarness):
    def test_close_refuses_it_and_closes_nothing(self):
        self.mcp.live = [_live_row()]
        self.mcp.simulation = [_simulation_row()]
        for argv in (("omega", "--paper"), ("--all", "--paper")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    close.main(["close.py", *argv])
            self.assertEqual(ctx.exception.code, 2, argv)
            self.assertIn("unrecognized arguments: --paper", err.getvalue())
        self.assertEqual(self.mcp.lists, [])
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])


class AnOldIdIsNoSimulationId(_CloseHarness):
    def setUp(self):
        super().setUp()
        self.mcp.live = [_live_row()]
        self.mcp.simulation = [_simulation_row()]

    def test_a_close_by_it_finds_nothing_and_points_at_no_flag(self):
        out, code = self._run("--strategy-id", OLD_ID)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertNotIn("--simulation", out)

    def test_a_simulation_close_by_it_finds_nothing(self):
        out, code = self._run("--strategy-id", OLD_ID, "--simulation")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
