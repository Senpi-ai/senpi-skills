"""manage.py — process-control primitives (stop_pid, is_pid_alive)."""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers import manage


# Child scripts run via `python -c <script>`. We use subprocess instead of
# multiprocessing.Process because we need to control reaping precisely —
# multiprocessing keeps the child as a zombie until the parent joins, which
# breaks `os.kill(pid, 0)` based liveness checks used by stop_pid.

_CHILD_HANDLES_SIGTERM = """
import signal, sys, time
def _on_term(_s, _f): sys.exit(0)
signal.signal(signal.SIGTERM, _on_term)
while True: time.sleep(0.05)
"""

_CHILD_IGNORES_SIGTERM = """
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True: time.sleep(0.05)
"""

_CHILD_EXITS_IMMEDIATELY = "import sys; sys.exit(0)"


def _spawn(script: str) -> "subprocess.Popen":
    """Spawn a fresh Python subprocess running `script` and wait briefly for
    it to install any signal handlers. Caller must `_drain(proc)` once the
    test is done so the zombie is reaped before the next `is_pid_alive` check.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give the child a moment to register signal handlers before we signal it.
    time.sleep(0.2)
    return proc


def _drain_in_background(proc: "subprocess.Popen") -> None:
    """Start a daemon thread that wait()s on the child so the kernel reaps it
    AS SOON AS the child exits. Without this, stop_pid's os.kill(pid, 0)
    keeps returning 'alive' for the zombie until the test process reaps it
    via the implicit __del__ of Popen — which happens far too late."""
    def _waiter() -> None:
        try:
            proc.wait()
        except Exception:
            pass
    t = threading.Thread(target=_waiter, daemon=True)
    t.start()


# ─── Tests ──────────────────────────────────────────────────────────────────


class IsPidAliveTests(unittest.TestCase):
    def test_current_process_is_alive(self) -> None:
        self.assertTrue(manage.is_pid_alive(os.getpid()))

    def test_unrealistic_pid_is_not_alive(self) -> None:
        self.assertFalse(manage.is_pid_alive(2147483646))

    def test_none_pid_is_not_alive(self) -> None:
        self.assertFalse(manage.is_pid_alive(None))

    def test_negative_pid_is_not_alive(self) -> None:
        self.assertFalse(manage.is_pid_alive(-1))
        self.assertFalse(manage.is_pid_alive(0))

    def test_non_int_pid_is_not_alive(self) -> None:
        self.assertFalse(manage.is_pid_alive("12345"))  # type: ignore[arg-type]


class StopPidTests(unittest.TestCase):
    """End-to-end stop tests against real subprocesses.

    Each test spawns a Python subprocess and starts a background `wait()`
    thread (`_drain_in_background`). The background wait makes the kernel
    reap the child the moment it exits, so `os.kill(pid, 0)` returns
    ProcessLookupError immediately and stop_pid's liveness check works.
    """

    def setUp(self) -> None:
        self._procs = []

    def tearDown(self) -> None:
        # Belt-and-suspenders cleanup so a failed test doesn't leak procs.
        for p in self._procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
                    try:
                        p.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass

    def _spawn_and_track(self, script: str) -> int:
        proc = _spawn(script)
        self._procs.append(proc)
        _drain_in_background(proc)
        return proc.pid

    def test_clean_sigterm_exit(self) -> None:
        pid = self._spawn_and_track(_CHILD_HANDLES_SIGTERM)
        result = manage.stop_pid(pid, timeout_seconds=5.0, poll_interval=0.05)
        self.assertEqual(result["outcome"], manage.STOP_TERM_OK)
        self.assertTrue(result["sigterm_sent"])
        self.assertFalse(result["sigkill_sent"])
        self.assertLess(result["elapsed_seconds"], 5.0)

    def test_sigterm_ignored_escalates_to_sigkill(self) -> None:
        pid = self._spawn_and_track(_CHILD_IGNORES_SIGTERM)
        result = manage.stop_pid(pid, timeout_seconds=0.5, poll_interval=0.05)
        self.assertEqual(result["outcome"], manage.STOP_KILL_OK)
        self.assertTrue(result["sigterm_sent"])
        self.assertTrue(result["sigkill_sent"])
        # Elapsed time should reflect the SIGTERM timeout (≥ 0.5) plus
        # however long SIGKILL took to reap.
        self.assertGreaterEqual(result["elapsed_seconds"], 0.5)

    def test_already_dead_returns_quickly(self) -> None:
        proc = _spawn(_CHILD_EXITS_IMMEDIATELY)
        self._procs.append(proc)
        _drain_in_background(proc)
        # Give the child time to exit + the background drain time to reap.
        time.sleep(0.5)
        result = manage.stop_pid(proc.pid, timeout_seconds=5.0, poll_interval=0.05)
        self.assertEqual(result["outcome"], manage.STOP_ALREADY_DEAD)
        self.assertFalse(result["sigterm_sent"])
        self.assertFalse(result["sigkill_sent"])

    def test_stop_polls_returns_quickly_when_daemon_exits_fast(self) -> None:
        """The 5s timeout is a ceiling — a daemon that exits in 50ms should
        return in ~50-100ms, not 5s."""
        pid = self._spawn_and_track(_CHILD_HANDLES_SIGTERM)
        start = time.time()
        result = manage.stop_pid(pid, timeout_seconds=5.0, poll_interval=0.05)
        elapsed = time.time() - start
        self.assertEqual(result["outcome"], manage.STOP_TERM_OK)
        # Should be well under the 5s ceiling — clean exit is fast.
        self.assertLess(
            elapsed, 2.0,
            f"stop_pid took {elapsed:.2f}s for a clean exit; "
            f"poll loop is over-sleeping",
        )


class StopPidValidationTests(unittest.TestCase):
    def test_invalid_pid_negative(self) -> None:
        r = manage.stop_pid(-1)
        self.assertEqual(r["outcome"], manage.STOP_INVALID_PID)

    def test_invalid_pid_zero(self) -> None:
        r = manage.stop_pid(0)
        self.assertEqual(r["outcome"], manage.STOP_INVALID_PID)

    def test_invalid_pid_non_int(self) -> None:
        r = manage.stop_pid("not a pid")  # type: ignore[arg-type]
        self.assertEqual(r["outcome"], manage.STOP_INVALID_PID)

    def test_dead_unrealistic_pid_is_already_dead(self) -> None:
        r = manage.stop_pid(2147483646)
        self.assertEqual(r["outcome"], manage.STOP_ALREADY_DEAD)
        self.assertFalse(r["sigterm_sent"])


class RelaunchDaemonTests(unittest.TestCase):
    """relaunch_daemon — uses an injectable Popen factory so tests don't
    actually spawn long-running daemons."""

    def setUp(self) -> None:
        # Real script path on disk — relaunch_daemon checks existence.
        import tempfile
        fd, self.script = tempfile.mkstemp(suffix=".py", prefix="fake-script-")
        os.write(fd, b"# fake daemon\n")
        os.close(fd)
        fd2, self.log = tempfile.mkstemp(suffix=".log", prefix="fake-log-")
        os.close(fd2)

    def tearDown(self) -> None:
        for path in (self.script, self.log):
            try:
                os.unlink(path)
            except OSError:
                pass

    def _make_fake_popen(self, fake_pid: int = 12345):
        captured = {}
        class FakeProc:
            pid = fake_pid
        def factory(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProc()
        return factory, captured

    def test_relaunch_success_returns_pid(self) -> None:
        factory, captured = self._make_fake_popen(fake_pid=4242)
        result = manage.relaunch_daemon(
            argv=[self.script, "--flag"],
            cwd="/tmp",
            log_path=self.log,
            popen_factory=factory,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_OK)
        self.assertEqual(result["pid"], 4242)
        # Legacy schema-1 argv (script-only); _normalize_argv prepends the
        # interpreter so Popen doesn't need the script to be executable.
        self.assertEqual(
            captured["argv"],
            [sys.executable, "-u", self.script, "--flag"],
        )
        self.assertTrue(result["argv_normalized"])
        self.assertEqual(result["argv_used"], captured["argv"])
        # Detach must be on.
        self.assertTrue(captured["kwargs"].get("start_new_session"))
        # stdin must be muted.
        self.assertEqual(captured["kwargs"]["stdin"], subprocess.DEVNULL)

    def test_relaunch_passes_log_fd_for_both_stdout_and_stderr(self) -> None:
        factory, captured = self._make_fake_popen()
        manage.relaunch_daemon(
            argv=[self.script], cwd=None, log_path=self.log,
            popen_factory=factory,
        )
        # Same fd for stdout and stderr — both point at the log.
        self.assertEqual(captured["kwargs"]["stdout"], captured["kwargs"]["stderr"])

    def test_relaunch_missing_script_fails_loudly(self) -> None:
        factory, _ = self._make_fake_popen()
        result = manage.relaunch_daemon(
            argv=["/no/such/script.py"], cwd=None, log_path=self.log,
            popen_factory=factory,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_SCRIPT_MISSING)
        # Error mentions both that we tried + what we tried — exact wording
        # may evolve (today: "argv[0] is not a regular file on disk: …"),
        # so match the path rather than a brittle substring.
        self.assertIn("/no/such/script.py", result["error"])

    def test_relaunch_empty_argv_fails(self) -> None:
        factory, _ = self._make_fake_popen()
        result = manage.relaunch_daemon(
            argv=[], cwd=None, log_path=self.log, popen_factory=factory,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_SPAWN_FAILED)
        self.assertIn("argv", result["error"])

    def test_relaunch_unwritable_log_fails(self) -> None:
        factory, _ = self._make_fake_popen()
        result = manage.relaunch_daemon(
            argv=[self.script], cwd=None,
            log_path="/no/such/dir/file.log",
            popen_factory=factory,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_LOG_OPEN_FAILED)

    def test_relaunch_popen_exception_surfaced(self) -> None:
        def angry_factory(*_a, **_kw):
            raise OSError("simulated spawn failure")
        result = manage.relaunch_daemon(
            argv=[self.script], cwd=None, log_path=self.log,
            popen_factory=angry_factory,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_SPAWN_FAILED)
        self.assertIn("simulated spawn failure", result["error"])

    def test_relaunch_unexpected_exception_subclass_does_not_escape(self) -> None:
        """Function contract: every failure encoded in result dict.

        A TypeError (or any other non-OSError/ValueError) from popen_factory
        must NOT propagate out — `cmd_restart` doesn't wrap in try/except and
        would crash the CLI. Bugbot review #3226591637 caught the original
        narrower `except (OSError, ValueError)` letting these through.
        """
        def typeerror_factory(*_a, **_kw):
            raise TypeError("unexpected kwarg")
        result = manage.relaunch_daemon(
            argv=[self.script], cwd=None, log_path=self.log,
            popen_factory=typeerror_factory,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_SPAWN_FAILED)
        self.assertIn("unexpected kwarg", result["error"])

    def test_relaunch_log_fd_closed_exactly_once_on_spawn_failure(self) -> None:
        """The previous layout closed log_fd in BOTH the except branch and the
        finally — a double-close that Python tolerates but signals broken
        control flow. With the single-close-in-finally fix, the file is
        closed exactly once even when spawn fails.
        """
        import builtins
        close_calls = {"n": 0}
        real_open = builtins.open

        class _CountingFile:
            def __init__(self, real):
                self._real = real
            def __getattr__(self, name):
                return getattr(self._real, name)
            def close(self):
                close_calls["n"] += 1
                self._real.close()

        def counting_open(*args, **kwargs):
            return _CountingFile(real_open(*args, **kwargs))

        builtins.open = counting_open
        try:
            def angry_factory(*_a, **_kw):
                raise OSError("boom")
            manage.relaunch_daemon(
                argv=[self.script], cwd=None, log_path=self.log,
                popen_factory=angry_factory,
            )
        finally:
            builtins.open = real_open
        self.assertEqual(
            close_calls["n"], 1,
            f"log_fd.close() ran {close_calls['n']} times; expected exactly 1",
        )

    def test_relaunch_inherits_env_when_none_passed(self) -> None:
        factory, captured = self._make_fake_popen()
        os.environ["TEST_RELAUNCH_INHERIT_MARKER"] = "yes"
        try:
            manage.relaunch_daemon(
                argv=[self.script], cwd=None, log_path=self.log,
                popen_factory=factory,
            )
            self.assertEqual(
                captured["kwargs"]["env"].get("TEST_RELAUNCH_INHERIT_MARKER"),
                "yes",
                "relaunch must inherit current process env when env=None",
            )
        finally:
            os.environ.pop("TEST_RELAUNCH_INHERIT_MARKER", None)

    def test_relaunch_uses_explicit_env_when_passed(self) -> None:
        factory, captured = self._make_fake_popen()
        custom_env = {"FOO": "bar"}
        manage.relaunch_daemon(
            argv=[self.script], cwd=None, log_path=self.log,
            env=custom_env, popen_factory=factory,
        )
        self.assertEqual(captured["kwargs"]["env"], custom_env)


class StopOutcomeSuccessTests(unittest.TestCase):
    def test_already_dead_is_success(self) -> None:
        self.assertTrue(manage.stop_outcome_was_success(manage.STOP_ALREADY_DEAD))

    def test_term_is_success(self) -> None:
        self.assertTrue(manage.stop_outcome_was_success(manage.STOP_TERM_OK))

    def test_kill_is_success(self) -> None:
        self.assertTrue(manage.stop_outcome_was_success(manage.STOP_KILL_OK))

    def test_permission_denied_is_failure(self) -> None:
        self.assertFalse(manage.stop_outcome_was_success(manage.STOP_PERMISSION_DENIED))

    def test_kill_failed_is_failure(self) -> None:
        self.assertFalse(manage.stop_outcome_was_success(manage.STOP_KILL_FAILED))

    def test_invalid_pid_is_failure(self) -> None:
        self.assertFalse(manage.stop_outcome_was_success(manage.STOP_INVALID_PID))


# ─── Tests for _normalize_argv (R7-R12) ─────────────────────────────────────


class NormalizeArgvTests(unittest.TestCase):
    """_normalize_argv migrates schema-1 boot.json argv (script-only) to
    schema-2 (interpreter-first). Idempotent — modern argv passes through.
    """

    def test_passthrough_for_modern_shape(self) -> None:
        """R7: [interpreter, script] is left unchanged."""
        argv = ["/usr/bin/python3", "/path/script.py"]
        out, was_norm = manage._normalize_argv(argv)
        self.assertEqual(out, argv)
        self.assertFalse(was_norm)

    def test_passthrough_for_versioned_python(self) -> None:
        """R8: /usr/bin/python3.11 is recognized as an interpreter."""
        argv = ["/usr/bin/python3.11", "/path/script.py"]
        out, was_norm = manage._normalize_argv(argv)
        self.assertEqual(out, argv)
        self.assertFalse(was_norm)

    def test_prepends_for_legacy_script_only(self) -> None:
        """R9: ['script.py'] → [sys.executable, '-u', 'script.py'], was_norm=True."""
        argv = ["/path/script.py"]
        out, was_norm = manage._normalize_argv(argv)
        self.assertEqual(out, [sys.executable, "-u", "/path/script.py"])
        self.assertTrue(was_norm)

    def test_prepends_preserving_script_args(self) -> None:
        """R10: script's own arguments are preserved after normalization."""
        argv = ["/path/script.py", "--flag", "x"]
        out, was_norm = manage._normalize_argv(argv)
        self.assertEqual(
            out, [sys.executable, "-u", "/path/script.py", "--flag", "x"],
        )
        self.assertTrue(was_norm)

    def test_passthrough_for_unknown_non_py_binary(self) -> None:
        """R11: non-.py argv[0] is NOT 'fixed' — we don't second-guess."""
        argv = ["/usr/bin/cat", "/path/file"]
        out, was_norm = manage._normalize_argv(argv)
        self.assertEqual(out, argv)
        self.assertFalse(was_norm)

    def test_empty_argv(self) -> None:
        """R12: empty argv passes through; relaunch_daemon handles emptiness."""
        out, was_norm = manage._normalize_argv([])
        self.assertEqual(out, [])
        self.assertFalse(was_norm)

    def test_idempotent_when_applied_twice(self) -> None:
        """Defensive: normalizing an already-modern argv must not stack."""
        argv = ["/path/script.py"]
        once, _ = manage._normalize_argv(argv)
        twice, was_norm_again = manage._normalize_argv(once)
        self.assertEqual(twice, once)
        self.assertFalse(was_norm_again)


