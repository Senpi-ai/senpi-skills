"""stats.py — log parser + hourly UTC bucket aggregator."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers import stats


def _event(name: str, *, iso: str, **fields):
    return {"event": name, "iso": iso, **fields}


class ParseLogLineTests(unittest.TestCase):
    def test_valid_event_line(self) -> None:
        line = '[senpi_helpers] {"event": "mcp_call", "tool": "x", "status": "ok"}'
        out = stats.parse_log_line(line)
        self.assertEqual(out["event"], "mcp_call")
        self.assertEqual(out["status"], "ok")

    def test_line_without_prefix_is_none(self) -> None:
        self.assertIsNone(stats.parse_log_line("some unrelated log line"))

    def test_malformed_json_is_none(self) -> None:
        line = '[senpi_helpers] {malformed json'
        self.assertIsNone(stats.parse_log_line(line))

    def test_non_object_payload_is_none(self) -> None:
        line = '[senpi_helpers] "just a string"'
        self.assertIsNone(stats.parse_log_line(line))

    def test_line_with_leading_timestamp_still_parses(self) -> None:
        # Some log shipping prepends its own timestamp; the helper prefix may
        # not be at index 0.
        line = '2026-05-12 09:00:00.123 INFO [senpi_helpers] {"event": "mcp_call", "status": "ok"}'
        out = stats.parse_log_line(line)
        self.assertIsNotNone(out)
        self.assertEqual(out["event"], "mcp_call")


class AggregateEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pin "now" to a fixed wall-clock so bucket boundaries are stable.
        self.now = datetime(2026, 5, 12, 9, 30, 0, tzinfo=timezone.utc)

    def _agg(self, events, *, window_hours=24):
        return stats.aggregate_events(events, window_hours=window_hours, now=self.now)

    def test_empty_input_returns_empty_buckets(self) -> None:
        buckets, total, earliest = self._agg([])
        self.assertEqual(total, 0)
        self.assertIsNone(earliest)
        # All buckets should still be present (one per hour in the window),
        # all zero-filled.
        self.assertGreater(len(buckets), 0)
        self.assertTrue(all(b["mcp_calls_ok"] == 0 for b in buckets))

    def test_mcp_call_ok_increments_bucket(self) -> None:
        events = [
            _event("mcp_call", iso="2026-05-12T09:15:00.000Z", status="ok"),
            _event("mcp_call", iso="2026-05-12T09:20:00.000Z", status="ok"),
        ]
        buckets, total, _ = self._agg(events)
        self.assertEqual(total, 2)
        # 09:00 bucket should have 2 ok calls.
        bucket_0900 = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T09:00:00Z")
        self.assertEqual(bucket_0900["mcp_calls_ok"], 2)
        self.assertEqual(bucket_0900["mcp_calls_failed"], 0)

    def test_mcp_call_failure_records_code(self) -> None:
        events = [
            _event("mcp_call", iso="2026-05-12T09:00:00.000Z",
                   status="http_error", code=503),
            _event("mcp_call", iso="2026-05-12T09:10:00.000Z",
                   status="network_error"),  # no code field — should fall back to status
        ]
        buckets, _, _ = self._agg(events)
        b = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T09:00:00Z")
        self.assertEqual(b["mcp_calls_failed"], 2)
        self.assertEqual(b["mcp_errors_by_code"].get("503"), 1)
        self.assertEqual(b["mcp_errors_by_code"].get("NETWORK_ERROR"), 1)

    def test_signal_post_ok_sums_batch_size(self) -> None:
        events = [
            _event("signal_post", iso="2026-05-12T09:00:00.000Z",
                   status="ok", batch_size=3),
            _event("signal_post", iso="2026-05-12T09:30:00.000Z",
                   status="ok", batch_size=1),
        ]
        buckets, _, _ = self._agg(events)
        b = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T09:00:00Z")
        self.assertEqual(b["signals_posted_ok"], 4)

    def test_signal_post_rejected_uses_failed_by_code(self) -> None:
        events = [
            _event("signal_post", iso="2026-05-12T09:00:00.000Z",
                   status="rejected", batch_size=2, failed_by_code={"INVALID_REQUEST": 2}),
            _event("signal_post", iso="2026-05-12T09:10:00.000Z",
                   status="http_error", envelope_code="UNAVAILABLE"),
        ]
        buckets, _, _ = self._agg(events)
        b = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T09:00:00Z")
        self.assertEqual(b["signals_posted_failed"], 2)  # envelope-failure count
        self.assertEqual(b["signals_errors_by_code"].get("INVALID_REQUEST"), 2)
        self.assertEqual(b["signals_errors_by_code"].get("UNAVAILABLE"), 1)

    def test_cache_hit_increments(self) -> None:
        events = [
            _event("cache_hit", iso="2026-05-12T09:00:00.000Z", tool="x"),
            _event("cache_hit", iso="2026-05-12T09:00:00.000Z", tool="y"),
            _event("cache_hit", iso="2026-05-12T08:30:00.000Z", tool="x"),
        ]
        buckets, _, _ = self._agg(events)
        b_09 = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T09:00:00Z")
        b_08 = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T08:00:00Z")
        self.assertEqual(b_09["cache_hits"], 2)
        self.assertEqual(b_08["cache_hits"], 1)

    def test_tick_finished_breakdown_by_status_and_code(self) -> None:
        events = [
            _event("daemon_tick_finished", iso="2026-05-12T09:00:00.000Z", status="ok"),
            _event("daemon_tick_finished", iso="2026-05-12T09:10:00.000Z",
                   status="error", code="SenpiClientError"),
            _event("daemon_tick_finished", iso="2026-05-12T09:20:00.000Z",
                   status="timeout", code="TICK_TIMEOUT"),
            _event("daemon_tick_finished", iso="2026-05-12T09:30:00.000Z",
                   status="skipped_locked", code="LOCK_BUSY"),
        ]
        buckets, _, _ = self._agg(events)
        b = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T09:00:00Z")
        self.assertEqual(b["ticks_by_status"]["ok"], 1)
        self.assertEqual(b["ticks_by_status"]["error"], 1)
        self.assertEqual(b["ticks_by_status"]["timeout"], 1)
        self.assertEqual(b["ticks_by_status"]["skipped_locked"], 1)
        self.assertEqual(b["ticks_errors_by_code"]["SenpiClientError"], 1)
        self.assertEqual(b["ticks_errors_by_code"]["TICK_TIMEOUT"], 1)

    def test_events_outside_window_are_dropped(self) -> None:
        events = [
            # Within window (24h before now).
            _event("mcp_call", iso="2026-05-11T15:00:00.000Z", status="ok"),
            # Just outside window — > 24h ago.
            _event("mcp_call", iso="2026-05-11T08:00:00.000Z", status="ok"),
        ]
        buckets, total, _ = self._agg(events, window_hours=24)
        self.assertEqual(total, 1)
        ok_count = sum(b["mcp_calls_ok"] for b in buckets)
        self.assertEqual(ok_count, 1)

    def test_future_events_are_dropped(self) -> None:
        events = [
            _event("mcp_call", iso="2026-05-12T15:00:00.000Z", status="ok"),  # future
            _event("mcp_call", iso="2026-05-12T09:00:00.000Z", status="ok"),  # past
        ]
        buckets, total, _ = self._agg(events, window_hours=24)
        self.assertEqual(total, 1)

    def test_earliest_event_iso_returned(self) -> None:
        events = [
            _event("mcp_call", iso="2026-05-12T09:00:00.000Z", status="ok"),
            _event("mcp_call", iso="2026-05-11T20:00:00.000Z", status="ok"),
            _event("mcp_call", iso="2026-05-12T05:00:00.000Z", status="ok"),
        ]
        _, _, earliest = self._agg(events)
        self.assertIsNotNone(earliest)
        self.assertEqual(earliest.hour, 20)
        self.assertEqual(earliest.day, 11)

    def test_unknown_event_type_ignored(self) -> None:
        events = [
            _event("retired_event_name", iso="2026-05-12T09:00:00.000Z"),
            _event("mcp_call", iso="2026-05-12T09:00:00.000Z", status="ok"),
        ]
        buckets, total, _ = self._agg(events)
        # retired_event_name doesn't count; mcp_call does.
        self.assertEqual(total, 1)

    def test_unparseable_iso_skipped(self) -> None:
        events = [
            _event("mcp_call", iso="not-a-date", status="ok"),
            _event("mcp_call", iso="2026-05-12T09:00:00.000Z", status="ok"),
        ]
        buckets, total, _ = self._agg(events)
        self.assertEqual(total, 1)

    def test_skipped_locked_does_not_count_as_error(self) -> None:
        """skipped_locked → in `ticks_by_status` only; NOT in `ticks_errors_by_code`."""
        events = [
            _event("daemon_tick_finished", iso="2026-05-12T09:00:00.000Z",
                   status="skipped_locked", code="LOCK_BUSY"),
            _event("daemon_tick_finished", iso="2026-05-12T09:00:00.000Z",
                   status="skipped_locked", code="LOCK_BUSY"),
        ]
        buckets, _, _ = self._agg(events)
        b = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T09:00:00Z")
        # Counted in by-status (it IS a real outcome — operators want to see it).
        self.assertEqual(b["ticks_by_status"]["skipped_locked"], 2)
        # NOT counted in errors-by-code — would surface false LOCK_BUSY
        # entries to operators monitoring the error histogram.
        self.assertEqual(b["ticks_errors_by_code"], {})

    def test_missing_code_collapses_to_unknown(self) -> None:
        # Tick error with no code field — must surface as UNKNOWN, not crash.
        events = [
            _event("daemon_tick_finished", iso="2026-05-12T09:00:00.000Z",
                   status="error"),  # no `code`
        ]
        buckets, _, _ = self._agg(events)
        b = next(b for b in buckets if b["hour_start_iso"] == "2026-05-12T09:00:00Z")
        # Falls back to status string when code is missing.
        self.assertIn("error", b["ticks_errors_by_code"])


class AggregateLogFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="senpi-stats-")
        self.log_path = os.path.join(self.tmp, "test.log")
        # Fixed now so the test events fall inside the window.
        self.now = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_log(self, lines):
        with open(self.log_path, "w") as f:
            for line in lines:
                f.write(line + "\n")

    def test_round_trip_mixed_events(self) -> None:
        self._write_log([
            'unrelated startup log',  # ignored
            '[senpi_helpers] {"event": "mcp_call", "iso": "2026-05-12T09:00:00.000Z", "status": "ok"}',
            '[senpi_helpers] {"event": "mcp_call", "iso": "2026-05-12T09:05:00.000Z", "status": "http_error", "code": 503}',
            '[senpi_helpers] {"event": "cache_hit", "iso": "2026-05-12T09:30:00.000Z", "tool": "x"}',
            '[senpi_helpers] {"event": "signal_post", "iso": "2026-05-12T09:45:00.000Z", "status": "ok", "batch_size": 1}',
            '[senpi_helpers] {"event": "daemon_tick_finished", "iso": "2026-05-12T09:55:00.000Z", "status": "ok"}',
            '[senpi_helpers] garbage that does not parse',
        ])
        payload = stats.aggregate_log_file(self.log_path, window_hours=24, now=self.now)
        self.assertEqual(payload["log_path"], self.log_path)
        self.assertEqual(payload["window_hours"], 24)
        self.assertEqual(payload["total_events_counted"], 5)
        self.assertEqual(payload["totals"]["mcp_calls_ok"], 1)
        self.assertEqual(payload["totals"]["mcp_calls_failed"], 1)
        self.assertEqual(payload["totals"]["mcp_errors_by_code"]["503"], 1)
        self.assertEqual(payload["totals"]["cache_hits"], 1)
        self.assertEqual(payload["totals"]["signals_posted_ok"], 1)
        self.assertEqual(payload["totals"]["ticks_by_status"]["ok"], 1)
        self.assertIsNotNone(payload["earliest_event_iso"])
        self.assertGreater(payload["log_size_bytes"], 0)

    def test_missing_file_raises_filenotfounderror(self) -> None:
        with self.assertRaises(FileNotFoundError):
            stats.aggregate_log_file("/no/such/file.log", window_hours=24)

    def test_non_helper_lines_are_skipped(self) -> None:
        self._write_log([
            'random log line 1',
            'random log line 2',
            'random log line 3',
        ])
        payload = stats.aggregate_log_file(self.log_path, window_hours=24, now=self.now)
        self.assertEqual(payload["total_events_counted"], 0)
        # Buckets are still populated (zero-filled).
        self.assertGreater(len(payload["buckets"]), 0)

    def test_non_utf8_bytes_do_not_crash(self) -> None:
        with open(self.log_path, "wb") as f:
            f.write(b'\xff\xfe garbage bytes\n')
            f.write(b'[senpi_helpers] {"event": "mcp_call", "iso": "2026-05-12T09:00:00.000Z", "status": "ok"}\n')
        payload = stats.aggregate_log_file(self.log_path, window_hours=24, now=self.now)
        self.assertEqual(payload["totals"]["mcp_calls_ok"], 1)


class BucketBoundaryTests(unittest.TestCase):
    def test_hour_bucket_iso_floors_to_hour(self) -> None:
        dt = datetime(2026, 5, 12, 9, 45, 30, 123000, tzinfo=timezone.utc)
        self.assertEqual(stats._hour_bucket_iso(dt), "2026-05-12T09:00:00Z")

    def test_hour_bucket_iso_handles_midnight(self) -> None:
        dt = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(stats._hour_bucket_iso(dt), "2026-05-12T00:00:00Z")

    def test_parse_event_ts_handles_naive_iso(self) -> None:
        # Legacy ISO without timezone — must be interpreted as UTC.
        dt = stats._parse_event_ts("2026-05-12T09:00:00.000")
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_parse_event_ts_invalid(self) -> None:
        self.assertIsNone(stats._parse_event_ts(None))
        self.assertIsNone(stats._parse_event_ts(""))
        self.assertIsNone(stats._parse_event_ts("not-a-date"))


if __name__ == "__main__":
    unittest.main()
