#!/usr/bin/env python3
"""Hermetic tests for `scripts/run.sh`, the tester convenience wrapper.

No network, no git, no openclaw: run.sh is copied into a throwaway repo layout and run with a PATH
whose `git` is a no-op and whose `python3` is a stub exiting with the deploy code under test. Run:

    python3 senpi-strategy-ops/tests/test_run_sh.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN_SH = REPO / "senpi-strategy-ops" / "scripts" / "run.sh"


class RunShDeployOutcomes(unittest.TestCase):
    """`set -e` aborted run.sh the moment `deploy.py create` returned non-zero — including the two
    codes that mean the deploy went through: `4` installed-unobserved (the first tick lands after the
    tick-wait, routine on a 900s-interval package) and `6` pending (still running at the budget
    lapse). The tester lost the report and read a working branch as a broken one."""

    def _run(self, deploy_exit):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        scripts = tmp / "senpi-strategy-ops" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(RUN_SH, scripts / "run.sh")
        (tmp / "strategies" / "spider").mkdir(parents=True)
        stub_bin = tmp / "bin"
        stub_bin.mkdir()
        for name, body in (("git", "#!/bin/sh\nexit 0\n"),
                           ("python3", f"#!/bin/sh\necho 'deploy dpl-a1b2c3d4 — report'\n"
                                       f"exit {deploy_exit}\n")):
            stub = stub_bin / name
            stub.write_text(body)
            stub.chmod(0o755)
        env = dict(os.environ, PATH=f"{stub_bin}{os.pathsep}{os.environ['PATH']}")
        proc = subprocess.run(["bash", str(scripts / "run.sh"), "spider", "main", "$500"],
                              capture_output=True, text=True, env=env, timeout=60)
        return proc

    def test_a_live_deploy_reports_success(self):
        proc = self._run(0)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("deployed", proc.stdout)

    def test_installed_unobserved_is_reported_not_aborted(self):
        proc = self._run(4)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("deploy dpl-a1b2c3d4 — report", proc.stdout)   # the report survived
        self.assertIn("tick", proc.stdout.lower())                   # says what 4 actually means
        self.assertIn("openclaw senpi scanner", proc.stdout)         # …and how to check it
        self.assertNotIn("error", proc.stderr.lower())

    def test_a_still_running_job_is_reported_not_aborted(self):
        proc = self._run(6)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("still running", proc.stdout.lower())
        self.assertIn("openclaw senpi deploy status", proc.stdout)   # how to watch it
        self.assertNotIn("error", proc.stderr.lower())

    def test_a_refused_or_failed_deploy_aborts_with_its_own_code(self):
        for code in (1, 2, 3, 5):
            proc = self._run(code)
            self.assertEqual(proc.returncode, code, code)
            self.assertIn("did not complete", proc.stderr)
            self.assertNotIn("deployed", proc.stdout)                # never claims a live strategy

    def test_the_script_is_syntactically_valid(self):
        subprocess.run(["bash", "-n", str(RUN_SH)], check=True)


if __name__ == "__main__":
    unittest.main()
