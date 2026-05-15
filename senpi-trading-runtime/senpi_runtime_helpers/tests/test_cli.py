"""senpi-helpers CLI — argparse + `list` subcommand."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timezone

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers import cli, state as _state


class CliFixtures(unittest.TestCase):
    """Shared setup: tmp state dir + env var; populate with daemon state files."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-cli-")
        self._env_prev = os.environ.get("SENPI_HELPERS_STATE_DIR")
        os.environ["SENPI_HELPERS_STATE_DIR"] = self.tmp

    def tearDown(self) -> None:
        if self._env_prev is None:
            os.environ.pop("SENPI_HELPERS_STATE_DIR", None)
        else:
            os.environ["SENPI_HELPERS_STATE_DIR"] = self._env_prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(
        self,
        name: str,
        *,
        pid: int = 99999999,
        wallet: str = "0x" + "a" * 40,
        scanner: str = "my_signals",
        tick_count: int = 5,
        error_count: int = 0,
        last_status: str = "ok",
        last_code=None,
    ) -> None:
        """Write pid.json + heartbeat.json + boot.json by hand."""
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1,
                "name": name,
                "pid": pid,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": wallet,
                "scanner": scanner,
                "interval_seconds": 300.0,
                "tick_timeout": 60.0,
                "log_path": f"/tmp/{name}.log",
                "version": "0.1.0",
            }, f)
        with open(os.path.join(d, "heartbeat.json"), "w") as f:
            json.dump({
                "schema": 1,
                "name": name,
                "last_tick_iso": "2026-05-12T08:55:00.000Z",
                "last_tick": tick_count,
                "last_tick_status": last_status,
                "last_tick_code": last_code,
                "last_tick_duration_ms": 1234,
                "last_tick_error": None,
                "tick_count": tick_count,
                "error_count": error_count,
            }, f)


class ListSubcommandTests(CliFixtures):
    def test_list_with_no_daemons_returns_zero_and_prints_message(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["list"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("No daemons registered", out)
        self.assertIn(self.tmp, out)  # path is surfaced

    def test_list_json_with_no_daemons_returns_empty_array(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["list", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc, {"daemons": []})

    def test_list_shows_seeded_daemons_in_table(self) -> None:
        self._seed("pangolin-tracker-6e92")
        self._seed("kodiak-tracker-aaaa", wallet="0x" + "b" * 40, scanner="kodiak_signals")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["list"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("NAME", out)
        self.assertIn("pangolin-tracker-6e92", out)
        self.assertIn("kodiak-tracker-aaaa", out)
        # Wallet shown short, not full.
        self.assertIn("0xaaaa…aaaa", out)
        self.assertIn("0xbbbb…bbbb", out)

    def test_list_json_emits_all_documented_fields(self) -> None:
        self._seed("pangolin-tracker-6e92", tick_count=42, error_count=3,
                   last_status="error", last_code="SenpiClientError")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["list", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(len(doc["daemons"]), 1)
        d = doc["daemons"][0]
        # Keys the table relies on must all be present.
        expected = {
            "name", "pid", "running", "wallet", "scanner",
            "interval_seconds", "start_time_iso", "log_path",
            "last_tick_iso", "last_tick_status", "last_tick_code",
            "tick_count", "error_count",
        }
        self.assertEqual(set(d.keys()), expected)
        self.assertEqual(d["name"], "pangolin-tracker-6e92")
        self.assertEqual(d["tick_count"], 42)
        self.assertEqual(d["error_count"], 3)
        self.assertEqual(d["last_tick_status"], "error")
        self.assertEqual(d["last_tick_code"], "SenpiClientError")

    def test_running_is_true_for_current_process_pid(self) -> None:
        # Use the test runner's PID — guaranteed alive.
        self._seed("alive-test", pid=os.getpid())
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["list", "--json"])
        doc = json.loads(buf.getvalue())
        self.assertTrue(doc["daemons"][0]["running"])

    def test_running_is_false_for_unrealistic_pid(self) -> None:
        self._seed("dead-test", pid=2147483646)  # near INT32_MAX, unlikely to exist
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["list", "--json"])
        doc = json.loads(buf.getvalue())
        self.assertFalse(doc["daemons"][0]["running"])

    def test_running_handles_missing_pid_file(self) -> None:
        """A daemon dir with only heartbeat (e.g. clean exit) → running=false."""
        d = os.path.join(self.tmp, "exited-clean")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "heartbeat.json"), "w") as f:
            json.dump({
                "schema": 1, "name": "exited-clean",
                "last_tick_iso": "2026-05-12T08:55:00.000Z",
                "last_tick": 10, "last_tick_status": "ok",
                "last_tick_code": None, "last_tick_duration_ms": 1,
                "last_tick_error": None, "tick_count": 10, "error_count": 0,
            }, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["list", "--json"])
        doc = json.loads(buf.getvalue())
        self.assertEqual(len(doc["daemons"]), 1)
        d_row = doc["daemons"][0]
        self.assertEqual(d_row["name"], "exited-clean")
        self.assertFalse(d_row["running"])
        self.assertIsNone(d_row["pid"])
        self.assertEqual(d_row["tick_count"], 10)

    def test_state_dir_arg_overrides_env(self) -> None:
        other_tmp = tempfile.mkdtemp(prefix="senpi-helpers-other-")
        try:
            # Daemon seeded into the env-var dir; CLI should NOT see it when
            # --state-dir points elsewhere.
            self._seed("hidden-from-cli")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["--state-dir", other_tmp, "list", "--json"])
            self.assertEqual(rc, 0)
            doc = json.loads(buf.getvalue())
            self.assertEqual(doc["daemons"], [])
        finally:
            shutil.rmtree(other_tmp, ignore_errors=True)


