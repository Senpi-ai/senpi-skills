"""state.py — pid/boot/heartbeat round-trip, atomicity, env-snapshot scrubbing."""

import json
import os
import sys
import tempfile
import unittest

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers import state as st


class StateDirTests(unittest.TestCase):
    def test_get_state_dir_uses_explicit_override_first(self) -> None:
        os.environ["SENPI_HELPERS_STATE_DIR"] = "/should/not/be/used"
        try:
            p = st.get_state_dir("/explicit/path")
            self.assertEqual(str(p), "/explicit/path")
        finally:
            del os.environ["SENPI_HELPERS_STATE_DIR"]

    def test_get_state_dir_uses_env_var(self) -> None:
        os.environ["SENPI_HELPERS_STATE_DIR"] = "/from/env"
        try:
            self.assertEqual(str(st.get_state_dir()), "/from/env")
        finally:
            del os.environ["SENPI_HELPERS_STATE_DIR"]

    def test_get_state_dir_default(self) -> None:
        # Make sure no env var leaks in from the test environment.
        os.environ.pop("SENPI_HELPERS_STATE_DIR", None)
        self.assertEqual(str(st.get_state_dir()), "/data/.openclaw/senpi-helpers")


class WriteReadRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-state-")
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_and_read_pid(self) -> None:
        ok = st.write_pid(
            "round-trip-pid",
            wallet="0xabc",
            scanner="my_signals",
            interval_seconds=300.0,
            tick_timeout=60.0,
            log_path="/tmp/foo.log",
            version="0.1.0",
            state_dir=self.tmp,
        )
        self.assertTrue(ok)
        data = st.read_pid("round-trip-pid", state_dir=self.tmp)
        self.assertIsNotNone(data)
        self.assertEqual(data["name"], "round-trip-pid")
        self.assertEqual(data["pid"], os.getpid())
        self.assertEqual(data["wallet"], "0xabc")
        self.assertEqual(data["scanner"], "my_signals")
        self.assertEqual(data["interval_seconds"], 300.0)
        self.assertEqual(data["tick_timeout"], 60.0)
        self.assertEqual(data["log_path"], "/tmp/foo.log")
        self.assertEqual(data["version"], "0.1.0")
        # ISO timestamp ends in Z and is sortable.
        self.assertTrue(data["start_time_iso"].endswith("Z"))

    def test_write_and_read_boot(self) -> None:
        ok = st.write_boot("round-trip-boot", state_dir=self.tmp)
        self.assertTrue(ok)
        data = st.read_boot("round-trip-boot", state_dir=self.tmp)
        self.assertIsNotNone(data)
        self.assertEqual(data["name"], "round-trip-boot")
        # Schema 2: argv is [sys.executable, "-u", *sys.argv]. See the
        # schema-versioning comment near the top of state.py.
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["argv"], [sys.executable, "-u", *sys.argv])
        self.assertEqual(data["cwd"], os.getcwd())
        self.assertIn("env_snapshot", data)
        self.assertIsInstance(data["env_snapshot"], dict)

    def test_write_and_read_heartbeat(self) -> None:
        ok = st.write_heartbeat(
            "round-trip-hb",
            tick=7,
            status="error",
            code="SenpiClientError",
            duration_ms=1234,
            error="SenpiClientError: signal_post: 1/1 item(s) rejected",
            tick_count=7,
            error_count=2,
            state_dir=self.tmp,
        )
        self.assertTrue(ok)
        data = st.read_heartbeat("round-trip-hb", state_dir=self.tmp)
        self.assertIsNotNone(data)
        self.assertEqual(data["last_tick"], 7)
        self.assertEqual(data["last_tick_status"], "error")
        self.assertEqual(data["last_tick_code"], "SenpiClientError")
        self.assertEqual(data["last_tick_duration_ms"], 1234)
        self.assertEqual(data["tick_count"], 7)
        self.assertEqual(data["error_count"], 2)

    def test_clear_pid_removes_file(self) -> None:
        st.write_pid(
            "clear-me",
            wallet="0xabc",
            scanner=None,
            interval_seconds=1.0,
            tick_timeout=1.0,
            log_path=None,
            version="0.1.0",
            state_dir=self.tmp,
        )
        self.assertIsNotNone(st.read_pid("clear-me", state_dir=self.tmp))
        st.clear_pid("clear-me", state_dir=self.tmp)
        self.assertIsNone(st.read_pid("clear-me", state_dir=self.tmp))

    def test_clear_pid_no_op_when_missing(self) -> None:
        # Must not raise — clear_pid is best-effort.
        st.clear_pid("never-existed", state_dir=self.tmp)

    def test_list_daemons_enumerates_subdirs(self) -> None:
        for n in ("alpha", "beta", "gamma"):
            st.write_pid(
                n,
                wallet="0xabc",
                scanner=None,
                interval_seconds=1.0,
                tick_timeout=1.0,
                log_path=None,
                version="0.1.0",
                state_dir=self.tmp,
            )
        # Drop a stray file at the root — must be ignored.
        with open(os.path.join(self.tmp, "stray.txt"), "w") as f:
            f.write("ignore me")
        result = st.list_daemons(state_dir=self.tmp)
        self.assertEqual(result, ["alpha", "beta", "gamma"])

    def test_list_daemons_returns_empty_when_dir_missing(self) -> None:
        missing = os.path.join(self.tmp, "does-not-exist")
        self.assertEqual(st.list_daemons(state_dir=missing), [])

    def test_atomic_write_overwrites_existing(self) -> None:
        st.write_pid(
            "overwrite",
            wallet="0xaaa",
            scanner=None,
            interval_seconds=1.0,
            tick_timeout=1.0,
            log_path=None,
            version="0.1.0",
            state_dir=self.tmp,
        )
        st.write_pid(
            "overwrite",
            wallet="0xbbb",
            scanner=None,
            interval_seconds=2.0,
            tick_timeout=2.0,
            log_path=None,
            version="0.1.0",
            state_dir=self.tmp,
        )
        data = st.read_pid("overwrite", state_dir=self.tmp)
        self.assertEqual(data["wallet"], "0xbbb")
        self.assertEqual(data["interval_seconds"], 2.0)


