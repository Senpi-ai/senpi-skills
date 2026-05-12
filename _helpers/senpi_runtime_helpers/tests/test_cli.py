"""senpi-helpers CLI — argparse + `list` subcommand."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

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


if __name__ == "__main__":
    unittest.main()