class HealthSubcommandTests(CliFixtures):
    def _seed_alive(self, name: str, **overrides) -> None:
        """Helper: seed a daemon with the test runner's PID (guaranteed alive)
        and an iso timestamp ~now (so it's "recent"). Overrides may override
        any heartbeat/pid field."""
        import time as _t
        now_iso = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime()) + ".000Z"
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        pid_doc = {
            "schema": 1,
            "name": name,
            "pid": os.getpid(),
            "start_time_iso": now_iso,
            "wallet": "0x" + "a" * 40,
            "scanner": "test_signals",
            "interval_seconds": 300.0,
            "tick_timeout": 60.0,
            "log_path": f"/tmp/{name}.log",
            "version": "0.1.0",
        }
        hb_doc = {
            "schema": 1,
            "name": name,
            "last_tick_iso": now_iso,
            "last_tick": 1,
            "last_tick_status": "ok",
            "last_tick_code": None,
            "last_tick_duration_ms": 100,
            "last_tick_error": None,
            "tick_count": 1,
            "error_count": 0,
        }
        for k, v in overrides.items():
            if k in pid_doc:
                pid_doc[k] = v
            if k in hb_doc:
                hb_doc[k] = v
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump(pid_doc, f)
        with open(os.path.join(d, "heartbeat.json"), "w") as f:
            json.dump(hb_doc, f)

    def test_healthy_daemon_exit_zero(self) -> None:
        self._seed_alive("svc")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["health", "svc", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["health"], "healthy")
        self.assertTrue(doc["running"])
        self.assertEqual(doc["last_tick_status"], "ok")

    def test_dead_pid_reports_down(self) -> None:
        self._seed_alive("svc", pid=2147483646)  # unrealistic high PID
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["health", "svc", "--json"])
        self.assertEqual(rc, 1)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["health"], "down")
        self.assertFalse(doc["running"])

    def test_stale_ticks_reports_unhealthy(self) -> None:
        # last_tick_iso 3 hours ago, interval 300s → age > 2*interval.
        import time as _t
        old_iso = _t.strftime("%Y-%m-%dT%H:%M:%S",
                              _t.gmtime(_t.time() - 3 * 3600)) + ".000Z"
        self._seed_alive("svc", last_tick_iso=old_iso)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["health", "svc", "--json"])
        self.assertEqual(rc, 1)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["health"], "stale_ticks")

    def test_last_tick_failed_reports_unhealthy(self) -> None:
        self._seed_alive("svc", last_tick_status="error",
                          last_tick_code="SenpiClientError")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["health", "svc", "--json"])
        self.assertEqual(rc, 1)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["health"], "last_tick_failed")
        self.assertEqual(doc["last_tick_code"], "SenpiClientError")

    def test_no_heartbeat_yet_reports_no_ticks(self) -> None:
        # Daemon has pid.json but no heartbeat yet — just started.
        d = os.path.join(self.tmp, "fresh")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": "fresh", "pid": os.getpid(),
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 60.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.1.0",
            }, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["health", "fresh", "--json"])
        self.assertEqual(rc, 1)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["health"], "no_ticks_yet")

    def test_missing_daemon_returns_not_found(self) -> None:
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out):
            # Capture stderr too
            old_stderr = sys.stderr
            sys.stderr = buf_err
            try:
                rc = cli.main(["health", "definitely-not-running"])
            finally:
                sys.stderr = old_stderr
        self.assertEqual(rc, 2)

    def test_name_omitted_with_one_daemon_uses_only_one(self) -> None:
        self._seed_alive("only-one")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["health", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["name"], "only-one")

    def test_name_omitted_with_multiple_daemons_errors(self) -> None:
        self._seed_alive("alpha")
        self._seed_alive("beta")
        buf_err = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf_err
        try:
            rc = cli.main(["health"])
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)
        self.assertIn("multiple", buf_err.getvalue().lower())

    def test_name_omitted_with_zero_daemons_errors(self) -> None:
        buf_err = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf_err
        try:
            rc = cli.main(["health"])
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)
        self.assertIn("no daemons", buf_err.getvalue().lower())

    def test_human_output_includes_key_fields(self) -> None:
        self._seed_alive("svc")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["health", "svc"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("name:", out)
        self.assertIn("health:", out)
        self.assertIn("svc", out)
        self.assertIn("healthy", out)
        self.assertIn("running:", out)
        self.assertIn("yes", out)


class StatsSubcommandTests(CliFixtures):
    """End-to-end: seed pid.json with a log_path, write some events, run stats."""

    def _seed_with_log(self, name: str, log_path: str) -> None:
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": name, "pid": os.getpid(),
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 300.0, "tick_timeout": 60.0,
                "log_path": log_path, "version": "0.1.0",
            }, f)

    def _make_log_file(self, lines):
        path = os.path.join(self.tmp, "events.log")
        with open(path, "w") as f:
            for line in lines:
                f.write(line + "\n")
        return path

    def test_stats_json_envelope_shape(self) -> None:
        log_path = self._make_log_file([
            '[senpi_helpers] {"event": "mcp_call", "iso": "2026-05-12T09:00:00.000Z", "status": "ok"}',
        ])
        self._seed_with_log("svc", log_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["stats", "svc", "--hours", "24", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        # Documented top-level envelope keys.
        for k in ("name", "log_path", "window_hours", "total_events_counted",
                  "earliest_event_iso", "log_size_bytes", "totals", "buckets"):
            self.assertIn(k, doc)
        self.assertEqual(doc["name"], "svc")
        self.assertEqual(doc["window_hours"], 24)

    def test_stats_missing_log_path_falls_back_to_default(self) -> None:
        """Bugbot caught the divergence between cmd_stats and cmd_logs.
        cmd_stats now uses the SAME 4-level fallback chain (pid → boot →
        env → /tmp/<name>.log). When the default path doesn't exist, we
        still return STATS_NO_LOG, but the error mentions exactly where
        we looked — more actionable than the old 'did not record a log
        path' message.
        """
        # Seed pid.json WITHOUT log_path.
        d = os.path.join(self.tmp, "no-log-path")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": "no-log-path", "pid": os.getpid(),
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 300.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.1.0",
            }, f)
        # Make sure /tmp/no-log-path.log doesn't exist (the fallback target).
        try:
            os.unlink("/tmp/no-log-path.log")
        except FileNotFoundError:
            pass
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["stats", "no-log-path"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)  # STATS_NO_LOG
        # Error references the resolved default path so the operator knows
        # exactly which file we tried to read.
        self.assertIn("/tmp/no-log-path.log", err)

    def test_stats_uses_default_path_same_as_logs_command(self) -> None:
        """Regression guard against Bugbot's finding: cmd_stats and
        cmd_logs should both succeed when /tmp/<name>.log exists, even if
        no daemon recorded log_path explicitly. Pre-fix, cmd_stats failed
        STATS_NO_LOG while cmd_logs found the default."""
        # Seed pid.json with log_path=None (the realistic case for an
        # older daemon launched before schema 2 always populated log_path).
        d = os.path.join(self.tmp, "default-log")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": "default-log", "pid": os.getpid(),
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 300.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.1.0",
            }, f)
        log_path = "/tmp/default-log.log"
        try:
            os.unlink(log_path)
        except FileNotFoundError:
            pass
        try:
            with open(log_path, "w") as f:
                f.write("")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["stats", "default-log", "--json"])
            self.assertEqual(rc, 0)
            doc = json.loads(buf.getvalue())
            self.assertEqual(doc["log_path"], log_path)
        finally:
            try:
                os.unlink(log_path)
            except FileNotFoundError:
                pass

    def test_stats_missing_log_file_returns_no_log_exit(self) -> None:
        self._seed_with_log("svc", "/no/such/file.log")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["stats", "svc"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)  # STATS_NO_LOG
        self.assertIn("not found", err.lower())

    def test_stats_missing_pid_returns_not_found(self) -> None:
        # Daemon dir doesn't exist at all.
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["stats", "ghost"])
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)  # STATS_NOT_FOUND

    def test_stats_human_output_includes_totals_section(self) -> None:
        log_path = self._make_log_file([
            '[senpi_helpers] {"event": "mcp_call", "iso": "2026-05-12T09:00:00.000Z", "status": "ok"}',
            '[senpi_helpers] {"event": "cache_hit", "iso": "2026-05-12T09:00:00.000Z", "tool": "x"}',
        ])
        self._seed_with_log("svc", log_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["stats", "svc", "--hours", "168"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("svc", out)
        self.assertIn("last 168 hours", out)
        self.assertIn("Totals", out)
        self.assertIn("MCP calls", out)
        self.assertIn("Cache hits", out)


class StopSubcommandTests(CliFixtures):
    def _seed_pid(self, name: str, pid: int) -> None:
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": name, "pid": pid,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 300.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.1.0",
            }, f)

    def test_stop_dead_pid_returns_already_dead_clears_pid_json(self) -> None:
        # Seed pid.json with a definitely-dead pid.
        self._seed_pid("svc", 2147483646)
        pid_path = os.path.join(self.tmp, "svc", "pid.json")
        self.assertTrue(os.path.exists(pid_path))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["stop", "svc", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["outcome"], "already_dead")
        # CLI should have cleaned up pid.json.
        self.assertFalse(os.path.exists(pid_path))

    def test_stop_missing_pid_returns_not_found(self) -> None:
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["stop", "ghost"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)
        self.assertIn("no pid.json", err.lower())

    def test_stop_invalid_pid_in_pidfile_returns_not_found(self) -> None:
        d = os.path.join(self.tmp, "bogus")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": "bogus", "pid": "not-an-int",
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 300.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.1.0",
            }, f)
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["stop", "bogus"])
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)

    def test_stop_human_output_includes_outcome(self) -> None:
        self._seed_pid("svc", 2147483646)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["stop", "svc"])
        self.assertIn("already stopped", buf.getvalue().lower())

    def test_stop_passes_timeout_through(self) -> None:
        """--timeout argument is forwarded to manage.stop_pid via args.timeout.

        Sanity check: argparse accepts the value and the subcommand runs.
        Actual escalation timing is tested in test_manage.py.
        """
        self._seed_pid("svc", 2147483646)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["stop", "svc", "--timeout", "5"])
        self.assertEqual(rc, 0)


