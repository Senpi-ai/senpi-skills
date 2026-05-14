"""Integration tests for `senpi-helpers restart` — end-to-end argv-normalization.

Tests focus on the boundary between cli.cmd_restart and manage.relaunch_daemon
for legacy schema-1 boot.json files (the production bug).

Two test classes:

- `CmdRestartLegacyMigrationTests` (fast): mocks subprocess.Popen and the
  pid-confirmation wait. Runs in ~10 ms. Always enabled.

- `CmdRestartRealSubprocessTests` (slow, gated): spawns real subprocesses
  on a non-+x script to prove the bug's fix end-to-end. Skipped unless
  RUN_SLOW_INTEGRATION=1 is set in env so CI / normal test runs stay fast.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers import cli, manage, state as st  # noqa: E402


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_argparse_ns(name, state_dir, *, json_out=False,
                     timeout=5.0):
    """Minimal argparse.Namespace matching what `cmd_restart` consumes."""
    return argparse.Namespace(
        name=name,
        state_dir=state_dir,
        json=json_out,
        timeout=timeout,
        # `_resolve_name` may consult these; defaults are fine.
        list_known=False,
    )


def _write_legacy_boot(state_dir: str, name: str, script_path: str) -> None:
    """Drop a schema-1 boot.json (script-only argv) like a production daemon
    started via `nohup python3 -u <script> &` would have written."""
    daemon_dir = os.path.join(state_dir, name)
    os.makedirs(daemon_dir, exist_ok=True)
    payload = {
        "schema": 1,
        "name": name,
        "argv": [script_path],
        "script_path": script_path,
        "cwd": "/tmp",
        "env_snapshot": {},
        "captured_at_iso": "2026-05-12T22:11:16.470Z",
    }
    with open(os.path.join(daemon_dir, "boot.json"), "w") as f:
        json.dump(payload, f)


def _write_pid_json(state_dir: str, name: str, pid: int, log_path: str) -> None:
    daemon_dir = os.path.join(state_dir, name)
    os.makedirs(daemon_dir, exist_ok=True)
    payload = {
        "schema": 1, "name": name, "pid": pid,
        "start_time_iso": "2026-05-12T22:11:16.470Z",
        "wallet": "0xtest", "scanner": "test",
        "interval_seconds": 60.0, "tick_timeout": 60.0,
        "log_path": log_path, "version": "0.0.0-test",
    }
    with open(os.path.join(daemon_dir, "pid.json"), "w") as f:
        json.dump(payload, f)


# ─── R15: fast mocked path ──────────────────────────────────────────────────


class CmdRestartLegacyMigrationTests(unittest.TestCase):
    """End-to-end through `cmd_restart` with a legacy boot.json and a
    non-executable script. Verifies that Popen is invoked with the
    interpreter prepended, which is the entire point of this fix.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-restart-int-")
        self.addCleanup(self._cleanup)
        # Real non-+x .py file — argv[0] existence check needs it on disk.
        fd, self.script = tempfile.mkstemp(suffix=".py", prefix="legacy-fake-",
                                           dir=self.tmp)
        os.write(fd, b"# fake daemon, not executable\n")
        os.close(fd)
        os.chmod(self.script, 0o644)
        self.log = os.path.join(self.tmp, "fake.log")
        with open(self.log, "w") as fh:
            fh.write("")

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_boot_json_triggers_normalization(self) -> None:
        """R15: schema-1 boot.json + non-+x script → cmd_restart calls
        Popen with [sys.executable, "-u", script]."""
        _write_legacy_boot(self.tmp, "legacy-daemon", self.script)
        # Use this process's pid as the "old daemon" but neutralize stop_pid
        # so we don't actually kill ourselves.
        _write_pid_json(self.tmp, "legacy-daemon", os.getpid(), self.log)

        captured = {}

        class FakeProc:
            pid = 999_999

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProc()

        # Intercept all the side-effecting boundaries: don't really kill the
        # current process, don't actually spawn anything, don't wait for pid.json.
        orig_stop_pid = manage.stop_pid
        orig_is_pid_alive = manage.is_pid_alive
        orig_popen = subprocess.Popen
        orig_wait = cli._wait_for_new_pid_json
        try:
            manage.stop_pid = lambda *_a, **_kw: {
                "outcome": manage.STOP_TERM_OK,
                "elapsed_seconds": 0.0,
                "sigterm_sent": True, "sigkill_sent": False, "error": None,
            }
            manage.is_pid_alive = lambda _pid: True  # so cmd_restart calls stop
            subprocess.Popen = fake_popen
            cli._wait_for_new_pid_json = lambda *_a, **_kw: None

            ns = _make_argparse_ns("legacy-daemon", self.tmp)
            rc = cli.cmd_restart(ns)
        finally:
            manage.stop_pid = orig_stop_pid
            manage.is_pid_alive = orig_is_pid_alive
            subprocess.Popen = orig_popen
            cli._wait_for_new_pid_json = orig_wait

        self.assertEqual(rc, cli.RESTART_OK)
        self.assertEqual(
            captured["argv"],
            [sys.executable, "-u", self.script],
            "cmd_restart must invoke Popen with the interpreter prepended "
            "for a legacy schema-1 boot.json",
        )

    def test_modern_boot_json_no_normalization(self) -> None:
        """Schema-2 boot.json (interpreter already in argv) → Popen called
        with argv unchanged; argv_normalized=False."""
        daemon_dir = os.path.join(self.tmp, "modern-daemon")
        os.makedirs(daemon_dir, exist_ok=True)
        modern_argv = [sys.executable, "-u", self.script]
        with open(os.path.join(daemon_dir, "boot.json"), "w") as f:
            json.dump({
                "schema": 2, "name": "modern-daemon",
                "argv": modern_argv, "script_path": self.script,
                "cwd": "/tmp", "env_snapshot": {},
                "captured_at_iso": "2026-05-14T00:00:00.000Z",
            }, f)
        _write_pid_json(self.tmp, "modern-daemon", os.getpid(), self.log)

        captured = {}

        class FakeProc:
            pid = 12345

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            return FakeProc()

        orig_stop_pid = manage.stop_pid
        orig_is_pid_alive = manage.is_pid_alive
        orig_popen = subprocess.Popen
        orig_wait = cli._wait_for_new_pid_json
        try:
            manage.stop_pid = lambda *_a, **_kw: {
                "outcome": manage.STOP_TERM_OK, "elapsed_seconds": 0.0,
                "sigterm_sent": True, "sigkill_sent": False, "error": None,
            }
            manage.is_pid_alive = lambda _pid: True
            subprocess.Popen = fake_popen
            cli._wait_for_new_pid_json = lambda *_a, **_kw: None

            ns = _make_argparse_ns("modern-daemon", self.tmp)
            rc = cli.cmd_restart(ns)
        finally:
            manage.stop_pid = orig_stop_pid
            manage.is_pid_alive = orig_is_pid_alive
            subprocess.Popen = orig_popen
            cli._wait_for_new_pid_json = orig_wait

        self.assertEqual(rc, cli.RESTART_OK)
        self.assertEqual(captured["argv"], modern_argv)


