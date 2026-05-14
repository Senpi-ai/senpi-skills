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

    def test_cli_module_imports_canonical_implementation(self) -> None:
        from senpi_runtime_helpers import cli
        self.assertIs(cli._is_pid_alive, st.pid_alive)


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
        # boot.json bumped to schema 2 (interpreter-first argv); pid + heartbeat
        # are still schema 1. See state.py top-of-file comment for boot's
        # versioning history.
        expected_schemas = {"pid": 1, "boot": 2, "heartbeat": 1}
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


if __name__ == "__main__":
    unittest.main()