# ─── relaunch_daemon's normalization contract (R13-R14) ─────────────────────


class RelaunchNormalizationTests(unittest.TestCase):
    """relaunch_daemon must call Popen with the normalized argv, and surface
    `argv_normalized` + `argv_used` in its result dict so callers (CLI,
    automation) can see what was actually launched.
    """

    def setUp(self) -> None:
        import tempfile
        # Use a non-+x .py file — the original bug's smoking gun.
        fd, self.script = tempfile.mkstemp(suffix=".py", prefix="legacy-")
        os.write(fd, b"# fake daemon, intentionally not +x\n")
        os.close(fd)
        os.chmod(self.script, 0o644)
        fd2, self.log = tempfile.mkstemp(suffix=".log", prefix="legacy-log-")
        os.close(fd2)

    def tearDown(self) -> None:
        for path in (self.script, self.log):
            try:
                os.unlink(path)
            except OSError:
                pass

    def _make_fake_popen(self, fake_pid: int = 999):
        captured = {}
        class FakeProc:
            pid = fake_pid
        def factory(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProc()
        return factory, captured

    def test_relaunch_uses_normalized_argv_for_legacy_boot(self) -> None:
        """R13: legacy [script] → Popen called with [executable, "-u", script]."""
        factory, captured = self._make_fake_popen()
        result = manage.relaunch_daemon(
            argv=[self.script],
            cwd=None,
            log_path=self.log,
            popen_factory=factory,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_OK)
        self.assertTrue(result["argv_normalized"])
        self.assertEqual(
            captured["argv"], [sys.executable, "-u", self.script],
        )
        self.assertEqual(result["argv_used"], captured["argv"])

    def test_relaunch_records_argv_used_when_no_normalization(self) -> None:
        """R14: modern [executable, script] passes through, was_norm=False."""
        factory, captured = self._make_fake_popen()
        modern_argv = [sys.executable, self.script]
        result = manage.relaunch_daemon(
            argv=modern_argv,
            cwd=None,
            log_path=self.log,
            popen_factory=factory,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_OK)
        self.assertFalse(result["argv_normalized"])
        self.assertEqual(captured["argv"], modern_argv)
        self.assertEqual(result["argv_used"], modern_argv)

    def test_relaunch_empty_argv0_returns_script_missing(self) -> None:
        """Adversarial: argv = [""] (empty string).

        Before garg-prashant's cwd-resolution fix, `os.path.exists("")`
        returned False ⇒ RELAUNCH_SCRIPT_MISSING. My fix joined "" against
        cwd, which yields the cwd itself (a directory that exists). The
        existence check passed despite argv[0] being garbage. Now we
        require argv[0] to resolve to a FILE, not just a path that exists.

        Caught while doing the adversarial review of commit e0fd059."""
        factory, _ = self._make_fake_popen()
        result = manage.relaunch_daemon(
            argv=[""],
            cwd=self.tmp_cwd_for_tests if hasattr(self, "tmp_cwd_for_tests") else None,
            log_path=self.log,
            popen_factory=factory,
        )
        self.assertEqual(
            result["outcome"], manage.RELAUNCH_SCRIPT_MISSING,
            "empty argv[0] must fail the existence check, not pass via cwd",
        )

    def test_relaunch_argv0_pointing_at_a_directory_fails(self) -> None:
        """Adversarial: argv[0] = a directory path (a real path that
        exists, but isn't an executable file).

        `os.path.exists()` returns True for directories. Popen would fail
        on exec, but the helper's pre-check should refuse before Popen so
        the operator gets a clearer error."""
        import tempfile
        dir_path = tempfile.mkdtemp(prefix="argv0-is-dir-")
        try:
            factory, _ = self._make_fake_popen()
            result = manage.relaunch_daemon(
                argv=[dir_path], cwd=None, log_path=self.log,
                popen_factory=factory,
            )
            self.assertEqual(
                result["outcome"], manage.RELAUNCH_SCRIPT_MISSING,
                "argv[0] pointing at a directory must not pass the file check",
            )
        finally:
            import shutil
            shutil.rmtree(dir_path, ignore_errors=True)

    def test_relaunch_with_relative_argv0_resolves_against_cwd(self) -> None:
        """Schema-1 boot.json can record argv = ['./producer.py'] when the
        operator launched from the script's directory. Popen will use the
        daemon's cwd (`cwd=` parameter), so the existence check must too —
        otherwise we'd return RELAUNCH_SCRIPT_MISSING for a launch that
        would actually work.

        Caught by garg-prashant on PR #279.
        """
        # Put a fake script in a subdir so we can pass a relative argv[0]
        # against an explicit cwd.
        import tempfile
        sub = tempfile.mkdtemp(prefix="relaunch-relcwd-")
        script_name = "producer.py"
        with open(os.path.join(sub, script_name), "w") as f:
            f.write("# fake\n")
        factory, captured = self._make_fake_popen()
        try:
            result = manage.relaunch_daemon(
                argv=[f"./{script_name}"],
                cwd=sub,
                log_path=self.log,
                popen_factory=factory,
            )
            self.assertEqual(
                result["outcome"], manage.RELAUNCH_OK,
                f"expected OK, got: {result}",
            )
            # Popen receives the normalized argv (which still includes the
            # relative path — we don't rewrite it; only the existence check
            # was resolved against cwd).
            self.assertEqual(captured["kwargs"].get("cwd"), sub)
        finally:
            import shutil
            shutil.rmtree(sub, ignore_errors=True)

    def test_empty_argv_result_includes_normalization_fields(self) -> None:
        """Failure paths still populate argv_normalized / argv_used keys so
        callers can branch on them without KeyError."""
        result = manage.relaunch_daemon(
            argv=[], cwd=None, log_path=self.log,
        )
        self.assertEqual(result["outcome"], manage.RELAUNCH_SPAWN_FAILED)
        self.assertIn("argv_normalized", result)
        self.assertIn("argv_used", result)


if __name__ == "__main__":
    unittest.main()