# ─── R17: stop_pid never targets the helper's own pid ───────────────────────


class StopPidTargetingTests(unittest.TestCase):
    """`cmd_restart` must call `stop_pid` with the daemon's pid from
    pid.json, NEVER with the helper's own pid. This is the "could the
    restart kill itself?" check. The implementation reads pid_data["pid"];
    we verify that path by instrumenting stop_pid."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-targeting-")
        self.addCleanup(self._cleanup)
        fd, self.script = tempfile.mkstemp(suffix=".py", prefix="legacy-",
                                           dir=self.tmp)
        os.close(fd)
        self.log = os.path.join(self.tmp, "fake.log")
        with open(self.log, "w") as fh:
            fh.write("")
        _write_legacy_boot(self.tmp, "target-test", self.script)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stop_pid_called_with_daemon_pid_not_helper_pid(self) -> None:
        helper_pid = os.getpid()
        daemon_pid = helper_pid + 999_000  # synthetic; must differ from helper
        if daemon_pid == helper_pid:
            self.skipTest("could not pick a synthetic daemon pid distinct from os.getpid()")
        _write_pid_json(self.tmp, "target-test", daemon_pid, self.log)

        called_with = {}

        def fake_stop(pid, **_kw):
            called_with["pid"] = pid
            return {
                "outcome": manage.STOP_TERM_OK, "elapsed_seconds": 0.0,
                "sigterm_sent": True, "sigkill_sent": False, "error": None,
            }

        class FakeProc:
            pid = 1

        orig_stop_pid = manage.stop_pid
        orig_pid_alive_and_matches = st.pid_alive_and_matches
        orig_popen = subprocess.Popen
        orig_wait = cli._wait_for_new_pid_json
        try:
            manage.stop_pid = fake_stop
            # cli now uses _pid_alive_for_daemon → state.pid_alive_and_matches.
            # Force it to report alive for the daemon_pid (which is synthetic).
            st.pid_alive_and_matches = lambda pid, **_kw: pid == daemon_pid
            subprocess.Popen = lambda *_a, **_kw: FakeProc()
            cli._wait_for_new_pid_json = lambda *_a, **_kw: None

            ns = _make_argparse_ns("target-test", self.tmp)
            cli.cmd_restart(ns)
        finally:
            manage.stop_pid = orig_stop_pid
            st.pid_alive_and_matches = orig_pid_alive_and_matches
            subprocess.Popen = orig_popen
            cli._wait_for_new_pid_json = orig_wait

        self.assertEqual(called_with.get("pid"), daemon_pid,
                         "stop_pid must target the daemon's pid from pid.json")
        self.assertNotEqual(called_with.get("pid"), helper_pid,
                            "stop_pid must NEVER be called with the helper's own pid")


# ─── R16: real-subprocess gated test ────────────────────────────────────────


@unittest.skipUnless(
    os.environ.get("RUN_SLOW_INTEGRATION") == "1",
    "set RUN_SLOW_INTEGRATION=1 to run the real-subprocess integration test",
)
class CmdRestartRealSubprocessTests(unittest.TestCase):
    """Reproduces the production bug with real subprocesses. Slow (~3 s)
    and uses real signals — gated behind RUN_SLOW_INTEGRATION=1."""

    DAEMON_SCRIPT = """\
