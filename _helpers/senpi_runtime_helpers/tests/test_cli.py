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

    def test_stats_missing_log_path_returns_no_log_exit(self) -> None:
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
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = cli.main(["stats", "no-log-path"])
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
        self.assertEqual(rc, 1)  # STATS_NO_LOG
        self.assertIn("log path", err.lower())
        self.assertIn("SENPI_HELPERS_LOG_PATH", err)

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


if __name__ == "__main__":
    unittest.main()
