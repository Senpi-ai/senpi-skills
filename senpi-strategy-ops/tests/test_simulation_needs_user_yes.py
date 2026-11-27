#!/usr/bin/env python3
"""A simulation starts only after the user's yes: `create|runtime --simulation` without `--user-said-yes`
starts nothing. An agent whose cost quote failed once started a two-wallet simulation the user never
approved, with the yes rule in its skill description.

    python3 -m pytest senpi-strategy-ops/tests/test_simulation_needs_user_yes.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli      # noqa: E402
import deploy    # noqa: E402


class SimulationNeedsTheUsersYes(unittest.TestCase):
    def setUp(self):
        self._orig = _cli.gateway_call
        self.calls = []
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append(method)
            return {"kind": "timeout", "payload": None, "message": None, "code": None, "rc": 0, "stderr": "",
                    "argv": [], "elapsed": 0.1, "transport": None}
        _cli.gateway_call = _gateway

    def tearDown(self):
        _cli.gateway_call = self._orig

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                deploy.main(["deploy.py", *argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue() + err.getvalue()

    def _assert_waits_for_the_yes(self, *argv):
        code, text = self._main(*argv)
        self.assertEqual(code, deploy.EXIT_CODES["refused"], argv)
        self.assertIn("[E_NEEDS_USER_YES] Nothing was started.", text)
        self.assertIn("end your turn", text)
        self.assertIn("--user-said-yes", text)
        self.assertEqual(self.calls, [], argv)

    def test_create_simulation_without_the_yes_starts_nothing(self):
        self._assert_waits_for_the_yes("create", "spider", "--budget", "1000", "--simulation")

    def test_a_local_directory_waits_for_the_yes_too(self):
        self._assert_waits_for_the_yes("create", "/data/workspace/strategies/spider", "--simulation")

    def test_a_variation_waits_for_the_yes(self):
        self._assert_waits_for_the_yes("create", "spider", "--budget", "1000", "--simulation",
                                       "--variant-of", "simulation-11111111-2222-4333-8444-555555555555",
                                       "--batch", "b-1")

    def test_runtime_simulation_without_the_yes_starts_nothing(self):
        self._assert_waits_for_the_yes("runtime", "spider", "--simulation")

    def test_the_yes_flag_passes(self):
        _code, text = self._main("create", "spider", "--budget", "1000", "--simulation", "--user-said-yes")
        self.assertNotIn("E_NEEDS_USER_YES", text)

    def test_a_dry_run_plans_without_the_yes(self):
        _code, text = self._main("create", "spider", "--budget", "1000", "--simulation", "--dry-run")
        self.assertNotIn("E_NEEDS_USER_YES", text)

    def test_a_live_create_is_not_gated_here(self):
        _code, text = self._main("create", "spider", "--budget", "1000")
        self.assertNotIn("E_NEEDS_USER_YES", text)


if __name__ == "__main__":
    unittest.main()