import os, sys, time
PID_PATH = os.environ["INT_PID_PATH"]
MARK_PATH = os.environ["INT_MARK_PATH"]
with open(PID_PATH, "w") as f:
    f.write(str(os.getpid()))
with open(MARK_PATH, "w") as f:
    f.write("STARTED\\n")
# Hold long enough that stop_pid sees us alive when the test calls
# cmd_restart. The test explicitly kills us in teardown.
time.sleep(60.0)
"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-real-int-")
        self.addCleanup(self._cleanup)
        # Real non-+x .py daemon script.
        self.script = os.path.join(self.tmp, "fake-producer.py")
        with open(self.script, "w") as fh:
            fh.write("#!/usr/bin/env python3\n")
            fh.write(self.DAEMON_SCRIPT)
        os.chmod(self.script, 0o644)
        self.log = os.path.join(self.tmp, "daemon.log")
        with open(self.log, "w") as fh:
            fh.write("")
        self.int_pid_file = os.path.join(self.tmp, "daemon.pid")
        self.mark_file = os.path.join(self.tmp, "started.mark")
        self._old_env = dict(os.environ)
        os.environ["INT_PID_PATH"] = self.int_pid_file
        os.environ["INT_MARK_PATH"] = self.mark_file
        self.children = []

    def _cleanup(self) -> None:
        import shutil
        # Reap any leftover children to keep CI quiet.
        for proc in self.children:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        os.environ.clear()
        os.environ.update(self._old_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_restart_cycle_on_non_executable_script(self) -> None:
        # 1) Launch the "old daemon" the way operators do: explicit interpreter.
        old = subprocess.Popen(
            [sys.executable, "-u", self.script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.children.append(old)
        # Drain the child in a background thread so when stop_pid kills it,
        # the kernel reaps the zombie immediately. Without this, is_pid_alive
        # would keep returning True on the zombie pid until tearDown ran.
        # Same pattern as test_manage.py::_drain_in_background.
        threading.Thread(
            target=lambda: old.wait(),
            daemon=True,
        ).start()
        # Wait for it to write its pid + mark file.
        for _ in range(50):
            if os.path.exists(self.int_pid_file) and os.path.exists(self.mark_file):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(self.int_pid_file),
                        "old daemon did not write its pid file in 2.5s")
        with open(self.int_pid_file) as fh:
            old_pid = int(fh.read().strip())
        self.assertTrue(manage.is_pid_alive(old_pid),
                        "old daemon pid not alive after launch")

        # 2) Write legacy schema-1 boot.json and a pid.json pointing at the
        #    real running daemon.
        _write_legacy_boot(self.tmp, "real-daemon", self.script)
        _write_pid_json(self.tmp, "real-daemon", old_pid, self.log)

        # 3) Restart via the CLI's real cmd_restart entry point.
        # Reset mark + pid files so we can detect the NEW daemon.
        for path in (self.int_pid_file, self.mark_file):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        ns = _make_argparse_ns("real-daemon", self.tmp, json_out=True,
                               timeout=5.0)
        rc = cli.cmd_restart(ns)
        self.assertEqual(rc, cli.RESTART_OK)

        # 4) Old daemon should be dead within a short window.
        for _ in range(50):
            if not manage.is_pid_alive(old_pid):
                break
            time.sleep(0.05)
        self.assertFalse(manage.is_pid_alive(old_pid),
                         "stop_pid failed to kill the old daemon")

        # 5) New daemon must have started (mark file appears) within timeout.
        new_pid = None
        for _ in range(100):
            if os.path.exists(self.int_pid_file):
                with open(self.int_pid_file) as fh:
                    new_pid = int(fh.read().strip())
                if new_pid and new_pid != old_pid:
                    break
            time.sleep(0.05)
        self.assertIsNotNone(new_pid,
                             "new daemon did not write its pid file — restart failed")
        self.assertNotEqual(new_pid, old_pid)
        self.assertTrue(manage.is_pid_alive(new_pid),
                        "new daemon pid is not alive post-restart")

        # 6) Teardown via the helper to be a good citizen.
        try:
            os.kill(new_pid, 15)
        except ProcessLookupError:
            pass


if __name__ == "__main__":
    unittest.main()