class RestartSubcommandTests(CliFixtures):
    """Restart requires boot.json + an existing script_path. The actual
    subprocess.Popen is mocked via cli._manage monkeypatching so the test
    doesn't spawn real daemons."""

    def setUp(self) -> None:
        super().setUp()
        # Create a real on-disk script — cmd_restart checks os.path.exists.
        import tempfile
        fd, self.script = tempfile.mkstemp(suffix=".py", prefix="restart-fake-")
        os.write(fd, b"# placeholder daemon\n")
        os.close(fd)
        # Capture relaunch calls; replace with a callable that does nothing
        # except record args and return a fake success result.
        from senpi_runtime_helpers import cli as cli_mod
        self._cli = cli_mod
        self._orig_relaunch = self._cli._manage.relaunch_daemon
        self._relaunch_calls = []

        def fake_relaunch(**kwargs):
            self._relaunch_calls.append(kwargs)
            return {
                "outcome": self._cli._manage.RELAUNCH_OK,
                "pid": 4242,
                "error": None,
            }
        self._cli._manage.relaunch_daemon = fake_relaunch

    def tearDown(self) -> None:
        self._cli._manage.relaunch_daemon = self._orig_relaunch
        try:
            os.unlink(self.script)
        except OSError:
            pass
        super().tearDown()

    def _seed_full(self, name: str, *, pid=None, include_log=True) -> None:
        """Write pid.json + boot.json so restart has both files."""
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": name,
                "pid": pid if pid is not None else 2147483646,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 300.0, "tick_timeout": 60.0,
                "log_path": f"/tmp/{name}.log" if include_log else None,
                "version": "0.1.0",
            }, f)
        with open(os.path.join(d, "boot.json"), "w") as f:
            json.dump({
                "schema": 1, "name": name,
                "argv": [self.script, "--flag"],
                "script_path": self.script,
                "cwd": "/tmp",
                "env_snapshot": {},
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            }, f)

    def test_restart_success_with_dead_daemon_skips_stop(self) -> None:
        self._seed_full("svc")  # dead pid in pid.json
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["restart", "svc", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["outcome"], "restarted")
        self.assertEqual(doc["new_pid"], 4242)
        self.assertIsNone(doc["stop_result"])  # no stop needed
        self.assertEqual(len(self._relaunch_calls), 1)
        self.assertEqual(self._relaunch_calls[0]["argv"], [self.script, "--flag"])
        self.assertEqual(self._relaunch_calls[0]["cwd"], "/tmp")

    def test_restart_missing_boot_fails_with_actionable_message(self) -> None:
        # Pid.json only — no boot.json.
        d = os.path.join(self.tmp, "no-boot")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": "no-boot", "pid": 99999999,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 300.0, "tick_timeout": 60.0,
                "log_path": "/tmp/no-boot.log", "version": "0.1.0",
            }, f)
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["restart", "no-boot"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)
        self.assertIn("boot.json is missing", err)
        self.assertIn("manually", err)
        # Relaunch was NOT called.
        self.assertEqual(len(self._relaunch_calls), 0)

    def test_restart_missing_script_path_fails(self) -> None:
        d = os.path.join(self.tmp, "moved-script")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 1, "name": "moved-script", "pid": 99999999,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": "0x" + "a" * 40, "scanner": "s",
                "interval_seconds": 300.0, "tick_timeout": 60.0,
                "log_path": "/tmp/moved-script.log", "version": "0.1.0",
            }, f)
        with open(os.path.join(d, "boot.json"), "w") as f:
            json.dump({
                "schema": 1, "name": "moved-script",
                "argv": ["/no/such/script.py"],
                "script_path": "/no/such/script.py",
                "cwd": "/tmp", "env_snapshot": {},
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            }, f)
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["restart", "moved-script"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)
        self.assertIn("no longer exists", err)
        self.assertEqual(len(self._relaunch_calls), 0)

    def test_restart_no_log_path_uses_default_with_warning(self) -> None:
        """When no log_path is recorded anywhere (legacy schema-1 boot.json,
        no pid.json after clean stop, no SENPI_HELPERS_LOG_PATH), restart
        now falls back to /tmp/<name>.log with a stderr warning instead of
        bailing. Previously failed RC=1; today it succeeds so the new
        daemon's write_boot can persist a canonical path for next time."""
        self._seed_full("no-log", include_log=False)
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["restart", "no-log"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 0)
        self.assertIn("/tmp/no-log.log", err)
        # One relaunch_daemon call (the restart succeeded by using the default).
        self.assertEqual(len(self._relaunch_calls), 1)
        self.assertEqual(self._relaunch_calls[0]["log_path"], "/tmp/no-log.log")

    def test_restart_relaunch_failure_surfaces(self) -> None:
        self._seed_full("svc")
        def angry_relaunch(**kwargs):
            return {
                "outcome": self._cli._manage.RELAUNCH_SPAWN_FAILED,
                "pid": None,
                "error": "simulated spawn failure",
            }
        self._cli._manage.relaunch_daemon = angry_relaunch
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["restart", "svc"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)
        self.assertIn("relaunch failed", err.lower())
        self.assertIn("simulated spawn failure", err)

    def test_restart_no_daemon_record_returns_not_found(self) -> None:
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["restart", "ghost"])
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)


# ─── cmd_start (new in this PR) ─────────────────────────────────────────────


class StartSubcommandTests(RestartSubcommandTests):
    """`start` shares the relaunch path with `restart`; the difference is
    that start doesn't run stop_pid. Inheriting from RestartSubcommandTests
    reuses the fake-relaunch fixture without duplicating it."""

    def test_start_no_existing_pid_relaunches_cold(self) -> None:
        """Cleanly-stopped daemon (no pid.json) — start launches it fresh."""
        d = os.path.join(self.tmp, "cold")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "boot.json"), "w") as f:
            json.dump({
                "schema": 2, "name": "cold",
                "argv": [sys.executable, "-u", self.script],
                "script_path": self.script,
                "cwd": "/tmp",
                "env_snapshot": {},
                "log_path": "/tmp/cold.log",
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            }, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["start", "cold", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["outcome"], "started")
        self.assertEqual(doc["new_pid"], 4242)
        self.assertEqual(len(self._relaunch_calls), 1)
        self.assertEqual(self._relaunch_calls[0]["log_path"], "/tmp/cold.log")

    def test_start_idempotent_when_already_running(self) -> None:
        """If pid is alive AND fingerprints match → skip the relaunch."""
        # Use our own pid + matching fingerprints so _pid_alive_for_daemon
        # returns True. On non-Linux fingerprints are None, which also passes.
        d = os.path.join(self.tmp, "already-up")
        os.makedirs(d, exist_ok=True)
        own_pid = os.getpid()
        from senpi_runtime_helpers import state as st_mod
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 2, "name": "already-up", "pid": own_pid,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": None, "scanner": None,
                "interval_seconds": 60.0, "tick_timeout": 60.0,
                "log_path": "/tmp/already-up.log", "version": "0.0.0",
                "cmdline_fingerprint": st_mod.cmdline_fingerprint_for_pid(own_pid),
                "start_time_jiffies": st_mod.start_time_jiffies_for_pid(own_pid),
            }, f)
        with open(os.path.join(d, "boot.json"), "w") as f:
            json.dump({
                "schema": 2, "name": "already-up",
                "argv": [sys.executable, "-u", self.script],
                "script_path": self.script, "cwd": "/tmp",
                "env_snapshot": {}, "log_path": "/tmp/already-up.log",
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            }, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["start", "already-up", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["outcome"], "already_running")
        # Crucially, NO relaunch call fired.
        self.assertEqual(len(self._relaunch_calls), 0)

    def test_start_missing_boot_fails(self) -> None:
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["start", "ghost"])
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)
        self.assertEqual(len(self._relaunch_calls), 0)


# ─── pid-recycle guard at the CLI layer ─────────────────────────────────────