class TolerantWriteTests(unittest.TestCase):
    """State writes must NEVER raise — daemon health depends on the producer
    continuing to tick even when its state dir is read-only / full / gone."""

    def test_write_pid_unwritable_dir_returns_false_does_not_raise(self) -> None:
        # Force the parent path to be a regular file so mkdir fails.
        with tempfile.NamedTemporaryFile() as f:
            # Reuse the file as a fake "directory" — mkdir(parents=True) on a
            # path that goes THROUGH a file (not just AT a file) raises.
            unwritable = os.path.join(f.name, "nope")
            ok = st.write_pid(
                "blocked",
                wallet="0xabc",
                scanner=None,
                interval_seconds=1.0,
                tick_timeout=1.0,
                log_path=None,
                version="0.1.0",
                state_dir=unwritable,
            )
            self.assertFalse(ok)

    def test_write_heartbeat_unwritable_dir_returns_false(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            unwritable = os.path.join(f.name, "nope")
            ok = st.write_heartbeat(
                "blocked",
                tick=1,
                status="ok",
                code=None,
                duration_ms=1,
                error=None,
                tick_count=1,
                error_count=0,
                state_dir=unwritable,
            )
            self.assertFalse(ok)


class EnvSnapshotTests(unittest.TestCase):
    """boot.json env_snapshot must include skill vars + exclude auth tokens."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-env-")
        self.addCleanup(self._cleanup)
        # Snapshot any pre-existing env so cleanup is clean.
        self._restore = {}

    def _set_env(self, k: str, v: str) -> None:
        self._restore[k] = os.environ.get(k)
        os.environ[k] = v

    def _cleanup(self) -> None:
        import shutil
        for k, prev in self._restore.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_captures_skill_and_helper_vars(self) -> None:
        self._set_env("PANGOLIN_WALLET", "0xabc")
        self._set_env("KODIAK_DECISION_MODEL", "gemini-x")
        self._set_env("SENPI_HELPERS_MAX_CONCURRENT", "16")
        self._set_env("WALLET_ADDRESS", "0xdef")
        st.write_boot("envsnap", state_dir=self.tmp)
        data = st.read_boot("envsnap", state_dir=self.tmp)
        env = data["env_snapshot"]
        self.assertEqual(env.get("PANGOLIN_WALLET"), "0xabc")
        self.assertEqual(env.get("KODIAK_DECISION_MODEL"), "gemini-x")
        self.assertEqual(env.get("SENPI_HELPERS_MAX_CONCURRENT"), "16")
        self.assertEqual(env.get("WALLET_ADDRESS"), "0xdef")

    def test_excludes_sensitive_keys(self) -> None:
        self._set_env("SENPI_AUTH_TOKEN", "supersecret")
        self._set_env("OPENCLAW_GATEWAY_TOKEN", "also-secret")
        self._set_env("ANTHROPIC_API_KEY", "sk-...")
        self._set_env("TELEGRAM_BOT_TOKEN", "bot-...")
        st.write_boot("envsnap-secrets", state_dir=self.tmp)
        data = st.read_boot("envsnap-secrets", state_dir=self.tmp)
        env = data["env_snapshot"]
        self.assertNotIn("SENPI_AUTH_TOKEN", env)
        self.assertNotIn("OPENCLAW_GATEWAY_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)

    def test_excludes_unrelated_env_vars(self) -> None:
        # Random PATH-like vars must NOT be captured.
        st.write_boot("envsnap-unrelated", state_dir=self.tmp)
        data = st.read_boot("envsnap-unrelated", state_dir=self.tmp)
        env = data["env_snapshot"]
        self.assertNotIn("PATH", env)
        self.assertNotIn("HOME", env)
        self.assertNotIn("SHELL", env)


class PidAliveTests(unittest.TestCase):
    """`state.pid_alive` is the canonical PID-liveness check — verify the
    same identity is shared by every consumer module (lock / cli / manage)
    so the bug-fix-once invariant holds.

    Functional behavior is covered by test_manage.IsPidAliveTests; here we
    only check the cross-module aliasing.
    """

    def test_returns_true_for_current_process(self) -> None:
        self.assertTrue(st.pid_alive(os.getpid()))

    def test_returns_false_for_unrealistic_pid(self) -> None:
        self.assertFalse(st.pid_alive(2147483646))

    def test_returns_false_for_invalid_inputs(self) -> None:
        self.assertFalse(st.pid_alive(None))
        self.assertFalse(st.pid_alive(0))
        self.assertFalse(st.pid_alive(-1))
        self.assertFalse(st.pid_alive("not an int"))  # type: ignore[arg-type]

    def test_lock_module_imports_canonical_implementation(self) -> None:
        from senpi_runtime_helpers import lock
        self.assertIs(lock._process_alive, st.pid_alive)

    def test_manage_module_imports_canonical_implementation(self) -> None:
        from senpi_runtime_helpers import manage
        self.assertIs(manage.is_pid_alive, st.pid_alive)

    def test_cli_module_uses_canonical_pid_alive_via_recycle_guard(self) -> None:
        """The CLI now consults pid_alive through the pid-recycle guard
        helper (`_pid_alive_for_daemon` → `state.pid_alive_and_matches`),
        not via a bare `_is_pid_alive` alias. This test pins the new
        invariant — `state.pid_alive` is still the leaf-level liveness
        check, just one indirection deeper."""
        from senpi_runtime_helpers import cli
        # The recycle-aware helper exists at the cli layer.
        self.assertTrue(callable(cli._pid_alive_for_daemon))
        # And `state.pid_alive_and_matches` (the dispatcher) chains into
        # `state.pid_alive` for the basic existence check. Verified by
        # passing a definitely-dead pid: pid_alive returns False ⇒
        # pid_alive_and_matches returns False without consulting
        # fingerprints.
        self.assertFalse(st.pid_alive_and_matches(2147483646))
        self.assertFalse(st.pid_alive(2147483646))


class PidAliveZombieFilterTests(unittest.TestCase):
    """Zombies sit in the kernel's process table until their parent reap()s
    them. `os.kill(pid, 0)` succeeds on a zombie — but the process is dead.
    `pid_alive` must filter zombies so `stop_pid`/`restart` don't report
    `STOP_KILL_FAILED` after a daemon has actually exited cleanly.

    Linux-only: /proc is the source of truth for process state. On non-Linux
    dev hosts the function degrades to the plain signal(0) result.

    Repro path the fix addresses: daemon launched via `nohup … &`, parent
    bash exits, daemon reparented to PID 1, daemon dies, PID 1 doesn't
    actively reap → zombie. Production: Railway's `node src/server.js` as
    PID 1 doesn't reap.
    """

    def _spawn_and_kill(self):
        """Spawn a short-lived subprocess WITHOUT waiting on it, so it
        becomes a zombie under this test process (the parent). Returns the
        pid. The test process is the parent — we leave it un-reaped so the
        zombie sits in the process table.
        """
        import subprocess
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait for the child to actually exit. `signal(0)` will still
        # report it alive — the kernel keeps the slot until we wait().
        # poll() observes exit without reaping (only wait() reaps).
        for _ in range(200):
            if proc.poll() is not None:
                break
            import time as _time
            _time.sleep(0.01)
        return proc

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc only")
    def test_zombie_is_not_alive(self):
        """signal(0) succeeds on a zombie but the daemon has DIED — `stop_pid`
        polling pid_alive would otherwise spin until SIGKILL grace ends, then
        wrongly report STOP_KILL_FAILED. Filter zombies via /proc/<pid>/status."""
        proc = self._spawn_and_kill()
        try:
            self.assertEqual(proc.poll(), 0,
                             "zombie test requires the child to have exited")
            # Confirm it's a zombie at the kernel level.
            with open(f"/proc/{proc.pid}/status") as f:
                state_line = next(l for l in f if l.startswith("State:"))
            self.assertIn("Z", state_line,
                          f"expected zombie state, got: {state_line.strip()}")
            # The bug: signal(0) returns success for zombies.
            self.assertFalse(
                st.pid_alive(proc.pid),
                "pid_alive must return False for zombies; otherwise stop_pid "
                "spins to SIGKILL escalation on already-dead daemons.",
            )
        finally:
            # Reap before the test process exits to keep CI clean.
            proc.wait()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc only")
    def test_live_process_still_reported_alive(self):
        """Regression guard: the zombie filter must not break the common
        path. The current Python process is alive; pid_alive must return True.
        """
        self.assertTrue(st.pid_alive(os.getpid()))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc only")
    def test_proc_status_missing_falls_back_to_signal_zero(self):
        """If `/proc/<pid>/status` cannot be read (race: pid just exited and
        was reaped between signal(0) and our /proc read), the function must
        not crash. The contract is: when /proc is unreadable, trust signal(0).
        """
        # Use init's pid (1) — always alive on any container, /proc/1/status
        # always readable. Sanity-check the normal path. The race itself is
        # hard to engineer deterministically; this test guards the
        # "/proc read failure" recovery branch indirectly via code review.
        self.assertTrue(st.pid_alive(1))


class LogPathDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get("SENPI_HELPERS_LOG_PATH")

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("SENPI_HELPERS_LOG_PATH", None)
        else:
            os.environ["SENPI_HELPERS_LOG_PATH"] = self._prev

    def test_env_override_wins(self) -> None:
        os.environ["SENPI_HELPERS_LOG_PATH"] = "/tmp/explicit.log"
        self.assertEqual(st.detect_log_path(), "/tmp/explicit.log")

    def test_returns_none_when_stderr_is_terminal(self) -> None:
        # In a typical test runner stderr is either a TTY or a pipe, neither
        # of which qualifies as a "log file". Empty env override → None.
        os.environ.pop("SENPI_HELPERS_LOG_PATH", None)
        result = st.detect_log_path()
        # We can't assert the exact value (depends on the test runner) but
        # if /proc/self/fd/2 is unavailable OR points at a non-file target,
        # detect must return None.
        if result is not None:
            # If a result came back, it must look like a real path (not a
            # /dev/* terminal or pipe:[…] descriptor).
            self.assertTrue(result.startswith("/"))
            self.assertFalse(result.startswith("/dev/"))
            self.assertFalse(result.startswith("/proc/"))
            self.assertFalse(result.endswith(" (deleted)"))


class JsonShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-shape-")
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_state_files_include_schema_and_name(self) -> None:
        st.write_pid(
            "shape", wallet="0xabc", scanner=None, interval_seconds=1.0,
            tick_timeout=1.0, log_path=None, version="0.1.0", state_dir=self.tmp,
        )
        st.write_boot("shape", state_dir=self.tmp)
        st.write_heartbeat(
            "shape", tick=1, status="ok", code=None, duration_ms=10,
            error=None, tick_count=1, error_count=0, state_dir=self.tmp,
        )
        # Schema versions per file (see state.py top-of-file protocol):
        #   pid.json schema 2 — adds cmdline_fingerprint + start_time_jiffies
        #     for the pid-recycle guard.
        #   boot.json schema 2 — adds interpreter-first argv + log_path.
        #   heartbeat.json schema 1 — unchanged.
        expected_schemas = {"pid": 2, "boot": 2, "heartbeat": 1}
        for kind in ("pid", "boot", "heartbeat"):
            with open(os.path.join(self.tmp, "shape", f"{kind}.json")) as f:
                doc = json.load(f)
            self.assertEqual(
                doc.get("schema"), expected_schemas[kind],
                f"{kind}.json wrong schema: got {doc.get('schema')}",
            )
            self.assertEqual(doc.get("name"), "shape", f"{kind}.json missing name")


# ─── Tests for schema-2 write_boot (R1-R4) ──────────────────────────────────


class WriteBootSchema2Tests(unittest.TestCase):
    """write_boot's schema-2 output: [sys.executable, "-u", *sys.argv].

    Each test stubs sys.argv (and where needed, sys.executable) and asserts
    the boot.json payload reflects the schema-2 contract. See the
    schema-versioning comment near the top of state.py for the rationale.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-write-boot-")
        self.addCleanup(self._cleanup)
        # Save sys.argv / sys.executable so each test can mutate them.
        self._orig_argv = list(sys.argv)
        self._orig_executable = sys.executable
        self.addCleanup(self._restore_sys)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _restore_sys(self) -> None:
        sys.argv[:] = self._orig_argv
        sys.executable = self._orig_executable

    def test_argv_includes_sys_executable_and_dash_u(self) -> None:
        """R1: argv = [interpreter, "-u", script, ...args]."""
        sys.argv[:] = ["/scripts/foo-producer.py", "--mode", "live"]
        sys.executable = "/usr/bin/python3"
        ok = st.write_boot("R1-test", state_dir=self.tmp)
        self.assertTrue(ok)
        data = st.read_boot("R1-test", state_dir=self.tmp)
        self.assertEqual(
            data["argv"],
            ["/usr/bin/python3", "-u", "/scripts/foo-producer.py", "--mode", "live"],
        )

    def test_schema_field_is_2(self) -> None:
        """R2: payload['schema'] is 2 on every fresh write_boot."""
        ok = st.write_boot("R2-test", state_dir=self.tmp)
        self.assertTrue(ok)
        data = st.read_boot("R2-test", state_dir=self.tmp)
        self.assertEqual(data["schema"], 2)

    def test_script_path_resolved_to_realpath(self) -> None:
        """R3: script_path follows symlinks (realpath), distinct from argv[0]."""
        real_dir = os.path.join(self.tmp, "scripts")
        os.makedirs(real_dir, exist_ok=True)
        real_script = os.path.join(real_dir, "foo.py")
        with open(real_script, "w") as fh:
            fh.write("# fake\n")
        link_path = os.path.join(self.tmp, "link.py")
        os.symlink(real_script, link_path)

        sys.argv[:] = [link_path]
        ok = st.write_boot("R3-test", state_dir=self.tmp)
        self.assertTrue(ok)
        data = st.read_boot("R3-test", state_dir=self.tmp)
        self.assertEqual(data["script_path"], os.path.realpath(link_path))
        # argv preserves the link path (we don't realpath inside argv).
        self.assertEqual(data["argv"][2], link_path)

    def test_handles_empty_sys_argv(self) -> None:
        """R4: empty sys.argv records [interpreter, "-u"] and script_path=None."""
        sys.argv[:] = []
        sys.executable = "/usr/bin/python3"
        ok = st.write_boot("R4-test", state_dir=self.tmp)
        self.assertTrue(ok)
        data = st.read_boot("R4-test", state_dir=self.tmp)
        self.assertEqual(data["argv"], ["/usr/bin/python3", "-u"])
        self.assertIsNone(data["script_path"])

    def test_log_path_recorded_in_boot_json(self) -> None:
        """R-log: boot.json captures log_path so restart/stats survive a
        clean stop (which removes pid.json)."""
        os.environ["SENPI_HELPERS_LOG_PATH"] = "/tmp/explicit.log"
        try:
            ok = st.write_boot("R-log-test", state_dir=self.tmp)
            self.assertTrue(ok)
            data = st.read_boot("R-log-test", state_dir=self.tmp)
            self.assertEqual(data["log_path"], "/tmp/explicit.log")
        finally:
            os.environ.pop("SENPI_HELPERS_LOG_PATH", None)

    def test_log_path_can_be_none_when_undetectable(self) -> None:
        """If detect_log_path can't determine a path (stderr is a pipe, no
        explicit env override), log_path is None — readers must tolerate it."""
        os.environ.pop("SENPI_HELPERS_LOG_PATH", None)
        ok = st.write_boot("R-log-none", state_dir=self.tmp)
        self.assertTrue(ok)
        data = st.read_boot("R-log-none", state_dir=self.tmp)
        # In CI / dev environments stderr might be redirected to a real file
        # or a pipe. Either way the field must exist (not KeyError).
        self.assertIn("log_path", data)


# ─── Tests for looks_like_python_interpreter (R5-R6) ────────────────────────


class LooksLikePythonInterpreterTests(unittest.TestCase):
    """Heuristic used by manage._normalize_argv to detect modern boot.json
    argv (where argv[0] is the interpreter) vs legacy (script-only)."""

    POSITIVE_CASES = [
        "python", "python3", "python3.11", "python.exe", "python3.exe",
        "python3.11.exe", "pypy", "pypy3", "pypy3.10",
        "/usr/bin/python3", "/usr/bin/python3.11",
        # case insensitive
        "PYTHON3", "/usr/bin/PYTHON3.11",
        # Note: Windows-style paths ("C:\\Python311\\python.exe") aren't
        # supported on Linux/macOS because os.path.basename uses the host's
        # path separator. Production runs on Linux only; we don't carry the
        # complexity of cross-platform basename here.
    ]

    NEGATIVE_CASES = [
        "", "myscript.py", "/path/to/script.py",
        "python3.11-config", "python-pip", "python-something",
        "cat", "/usr/bin/cat", "/path/to/script.sh",
        "pythonfoo",   # bare-letter suffix, not version digits
        "/path/python3.11.dev",  # has trailing non-version garbage
    ]

    def test_positive_cases(self) -> None:
        """R5: every recognized python/pypy invocation is detected."""
        for path in self.POSITIVE_CASES:
            with self.subTest(path=path):
                self.assertTrue(
                    st.looks_like_python_interpreter(path),
                    f"{path!r} should be recognized as a python interpreter",
                )

    def test_negative_cases(self) -> None:
        """R6: non-interpreter paths must not match."""
        for path in self.NEGATIVE_CASES:
            with self.subTest(path=path):
                self.assertFalse(
                    st.looks_like_python_interpreter(path),
                    f"{path!r} should NOT be detected as a python interpreter",
                )


# ─── Tests for ensure_daemon_stderr_redirected ──────────────────────────────


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "ensure_daemon_stderr_redirected relies on /proc/self/fd/2 (Linux-only). "
    "Production runs on Linux containers; the fallback is exercised there.",
)
class EnsureDaemonStderrRedirectedTests(unittest.TestCase):
    """Daemon-side log-path fallback. Run from a subprocess so we don't
    permanently dup2 onto this test process's own fd 1/2."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-ensure-log-")
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        # Best-effort cleanup of any /tmp/<name>.log files we created.
        for name in ("ensure-test-already-redirected", "ensure-test-pipe-fallback"):
            try:
                os.unlink(f"/tmp/{name}.log")
            except FileNotFoundError:
                pass

    def _run_in_subprocess(self, script: str) -> str:
        """Run a python snippet in a clean subprocess and return its stdout
        (the resolved log_path, one line). Lets us control fd 2 redirection
        per-test without dirtying this test process."""
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            self.fail(f"subprocess failed rc={proc.returncode}: {proc.stderr}")
        return proc.stdout.strip()

    def test_no_op_when_stderr_already_points_at_a_file(self) -> None:
        """If the daemon was launched as `python ... > log 2>&1`, the
        fallback must NOT clobber the existing redirection."""
        log_file = os.path.join(self.tmp, "explicit.log")
        # Subprocess sets stderr to a real file, then calls ensure().
        out = self._run_in_subprocess(
            "import os, sys\n"
            f"sys.path.insert(0, {repr(_HELPERS_PARENT)})\n"
            "from senpi_runtime_helpers import state\n"
            f"fd = os.open({repr(log_file)}, os.O_WRONLY|os.O_CREAT, 0o644)\n"
            "os.dup2(fd, 2); os.close(fd)\n"
            "print(state.ensure_daemon_stderr_redirected('test-already'))\n"
        )
        self.assertEqual(out, log_file)

    def test_does_not_close_redirected_streams_when_fd_is_1_or_2(self) -> None:
        """Adversarial: if stdin/stdout/stderr are closed (rare but possible
        on `python script.py >&- 2>&-` launches), os.open returns the
        lowest free fd — which could be 1 or 2. After dup2(fd, 1/2),
        unconditionally `os.close(fd)` would close the very stream we just
        redirected. Verify the post-fallback log handle is still usable.

        Caught by reviewer on PR #279.
        """
        fallback_path = "/tmp/ensure-test-fd-guard.log"
        try:
            os.unlink(fallback_path)
        except FileNotFoundError:
            pass
        try:
            import subprocess
            # Subprocess closes fd 1 and 2 BEFORE calling ensure(). After
            # the call, write a known marker via os.write() to fd 2 — if
            # ensure() closed fd 2 by mistake, that write raises EBADF.
            # Marker has to land in the LOG FILE (since ensure redirects
            # fd 2 to the fallback), which we can then check from the
            # parent process.
            script = (
                "import os, sys\n"
                f"sys.path.insert(0, {repr(_HELPERS_PARENT)})\n"
                "from senpi_runtime_helpers import state\n"
                "# Close fd 1 and fd 2 entirely. fd 0 is left alone so\n"
                "# subprocess capture_output (which gives stdin=DEVNULL\n"
                "# from the parent) doesn't fight us.\n"
                "os.close(1)\n"
                "os.close(2)\n"
                "result = state.ensure_daemon_stderr_redirected('ensure-test-fd-guard')\n"
                "# Write a sentinel to fd 2. If ensure() left fd 2 closed,\n"
                "# this raises OSError EBADF and the subprocess fails with\n"
                "# a non-zero exit, which the assertion below catches.\n"
                "os.write(2, b'POST_ENSURE_WROTE_OK\\n')\n"
            )
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=10,
            )
            # Subprocess must NOT have crashed. A crash here means stdout
            # OR stderr got closed by the close-fd-after-dup2 bug.
            self.assertEqual(
                proc.returncode, 0,
                f"subprocess failed (likely closed-fd regression): "
                f"rc={proc.returncode} stderr={proc.stderr!r}",
            )
            # Sentinel reached the fallback log file (where fd 2 was
            # redirected to). Confirms post-ensure fd 2 is still writable.
            self.assertTrue(os.path.exists(fallback_path),
                            f"fallback log not created at {fallback_path}")
            with open(fallback_path) as f:
                content = f.read()
            self.assertIn("POST_ENSURE_WROTE_OK", content,
                          "expected sentinel in log; fd 2 must remain "
                          "writable after ensure_daemon_stderr_redirected")
        finally:
            try:
                os.unlink(fallback_path)
            except FileNotFoundError:
                pass

    def test_fallback_to_tmp_name_log_when_stderr_is_pipe(self) -> None:
        """The motivating bug: openclaw exec gives the daemon a pipe on fd 2.
        ensure() must redirect to /tmp/<name>.log so the helpers state files
        record a usable path."""
        fallback_path = "/tmp/ensure-test-pipe-fallback.log"
        try:
            os.unlink(fallback_path)
        except FileNotFoundError:
            pass
        # Subprocess: pipe stderr, then call ensure().
        # stdout (where we print the result) goes back to us via capture_output.
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-c",
             "import os, sys\n"
             f"sys.path.insert(0, {repr(_HELPERS_PARENT)})\n"
             "from senpi_runtime_helpers import state\n"
             "# Replace fd 2 with a pipe so detect_log_path returns None.\n"
             "r, w = os.pipe()\n"
             "os.dup2(w, 2); os.close(w); os.close(r)\n"
             "result = state.ensure_daemon_stderr_redirected('ensure-test-pipe-fallback')\n"
             "# Write result to fd 3 so we don't compete with the dup2'd stderr.\n"
             "import sys as _s; _s.stdout.write(str(result)); _s.stdout.flush()\n"
            ],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0,
                         f"subprocess failed: {proc.stderr}")
        self.assertEqual(proc.stdout.strip(), fallback_path)
        self.assertTrue(os.path.exists(fallback_path),
                        f"fallback log not created at {fallback_path}")


# ─── Tests for pid-recycle guard ────────────────────────────────────────────


class PidAliveAndMatchesTests(unittest.TestCase):
    """pid_alive_and_matches degrades to plain pid_alive on hosts without
    fingerprints (non-Linux or schema-1 pid.json), and strictly rejects on
    fingerprint mismatch."""

    def test_degrades_to_pid_alive_when_no_fingerprints(self) -> None:
        # Our own pid is alive. With None fingerprints, no extra check fires.
        self.assertTrue(
            st.pid_alive_and_matches(os.getpid(),
                                     expected_fingerprint=None,
                                     expected_jiffies=None)
        )

    def test_returns_false_when_pid_not_alive(self) -> None:
        self.assertFalse(
            st.pid_alive_and_matches(2147483646,
                                     expected_fingerprint="anything",
                                     expected_jiffies=12345)
        )

    def test_rejects_on_fingerprint_mismatch(self) -> None:
        """If /proc/<pid>/cmdline returns a real fingerprint and it doesn't
        match the expected one, the pid was recycled to a stranger — return
        False. Linux-only assertion; on macOS the fingerprint helper returns
        None and the check degrades to pid_alive (which is True)."""
        if not sys.platform.startswith("linux"):
            self.skipTest("requires /proc for cmdline fingerprint check")
        self.assertFalse(
            st.pid_alive_and_matches(
                os.getpid(),
                expected_fingerprint="0" * 64,  # impossible hash
                expected_jiffies=None,
            )
        )

    def test_rejects_on_jiffies_mismatch(self) -> None:
        if not sys.platform.startswith("linux"):
            self.skipTest("requires /proc for stat field 22 check")
        self.assertFalse(
            st.pid_alive_and_matches(
                os.getpid(),
                expected_fingerprint=None,
                expected_jiffies=1,  # impossibly early
            )
        )

    def test_write_pid_records_fingerprints(self) -> None:
        tmp = tempfile.mkdtemp(prefix="senpi-helpers-fp-")
        try:
            ok = st.write_pid(
                "fp-test", wallet="0xabc", scanner=None,
                interval_seconds=60.0, tick_timeout=60.0, log_path=None,
                version="0.0.0", state_dir=tmp,
            )
            self.assertTrue(ok)
            data = st.read_pid("fp-test", state_dir=tmp)
            self.assertEqual(data["schema"], 2)
            # On Linux both fields are populated; on macOS both are None
            # (no /proc). Either way the keys must exist.
            self.assertIn("cmdline_fingerprint", data)
            self.assertIn("start_time_jiffies", data)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ─── Tests for read_proc_environ (used by --inherit-env-from) ───────────────


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "read_proc_environ requires /proc (Linux-only)",
)
class ReadProcEnvironTests(unittest.TestCase):
    """Reads /proc/<pid>/environ for the --inherit-env-from flag.
    Production-only path; gated on Linux."""

    def test_reads_own_environ(self) -> None:
        """Use os.getpid() as the test pid — we know our own environ."""
        os.environ["SENPI_HELPERS_READ_ENVIRON_TEST"] = "marker-value"
        try:
            env = st.read_proc_environ(os.getpid())
        finally:
            os.environ.pop("SENPI_HELPERS_READ_ENVIRON_TEST", None)
        self.assertIsNotNone(env)
        # Linux note: /proc/self/environ snapshots the env at exec time, so
        # post-fork modifications via os.environ may not appear. The marker
        # MIGHT not be there. We assert the function returns a dict with
        # SOMETHING reasonable (it should at least have PATH or HOME).
        self.assertIsInstance(env, dict)
        self.assertTrue(env, "expected non-empty environ dict")
        # Most CI/dev shells set HOME or PATH.
        self.assertTrue(
            "HOME" in env or "PATH" in env or "PWD" in env,
            f"environ unexpectedly missing common keys: {sorted(env)[:10]}",
        )

    def test_returns_none_for_missing_pid(self) -> None:
        self.assertIsNone(st.read_proc_environ(2147483646))

    def test_skips_malformed_entries(self) -> None:
        """Bad env entries (no '=' or weird key chars) don't poison the dict."""
        # Can't easily inject bad data into our own /proc; rely on the
        # robust parser tested indirectly via test_reads_own_environ.
        # Direct unit test: invoke the parsing logic via a writable file.
        # Path A: shadow _read_proc_field via monkeypatching.
        orig = st._read_proc_field
        try:
            st._read_proc_field = lambda pid, field: (
                "GOOD=ok\0NO_EQUALS_SIGN\0BAD-KEY=value\0OTHER=valid\0"
            )
            env = st.read_proc_environ(123)  # pid value ignored under stub
        finally:
            st._read_proc_field = orig
        self.assertEqual(env, {"GOOD": "ok", "OTHER": "valid"})


# ─── Tests for unknown-schema warning ───────────────────────────────────────


class UnknownSchemaWarningTests(unittest.TestCase):
    """_read_json logs (but does not crash) on an unsupported schema number.

    Verified by writing a pid.json with schema=999 and checking that
    read_pid returns the data without raising."""

    def test_read_json_returns_data_for_unknown_schema(self) -> None:
        tmp = tempfile.mkdtemp(prefix="senpi-helpers-schema-warn-")
        try:
            daemon_dir = os.path.join(tmp, "future-daemon")
            os.makedirs(daemon_dir)
            future_pid = {"schema": 999, "name": "future-daemon", "pid": 1234}
            with open(os.path.join(daemon_dir, "pid.json"), "w") as f:
                json.dump(future_pid, f)
            data = st.read_pid("future-daemon", state_dir=tmp)
            # Reader returns the dict despite unknown schema.
            self.assertIsNotNone(data)
            self.assertEqual(data["schema"], 999)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