class AutoResolveDisabledForDestructiveCommandsTests(RestartSubcommandTests):
    """Single-daemon-host footgun fix. Stop / restart / start now require
    an explicit <name>; auto-resolve is reserved for read-only commands
    (list, health, stats, boot, logs). Verifies the new contract — and
    that read-only commands keep auto-resolving."""

    def _seed_minimal_daemon(self, name: str) -> None:
        """Just enough state so `list_daemons` returns this name."""
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 2, "name": name, "pid": 2147483646,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": None, "scanner": None,
                "interval_seconds": 60.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.0.0",
                "cmdline_fingerprint": None, "start_time_jiffies": None,
            }, f)

    def test_stop_without_name_on_single_daemon_host_errors(self) -> None:
        self._seed_minimal_daemon("solo")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["stop"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        # 2 = NOT_FOUND (semantic: "couldn't determine which daemon").
        self.assertEqual(rc, 2)
        self.assertIn("explicit <name>", err)
        self.assertIn("solo", err)

    def test_restart_without_name_on_single_daemon_host_errors(self) -> None:
        self._seed_minimal_daemon("solo")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["restart"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)
        self.assertIn("explicit <name>", err)
        # No relaunch_daemon should have fired.
        self.assertEqual(len(self._relaunch_calls), 0)

    def test_start_without_name_on_single_daemon_host_errors(self) -> None:
        self._seed_minimal_daemon("solo")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["start"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)
        self.assertIn("explicit <name>", err)
        self.assertEqual(len(self._relaunch_calls), 0)

    def test_list_without_name_still_works_on_single_daemon_host(self) -> None:
        """Read-only commands keep auto-resolve. Otherwise we'd break every
        operator's `senpi-helpers list` muscle memory."""
        self._seed_minimal_daemon("solo")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["list", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(len(doc["daemons"]), 1)
        self.assertEqual(doc["daemons"][0]["name"], "solo")

    def test_stop_with_explicit_name_still_works(self) -> None:
        """Confirm the existing happy-path didn't regress."""
        self._seed_minimal_daemon("explicit")  # writes pid.json with dead pid
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["stop", "explicit", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["outcome"], "already_dead")


class StopRecycleGuardTests(RestartSubcommandTests):
    """When pid.json's fingerprints don't match /proc/<pid>'s, cmd_stop must
    refuse to SIGTERM and clear the stale pid.json."""

    def test_stop_refuses_when_fingerprint_mismatch(self) -> None:
        """pid is alive (we use os.getpid()) but the recorded fingerprint
        won't match — recycle guard kicks in, no signal sent."""
        if not sys.platform.startswith("linux"):
            self.skipTest("requires /proc to compute live fingerprints")
        d = os.path.join(self.tmp, "stale")
        os.makedirs(d, exist_ok=True)
        own_pid = os.getpid()
        # Write pid.json with the WRONG fingerprint.
        with open(os.path.join(d, "pid.json"), "w") as f:
            json.dump({
                "schema": 2, "name": "stale", "pid": own_pid,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": None, "scanner": None,
                "interval_seconds": 60.0, "tick_timeout": 60.0,
                "log_path": "/tmp/stale.log", "version": "0.0.0",
                "cmdline_fingerprint": "0" * 64,  # impossible hash
                "start_time_jiffies": None,
            }, f)
        # Capture stdout + stderr so the recycle-guard message is testable.
        out = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            with redirect_stdout(out):
                rc = cli.main(["stop", "stale"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 0)  # treated as success (already dead)
        self.assertIn("recycle", err.lower())
        # pid.json should have been cleared by the recycle-guard branch.
        self.assertFalse(os.path.exists(os.path.join(d, "pid.json")))


class HealthComputationTests(unittest.TestCase):
    """Pure-function tests for the health-state rules."""

    def test_not_running_is_down(self) -> None:
        self.assertEqual(
            cli._compute_health(
                running=False, last_tick_age=10,
                last_tick_status="ok", interval_seconds=60,
            ),
            "down",
        )

    def test_running_no_tick_yet_is_no_ticks_yet(self) -> None:
        self.assertEqual(
            cli._compute_health(
                running=True, last_tick_age=None,
                last_tick_status=None, interval_seconds=60,
            ),
            "no_ticks_yet",
        )

    def test_age_within_2x_interval_is_healthy(self) -> None:
        self.assertEqual(
            cli._compute_health(
                running=True, last_tick_age=119,
                last_tick_status="ok", interval_seconds=60,
            ),
            "healthy",
        )

    def test_age_beyond_2x_interval_is_stale(self) -> None:
        self.assertEqual(
            cli._compute_health(
                running=True, last_tick_age=121,
                last_tick_status="ok", interval_seconds=60,
            ),
            "stale_ticks",
        )

    def test_last_status_error_is_last_tick_failed(self) -> None:
        self.assertEqual(
            cli._compute_health(
                running=True, last_tick_age=10,
                last_tick_status="error", interval_seconds=60,
            ),
            "last_tick_failed",
        )

    def test_last_status_skipped_locked_is_healthy(self) -> None:
        """skipped_locked = normal overlap, NOT a failure."""
        self.assertEqual(
            cli._compute_health(
                running=True, last_tick_age=10,
                last_tick_status="skipped_locked", interval_seconds=60,
            ),
            "healthy",
        )

    def test_last_status_timeout_is_last_tick_failed(self) -> None:
        self.assertEqual(
            cli._compute_health(
                running=True, last_tick_age=10,
                last_tick_status="timeout", interval_seconds=60,
            ),
            "last_tick_failed",
        )

    def test_no_interval_skips_stale_check(self) -> None:
        # Interval unknown → can't say "stale". Healthy if status ok.
        self.assertEqual(
            cli._compute_health(
                running=True, last_tick_age=99999,
                last_tick_status="ok", interval_seconds=None,
            ),
            "healthy",
        )


class ArgParseTests(unittest.TestCase):
    def test_no_subcommand_exits_with_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            cli.main([])
        # argparse on missing required subcommand exits with code 2.
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_subcommand_exits_with_error(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main(["notarealcommand"])

    def test_help_exits_clean(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)


class FormattingTests(unittest.TestCase):
    def test_format_duration_seconds(self) -> None:
        self.assertEqual(cli._format_duration(45), "45s")
        self.assertEqual(cli._format_duration(0), "0s")

    def test_format_duration_minutes(self) -> None:
        self.assertEqual(cli._format_duration(150), "2m 30s")

    def test_format_duration_hours(self) -> None:
        self.assertEqual(cli._format_duration(3700), "1h 1m")

    def test_format_duration_days(self) -> None:
        self.assertEqual(cli._format_duration(2 * 86400 + 5 * 3600), "2d 5h")

    def test_format_duration_none(self) -> None:
        self.assertEqual(cli._format_duration(None), "-")

    def test_shorten_wallet_long(self) -> None:
        full = "0x" + "a" * 40
        self.assertEqual(cli._shorten_wallet(full), "0xaaaa…aaaa")

    def test_shorten_wallet_short(self) -> None:
        self.assertEqual(cli._shorten_wallet("0xabc"), "0xabc")

    def test_shorten_wallet_none(self) -> None:
        self.assertEqual(cli._shorten_wallet(None), "-")

    def test_parse_iso_round_trip(self) -> None:
        dt = cli._parse_iso("2026-05-12T08:00:00.000Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.hour, 8)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_parse_iso_invalid(self) -> None:
        self.assertIsNone(cli._parse_iso("not a date"))
        self.assertIsNone(cli._parse_iso(None))
        self.assertIsNone(cli._parse_iso(""))

    def test_age_seconds_clamps_negative(self) -> None:
        """Clock skew can produce future timestamps — age must be >= 0."""
        import time as _t
        future = _t.strftime("%Y-%m-%dT%H:%M:%S",
                             _t.gmtime(_t.time() + 600)) + ".000Z"
        age = cli._age_seconds(future)
        self.assertEqual(age, 0)


# ─── cmd_boot ───────────────────────────────────────────────────────────────


class BootSubcommandTests(CliFixtures):
    """`senpi-helpers boot <name>` is a pure reader over boot.json.
    Verifies the JSON pass-through path, the human renderer, and the
    not-found error case."""

    def _write_boot(self, name: str, payload: dict) -> None:
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "boot.json"), "w") as f:
            json.dump(payload, f)

    def test_boot_emits_json_passthrough(self) -> None:
        payload = {
            "schema": 2, "name": "svc",
            "argv": ["/usr/bin/python3", "-u", "/data/scripts/svc.py"],
            "script_path": "/data/scripts/svc.py", "cwd": "/data",
            "env_snapshot": {"WALLET_ADDRESS": "0xabc"},
            "log_path": "/tmp/svc.log",
            "captured_at_iso": "2026-05-12T08:00:00.000Z",
        }
        self._write_boot("svc", payload)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["boot", "svc", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc, payload)

    def test_boot_human_output_includes_key_fields(self) -> None:
        self._write_boot("svc", {
            "schema": 2, "name": "svc",
            "argv": ["/usr/bin/python3", "/data/scripts/svc.py"],
            "script_path": "/data/scripts/svc.py", "cwd": "/data",
            "env_snapshot": {"WALLET_ADDRESS": "0xabc"},
            "log_path": "/tmp/svc.log",
            "captured_at_iso": "2026-05-12T08:00:00.000Z",
        })
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["boot", "svc"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("svc", out)
        self.assertIn("/data/scripts/svc.py", out)
        self.assertIn("/tmp/svc.log", out)
        self.assertIn("WALLET_ADDRESS", out)
        self.assertIn("0xabc", out)

    def test_boot_truncates_long_env_values_in_human_output(self) -> None:
        long_token = "x" * 200
        self._write_boot("svc", {
            "schema": 2, "name": "svc",
            "argv": [], "script_path": "/x.py", "cwd": "/",
            "env_snapshot": {"BIG_VAR": long_token},
            "log_path": None, "captured_at_iso": "2026-05-12T08:00:00.000Z",
        })
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["boot", "svc"])
        out = buf.getvalue()
        # First 60 chars of long_token should appear; the full 200-char value
        # should NOT (truncated with ellipsis).
        self.assertIn("x" * 60, out)
        self.assertNotIn("x" * 200, out)

    def test_boot_missing_returns_not_found(self) -> None:
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["boot", "ghost"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)
        self.assertIn("no boot.json", err)


# ─── cmd_logs ───────────────────────────────────────────────────────────────


# ─── Adversarial coverage of the seek-from-end tail (input domain) ──────────
#
# Walked the parameter space explicitly per the
# memory/feedback_tdd_adversarial.md discipline:
#   n: 0, 1, < lines, == lines, > lines
#   file size: empty, < chunk, > chunk (forces multiple backward reads)
#   last line: with trailing newline, without
#   single line longer than chunk (forces backward read until first \n)
#   non-UTF-8 bytes (errors=replace)


class ReadLastNLinesTests(unittest.TestCase):
    """_read_last_n_lines is the seek-from-end implementation. Tests
    correctness across input classes; the I/O cost (O(n × line length),
    not O(file size)) is implicit in the algorithm — verified by the
    'large file with small chunk' tests that exercise the backward loop."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tail-test-")
        self.path = os.path.join(self.tmp, "log.txt")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_bytes(self, b: bytes) -> None:
        with open(self.path, "wb") as f:
            f.write(b)

    def test_n_zero_returns_empty(self) -> None:
        self._write_bytes(b"a\nb\nc\n")
        self.assertEqual(cli._read_last_n_lines(self.path, n=0), [])

    def test_empty_file_returns_empty(self) -> None:
        self._write_bytes(b"")
        self.assertEqual(cli._read_last_n_lines(self.path, n=10), [])

    def test_n_equals_file_lines_returns_all(self) -> None:
        self._write_bytes(b"a\nb\nc\n")
        self.assertEqual(
            cli._read_last_n_lines(self.path, n=3), ["a\n", "b\n", "c\n"],
        )

    def test_n_more_than_file_lines_returns_all(self) -> None:
        self._write_bytes(b"a\nb\n")
        self.assertEqual(
            cli._read_last_n_lines(self.path, n=10), ["a\n", "b\n"],
        )

    def test_n_one_returns_only_last_line(self) -> None:
        self._write_bytes(b"a\nb\nc\n")
        self.assertEqual(cli._read_last_n_lines(self.path, n=1), ["c\n"])

    def test_last_line_without_trailing_newline_preserved(self) -> None:
        self._write_bytes(b"a\nb\nfinal-no-newline")
        result = cli._read_last_n_lines(self.path, n=2)
        self.assertEqual(result, ["b\n", "final-no-newline"])

    def test_single_line_no_newline(self) -> None:
        self._write_bytes(b"only-line-no-newline")
        self.assertEqual(
            cli._read_last_n_lines(self.path, n=10), ["only-line-no-newline"],
        )

    def test_file_larger_than_chunk_forces_backward_loop(self) -> None:
        """File >> chunk_bytes. Backward-read loop must keep reading
        chunks until enough newlines accumulated. This is the case that
        the deque-based implementation handled correctly but slowly —
        and that the seek-from-end implementation handles fast."""
        # 200 lines × ~30 bytes each = ~6 KB. Use chunk_bytes=64 to force
        # ~100 backward iterations — the loop's correctness under load.
        for i in range(200):
            with open(self.path, "ab") as f:
                f.write(f"line-{i:04d}-some-content\n".encode())
        result = cli._read_last_n_lines(self.path, n=5, chunk_bytes=64)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0], "line-0195-some-content\n")
        self.assertEqual(result[-1], "line-0199-some-content\n")

    def test_single_line_larger_than_chunk(self) -> None:
        """A single line longer than chunk_bytes. Backward loop reads
        until pos==0 because no newline boundary is found. Returns the
        whole giant line."""
        giant = b"x" * 50_000 + b"\n"  # one 50KB line with newline
        self._write_bytes(giant)
        result = cli._read_last_n_lines(self.path, n=1, chunk_bytes=4096)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 50_001)  # 50K x's + \n
        self.assertTrue(result[0].endswith("\n"))

    def test_non_utf8_bytes_decoded_with_replace(self) -> None:
        """Log files can contain stray binary bytes (e.g. control chars
        from a misbehaving subprocess). The bytes-mode read + decode with
        errors='replace' should never raise."""
        self._write_bytes(b"valid line\n\x80\x81invalid\nlast\n")
        result = cli._read_last_n_lines(self.path, n=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "valid line\n")
        # Replacement char might be U+FFFD or the literal — either is fine
        # so long as no exception was raised.
        self.assertEqual(result[2], "last\n")


class LogsSubcommandTests(CliFixtures):
    """`senpi-helpers logs <name>` prints the tail of the daemon's log file.
    Tests the non-follow path (the follow path is real-time streaming —
    too fragile to unit-test deterministically, exercised manually)."""

    def _seed_boot_with_log(self, name: str, log_path: str) -> None:
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "boot.json"), "w") as f:
            json.dump({
                "schema": 2, "name": name,
                "argv": [], "script_path": "/x.py", "cwd": "/",
                "env_snapshot": {}, "log_path": log_path,
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            }, f)

    def test_logs_prints_last_n_lines(self) -> None:
        log_path = os.path.join(self.tmp, "svc.log")
        with open(log_path, "w") as f:
            for i in range(200):
                f.write(f"line-{i}\n")
        self._seed_boot_with_log("svc", log_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["logs", "svc", "--lines", "10"])
        out = buf.getvalue().splitlines()
        self.assertEqual(rc, 0)
        self.assertEqual(len(out), 10)
        # Tail = last 10 lines: line-190 .. line-199.
        self.assertEqual(out[0], "line-190")
        self.assertEqual(out[-1], "line-199")

    def test_logs_default_lines_is_50(self) -> None:
        log_path = os.path.join(self.tmp, "svc2.log")
        with open(log_path, "w") as f:
            for i in range(120):
                f.write(f"row-{i}\n")
        self._seed_boot_with_log("svc2", log_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["logs", "svc2"])
        out = buf.getvalue().splitlines()
        self.assertEqual(rc, 0)
        self.assertEqual(len(out), 50)
        # Tail = last 50: row-70 .. row-119.
        self.assertEqual(out[0], "row-70")
        self.assertEqual(out[-1], "row-119")

    def test_logs_resolves_path_from_boot_json_when_pid_json_absent(self) -> None:
        """After a clean stop, pid.json is gone but boot.json still has
        log_path. `logs` should use the boot.json value."""
        log_path = os.path.join(self.tmp, "post-stop.log")
        with open(log_path, "w") as f:
            f.write("only-line\n")
        self._seed_boot_with_log("cleanly-stopped", log_path)
        # No pid.json on disk for this daemon.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["logs", "cleanly-stopped"])
        out = buf.getvalue().strip()
        self.assertEqual(rc, 0)
        self.assertEqual(out, "only-line")

    def test_logs_negative_lines_rejected_cleanly(self) -> None:
        """argparse accepts negative integers as type=int. `deque(maxlen=-1)`
        would raise ValueError → uncaught traceback to the operator.
        cmd_logs must validate the value and emit a clean error."""
        log_path = os.path.join(self.tmp, "neg.log")
        with open(log_path, "w") as f:
            f.write("line\n")
        self._seed_boot_with_log("neg-lines", log_path)
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["logs", "neg-lines", "-n", "-5"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        # Either non-zero exit (graceful error) or zero (clamped to 0 with
        # a warning). Critical: NO unhandled exception, NO crash.
        self.assertNotEqual(rc, None)
        # Error should mention the bad value explicitly.
        self.assertTrue(
            "lines" in err.lower() or "negative" in err.lower(),
            f"expected error about lines value; got: {err!r}",
        )

    def test_logs_zero_lines_prints_nothing(self) -> None:
        """`--lines 0` is a degenerate but valid request: print no lines.
        Should not crash."""
        log_path = os.path.join(self.tmp, "zero.log")
        with open(log_path, "w") as f:
            f.write("hello\n" * 5)
        self._seed_boot_with_log("zero-lines", log_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["logs", "zero-lines", "-n", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "")

    def test_logs_missing_file_returns_no_log(self) -> None:
        # boot.json points at a path that doesn't exist on disk.
        self._seed_boot_with_log("missing", "/tmp/does-not-exist-XYZ.log")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["logs", "missing"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)  # LOGS_NO_LOG
        self.assertIn("log file not found", err)


# ─── cmd_diagnose ───────────────────────────────────────────────────────────


class DiagnoseSubcommandTests(CliFixtures):
    """`senpi-helpers diagnose <name>` runs the pre-flight checklist over
    pid/boot/heartbeat/log. Each test stages a specific failure mode and
    asserts the right check key flips to fail."""

    def _write_files(
        self, name: str, *,
        pid_data: dict = None,
        boot_data: dict = None,
        hb_data: dict = None,
    ) -> None:
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        if pid_data is not None:
            with open(os.path.join(d, "pid.json"), "w") as f:
                json.dump(pid_data, f)
        if boot_data is not None:
            with open(os.path.join(d, "boot.json"), "w") as f:
                json.dump(boot_data, f)
        if hb_data is not None:
            with open(os.path.join(d, "heartbeat.json"), "w") as f:
                json.dump(hb_data, f)

    def _run_json(self, name: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["diagnose", name, "--json"])
        return {"rc": rc, "doc": json.loads(buf.getvalue())}

    def _check_status(self, doc: dict, key: str) -> str:
        for c in doc["checks"]:
            if c["key"] == key:
                return c["status"]
        return "missing"

    def test_diagnose_no_state_returns_not_found(self) -> None:
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["diagnose", "ghost"])
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 2)

    def test_diagnose_missing_boot_json_fails_that_check(self) -> None:
        # Only heartbeat present.
        self._write_files("hb-only", hb_data={
            "schema": 1, "name": "hb-only",
            "last_tick_iso": "2026-05-12T08:00:00.000Z",
            "last_tick_status": "ok", "tick_count": 1, "error_count": 0,
        })
        r = self._run_json("hb-only")
        self.assertEqual(self._check_status(r["doc"], "boot_json_present"), "fail")
        self.assertEqual(r["rc"], 1)  # any fail → DIAGNOSE_UNHEALTHY

    def test_diagnose_script_path_missing_on_disk_fails(self) -> None:
        self._write_files("ghost-script", boot_data={
            "schema": 2, "name": "ghost-script",
            "argv": [], "script_path": "/nonexistent/path.py",
            "cwd": "/", "env_snapshot": {}, "log_path": None,
            "captured_at_iso": "2026-05-12T08:00:00.000Z",
        })
        r = self._run_json("ghost-script")
        self.assertEqual(self._check_status(r["doc"], "script_path_exists"), "fail")

    def test_diagnose_pid_dead_fails_pid_alive_check(self) -> None:
        self._write_files("dead-pid",
            boot_data={
                "schema": 2, "name": "dead-pid",
                "argv": [], "script_path": self.tmp,  # exists (dir)
                "cwd": "/", "env_snapshot": {}, "log_path": None,
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            },
            pid_data={
                "schema": 2, "name": "dead-pid", "pid": 2147483646,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": None, "scanner": None,
                "interval_seconds": 60.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.0.0",
                "cmdline_fingerprint": None, "start_time_jiffies": None,
            },
        )
        r = self._run_json("dead-pid")
        self.assertEqual(self._check_status(r["doc"], "pid_alive"), "fail")

    def test_diagnose_alive_with_recycled_pid_fails_fingerprint_check(self) -> None:
        if not sys.platform.startswith("linux"):
            self.skipTest("recycle guard requires /proc")
        own_pid = os.getpid()
        self._write_files("recycled",
            boot_data={
                "schema": 2, "name": "recycled",
                "argv": [], "script_path": self.tmp,
                "cwd": "/", "env_snapshot": {}, "log_path": None,
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            },
            pid_data={
                "schema": 2, "name": "recycled", "pid": own_pid,
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": None, "scanner": None,
                "interval_seconds": 60.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.0.0",
                "cmdline_fingerprint": "0" * 64,   # bogus
                "start_time_jiffies": None,
            },
        )
        r = self._run_json("recycled")
        self.assertEqual(self._check_status(r["doc"], "pid_alive_and_matches"), "fail")

    def test_diagnose_stale_heartbeat_fails(self) -> None:
        # last_tick was hours ago; interval is 60s → way past 2× threshold.
        self._write_files("stale",
            boot_data={
                "schema": 2, "name": "stale",
                "argv": [], "script_path": self.tmp,
                "cwd": "/", "env_snapshot": {}, "log_path": None,
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            },
            pid_data={
                "schema": 2, "name": "stale", "pid": os.getpid(),
                "start_time_iso": "2026-05-12T08:00:00.000Z",
                "wallet": None, "scanner": None,
                "interval_seconds": 60.0, "tick_timeout": 60.0,
                "log_path": None, "version": "0.0.0",
                "cmdline_fingerprint": None, "start_time_jiffies": None,
            },
            hb_data={
                "schema": 1, "name": "stale",
                "last_tick_iso": "2020-01-01T00:00:00.000Z",  # ancient
                "last_tick_status": "ok",
                "tick_count": 1, "error_count": 0,
            },
        )
        r = self._run_json("stale")
        self.assertEqual(self._check_status(r["doc"], "heartbeat_fresh"), "fail")

    def test_diagnose_all_passing_returns_ok(self) -> None:
        # Build a fully-clean state: script_path = an existing file in tmp,
        # pid = own pid (alive), no fingerprints (degrades cleanly),
        # heartbeat = recent (within 2× interval).
        from datetime import datetime, timezone, timedelta
        recent_iso = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        script_path = os.path.join(self.tmp, "fake-script.py")
        with open(script_path, "w") as f:
            f.write("# stub\n")
        log_path = os.path.join(self.tmp, "fake.log")
        with open(log_path, "w") as f:
            f.write("starting...\n")
        self._write_files("clean",
            boot_data={
                "schema": 2, "name": "clean",
                "argv": [], "script_path": script_path,
                "cwd": "/", "env_snapshot": {}, "log_path": log_path,
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            },
            pid_data={
                "schema": 2, "name": "clean", "pid": os.getpid(),
                "start_time_iso": recent_iso,
                "wallet": None, "scanner": None,
                "interval_seconds": 60.0, "tick_timeout": 60.0,
                "log_path": log_path, "version": "0.0.0",
                "cmdline_fingerprint": None, "start_time_jiffies": None,
            },
            hb_data={
                "schema": 1, "name": "clean",
                "last_tick_iso": recent_iso,
                "last_tick_status": "ok",
                "tick_count": 1, "error_count": 0,
            },
        )
        r = self._run_json("clean")
        self.assertEqual(r["rc"], 0)
        # No checks should be fail.
        fails = [c for c in r["doc"]["checks"] if c["status"] == "fail"]
        self.assertEqual(fails, [], msg=f"unexpected failures: {fails}")


# ─── cmd_start --inherit-env-from ───────────────────────────────────────────


class StartInheritEnvFromTests(CliFixtures):
    """`senpi-helpers start --inherit-env-from <pid|openclaw>` reads /proc
    environ and merges with the caller's env. Operator-set env wins."""

    def _seed_minimal_boot(self, name: str) -> str:
        """Returns the script_path that boot.json points at."""
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        # Make a real script file so cmd_start's existence check passes.
        script = os.path.join(self.tmp, f"{name}-script.py")
        with open(script, "w") as f:
            f.write("# stub\n")
        with open(os.path.join(d, "boot.json"), "w") as f:
            json.dump({
                "schema": 2, "name": name,
                "argv": [sys.executable, "-u", script],
                "script_path": script, "cwd": "/",
                "env_snapshot": {}, "log_path": f"/tmp/{name}.log",
                "captured_at_iso": "2026-05-12T08:00:00.000Z",
            }, f)
        return script

    def setUp(self) -> None:
        super().setUp()
        # Stub relaunch_daemon so the test never spawns a real process.
        self._cli = cli
        self._orig_relaunch = self._cli._manage.relaunch_daemon
        self._relaunch_calls = []

        def fake_relaunch(**kwargs):
            self._relaunch_calls.append(kwargs)
            return {
                "outcome": self._cli._manage.RELAUNCH_OK,
                "pid": 4242, "error": None,
                "argv_normalized": False, "argv_used": kwargs.get("argv", []),
            }
        self._cli._manage.relaunch_daemon = fake_relaunch

    def tearDown(self) -> None:
        self._cli._manage.relaunch_daemon = self._orig_relaunch
        super().tearDown()

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "/proc-based env inheritance is Linux-only",
    )
    def test_inherit_env_from_own_pid_merges_into_relaunch(self) -> None:
        self._seed_minimal_boot("env-test")
        # Set a unique env var so we can verify it's inherited.
        os.environ["SENPI_HELPERS_INHERIT_TEST"] = "from-our-environ"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "start", "env-test", "--inherit-env-from", str(os.getpid()),
                    "--json",
                ])
        finally:
            os.environ.pop("SENPI_HELPERS_INHERIT_TEST", None)
        self.assertEqual(rc, 0)
        # relaunch_daemon was called with an env dict (not None).
        self.assertEqual(len(self._relaunch_calls), 1)
        env_passed = self._relaunch_calls[0]["env"]
        self.assertIsInstance(env_passed, dict)
        # Either HOME or PATH should be there (inherited from this process).
        self.assertTrue("HOME" in env_passed or "PATH" in env_passed)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "/proc-based env inheritance is Linux-only",
    )
    def test_inherit_env_explicit_env_wins_over_inherited(self) -> None:
        """Operator-set env in the current shell takes priority — fills in
        the auth tokens from the inherited env, but the operator can
        override anything they explicitly set."""
        self._seed_minimal_boot("priority-test")
        # Inherited (from our /proc/self/environ) will have HOME.
        # Override it in our os.environ to a unique value.
        os.environ["HOME"] = "/explicitly-overridden"
        try:
            cli.main([
                "start", "priority-test", "--inherit-env-from", str(os.getpid()),
                "--json",
            ])
        finally:
            # Restore parent's HOME — important for the rest of the test
            # suite, which may rely on it.
            del os.environ["HOME"]
        env_passed = self._relaunch_calls[0]["env"]
        self.assertEqual(env_passed["HOME"], "/explicitly-overridden")

    def test_inherit_env_invalid_value_errors_cleanly(self) -> None:
        self._seed_minimal_boot("invalid-test")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main([
                "start", "invalid-test", "--inherit-env-from", "not-a-pid-or-openclaw",
            ])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)  # START_FAILED
        self.assertIn("invalid value", err)
        # Crucially, NO relaunch call fired.
        self.assertEqual(len(self._relaunch_calls), 0)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Tests the /proc-read failure path; on macOS the Linux gate fires first",
    )
    def test_inherit_env_nonexistent_pid_errors_cleanly(self) -> None:
        self._seed_minimal_boot("ghost-pid")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main([
                "start", "ghost-pid", "--inherit-env-from", "2147483646",
            ])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)
        self.assertIn("could not read", err)
        self.assertEqual(len(self._relaunch_calls), 0)

    def test_inherit_env_openclaw_alias_invokes_pgrep(self) -> None:
        """The 'openclaw' literal resolves via pgrep. Tries `pgrep -x openclaw`
        first, falls back to a cmdline word match. We mock subprocess.run
        so we don't depend on a real openclaw process on the test host."""
        self._seed_minimal_boot("oc-alias")
        import subprocess as sp
        orig_run = sp.run

        def fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = f"{os.getpid()}\n"
            # Either resolution strategy returns our pid — we don't pin
            # which one fires first so the test is resilient to ordering
            # changes inside _resolve_inherit_env_source.
            if cmd[:2] == ["pgrep", "-x"] and "openclaw" in cmd:
                return R()
            if cmd[:2] == ["pgrep", "-f"] and any("openclaw" in c for c in cmd):
                return R()
            return orig_run(cmd, **kwargs)
        sp.run = fake_run
        try:
            if not sys.platform.startswith("linux"):
                # On macOS we expect the linux-gate to fail; only run the
                # check we care about: pgrep was attempted.
                old_stderr = sys.stderr
                sys.stderr = io.StringIO()
                try:
                    rc = cli.main([
                        "start", "oc-alias", "--inherit-env-from", "openclaw",
                    ])
                finally:
                    sys.stderr = old_stderr
                self.assertEqual(rc, 1)
                return
            rc = cli.main([
                "start", "oc-alias", "--inherit-env-from", "openclaw", "--json",
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(len(self._relaunch_calls), 1)
        finally:
            sp.run = orig_run

    def test_inherit_env_openclaw_alias_falls_back_when_exact_match_fails(self) -> None:
        """Real installs put openclaw at /usr/local/bin/openclaw (so `pgrep -x
        openclaw` matches comm) or run `node .../openclaw` (so comm is
        `node` and only the cmdline match catches it). Verify the fallback
        path fires when the exact-comm match misses."""
        self._seed_minimal_boot("oc-fallback")
        import subprocess as sp
        orig_run = sp.run
        invocations = []

        def fake_run(cmd, **kwargs):
            invocations.append(cmd)
            class R:
                returncode = 0
                stdout = ""
            if cmd[:2] == ["pgrep", "-x"]:
                # Simulate "no exact comm match" — exit code 1, no stdout.
                R.returncode = 1
                return R()
            if cmd[:2] == ["pgrep", "-f"]:
                R.stdout = f"{os.getpid()}\n"
                return R()
            return orig_run(cmd, **kwargs)
        sp.run = fake_run
        try:
            if not sys.platform.startswith("linux"):
                self.skipTest("/proc lookup is Linux-only after pgrep resolves")
            rc = cli.main([
                "start", "oc-fallback", "--inherit-env-from", "openclaw", "--json",
            ])
            self.assertEqual(rc, 0)
            # Both strategies should have been attempted in order.
            self.assertEqual(len(invocations), 2)
            self.assertEqual(invocations[0][:2], ["pgrep", "-x"])
            self.assertEqual(invocations[1][:2], ["pgrep", "-f"])
        finally:
            sp.run = orig_run

    @unittest.skipIf(
        sys.platform.startswith("linux"),
        "macOS-only: verifies the Linux gate fires off Linux",
    )
    def test_inherit_env_on_non_linux_returns_error(self) -> None:
        self._seed_minimal_boot("dev-host")
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main([
                "start", "dev-host", "--inherit-env-from", str(os.getpid()),
            ])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)
        self.assertIn("Linux", err)
        self.assertEqual(len(self._relaunch_calls), 0)


# ─── _LogTailer state machine (adversarial input coverage) ──────────────────


class LogTailerStepTests(unittest.TestCase):
    """Unit-tests for the `tail -F` state machine extracted from
    `_stream_log`. Tests the input domain explicitly rather than the
    one happy path the original `_stream_log` exercised manually.

    Each test drives `step()` directly with controlled file-system state."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-helpers-tailer-")
        self.path = os.path.join(self.tmp, "log.txt")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, content: str, *, append: bool = False) -> None:
        mode = "a" if append else "w"
        with open(self.path, mode) as f:
            f.write(content)

    def test_first_open_skips_history(self) -> None:
        """The very first step() must seek-to-end so `logs --follow`
        doesn't replay the whole historical log."""
        self._write("historical line 1\nhistorical line 2\n")
        tailer = cli._LogTailer(self.path)
        action, payload = tailer.step()
        self.assertEqual(action, "wait")
        self.assertIsNone(payload)
        # If subsequent appends happen, those DO show up.
        self._write("new line\n", append=True)
        action, payload = tailer.step()
        self.assertEqual(action, "output")
        self.assertEqual(payload, "new line\n")
        tailer.close()

    def test_subsequent_appends_yield_output(self) -> None:
        self._write("a\n")
        tailer = cli._LogTailer(self.path)
        tailer.step()  # first open; skips history
        self._write("b\nc\n", append=True)
        action, payload = tailer.step()
        self.assertEqual(action, "output")
        self.assertEqual(payload, "b\nc\n")
        tailer.close()

    def test_rotation_reads_new_file_from_start(self) -> None:
        """When the inode changes (rotation), the new file's full content
        must be returned — NOT skipped via seek-to-end. This was the
        Bugbot finding on commit 1a84707."""
        self._write("old content\n")
        tailer = cli._LogTailer(self.path)
        tailer.step()  # first open, skip history
        # Rotate: replace the file with a fresh one (different inode).
        os.unlink(self.path)
        self._write("rotated content line 1\nrotated content line 2\n")
        action, payload = tailer.step()
        self.assertEqual(action, "output")
        self.assertEqual(
            payload, "rotated content line 1\nrotated content line 2\n",
            "rotation reopen must read from byte 0 of the new file",
        )
        tailer.close()

    def test_disappear_then_reappear_reads_new_content(self) -> None:
        """The bug I introduced in commit 7a03c7c: after FileNotFoundError,
        inode resets to None, then `is_first_open = (inode is None)` was
        True on the next reopen → seek-to-end → content lost. The
        `started`-flag refactor fixes this."""
        self._write("initial\n")
        tailer = cli._LogTailer(self.path)
        tailer.step()  # first open, skips history; started=True now
        # File disappears.
        os.unlink(self.path)
        action, _ = tailer.step()
        self.assertEqual(action, "wait")
        self.assertIsNone(tailer.fh, "fh should be closed after FileNotFoundError")
        self.assertIsNone(tailer.inode, "inode reset so reopen fires on reappearance")
        # File reappears with new content.
        self._write("reappeared line 1\nreappeared line 2\n")
        action, payload = tailer.step()
        self.assertEqual(action, "output")
        self.assertEqual(
            payload, "reappeared line 1\nreappeared line 2\n",
            "post-disappear reopen must read from byte 0, not seek-to-end",
        )
        tailer.close()

    def test_truncation_resets_position_and_reads_from_zero(self) -> None:
        """File shrunk below our last position → seek to 0, read whatever's
        there (may be smaller than what we'd already emitted)."""
        self._write("aaaaaaaaaaaaaaaaaaaa\n")  # 21 bytes
        tailer = cli._LogTailer(self.path)
        tailer.step()  # skip history
        self._write("bb\n")  # truncated to 3 bytes
        action, payload = tailer.step()
        self.assertEqual(action, "output")
        self.assertEqual(payload, "bb\n")
        tailer.close()

    def test_missing_file_returns_wait_without_crashing(self) -> None:
        """File never exists. Tailer should return 'wait' indefinitely
        without raising."""
        # Path points at a nonexistent file from the start.
        tailer = cli._LogTailer(os.path.join(self.tmp, "never-created.log"))
        for _ in range(5):
            action, payload = tailer.step()
            self.assertEqual(action, "wait")
        tailer.close()

    def test_open_failure_after_stat_succeeds_returns_wait(self) -> None:
        """Adversarial race: between os.stat (succeeds) and open() (raises),
        the file is deleted or permissions yanked. step() must catch the
        OSError, reset state, and return wait — NOT propagate."""
        self._write("content\n")
        tailer = cli._LogTailer(self.path)
        tailer.step()  # first open establishes baseline; skips history

        import builtins
        real_open = builtins.open
        real_stat = os.stat
        try:
            os.stat = lambda path: type("S", (), {
                "st_ino": (tailer.inode or 0) + 1,  # forces inode-change branch
                "st_size": 100,
            })()

            def raising_open(*args, **kwargs):
                if args and args[0] == self.path:
                    raise PermissionError("simulated race: file unreadable")
                return real_open(*args, **kwargs)
            builtins.open = raising_open

            action, payload = tailer.step()
        finally:
            builtins.open = real_open
            os.stat = real_stat

        self.assertEqual(action, "wait")
        self.assertIsNone(payload)
        self.assertIsNone(tailer.fh, "fh must be None after open failure")
        self.assertIsNone(tailer.inode, "inode must be reset for clean retry")
        tailer.close()

    def test_open_failure_then_next_step_does_not_crash_on_closed_fh(self) -> None:
        """The exact bug Cursor Bugbot caught on commit 57fad61.

        If reopen's open() raises after self.fh.close() succeeded, the
        OLD self.fh attribute pointed at the now-closed handle. On the
        next step() call — if the inode happened to match the old one —
        the reopen branch was skipped, then self.fh.read() raised
        ValueError ('I/O operation on closed file').

        Verify the fix: after an open failure, the next normal step()
        recovers cleanly without touching the closed handle.
        """
        self._write("initial\n")
        tailer = cli._LogTailer(self.path)
        tailer.step()  # first open
        old_inode = tailer.inode

        # Force an open failure by closing the file beneath us AND making
        # open raise. We use the same monkeypatch trick as above to drive
        # the reopen branch and force the failure deterministically.
        import builtins
        real_open = builtins.open
        real_stat = os.stat
        try:
            # Inode reported by stat differs → forces reopen.
            os.stat = lambda path: type("S", (), {
                "st_ino": (old_inode or 0) + 1,
                "st_size": 100,
            })()

            def raising_open(*args, **kwargs):
                if args and args[0] == self.path:
                    raise PermissionError("simulated")
                return real_open(*args, **kwargs)
            builtins.open = raising_open
            tailer.step()  # open fails — fh and inode must reset
        finally:
            builtins.open = real_open
            os.stat = real_stat

        # Now the file is back; the next step must NOT touch a closed
        # handle. Critical: even if inode happens to match, fh is None
        # (the fix), so the reopen branch fires fresh.
        self._write("recovered\n", append=True)
        action, payload = tailer.step()  # must not raise
        self.assertEqual(action, "output")
        # The reopen reads from byte 0 (per the started-flag rule:
        # only the very first open seeks to end). So we get the full
        # file content. The CRITICAL assertion is "no exception thrown
        # on closed-handle"; the exact payload is incidental but locked
        # for regression visibility.
        self.assertEqual(payload, "initial\nrecovered\n")
        tailer.close()

    def test_started_flag_survives_multiple_disappearances(self) -> None:
        """A flapping log file (created → deleted → recreated → deleted →
        recreated …) must always read from byte 0 after the first open.
        Only the very first open seeks to end."""
        self._write("history\n")
        tailer = cli._LogTailer(self.path)
        tailer.step()  # first open
        # Disappear / reappear cycle 1.
        os.unlink(self.path)
        tailer.step()  # wait
        self._write("cycle1 content\n")
        action, payload = tailer.step()
        self.assertEqual(payload, "cycle1 content\n")
        # Disappear / reappear cycle 2.
        os.unlink(self.path)
        tailer.step()  # wait
        self._write("cycle2 content\n")
        action, payload = tailer.step()
        self.assertEqual(payload, "cycle2 content\n",
                         "second reappearance must also read from byte 0")
        tailer.close()


if __name__ == "__main__":
    unittest.main()
