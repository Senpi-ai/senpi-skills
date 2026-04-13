import unittest
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_user_trades


class TimeFilteringTests(unittest.TestCase):
    def test_build_time_filter_includes_fractional_start_second(self):
        in_range = analyze_user_trades._build_time_filter(
            "2026-04-03T00:00:00Z",
            "2026-04-09T23:59:59Z",
        )

        self.assertTrue(in_range("2026-04-03T00:00:00.500Z"))
        self.assertFalse(in_range("2026-04-02T23:59:59.999Z"))

    def test_build_time_filter_includes_fractional_end_second(self):
        in_range = analyze_user_trades._build_time_filter(
            "2026-04-03T00:00:00Z",
            "2026-04-09T23:59:59Z",
        )

        self.assertTrue(in_range("2026-04-09T23:59:59.500Z"))
        self.assertFalse(in_range("2026-04-10T00:00:00Z"))

    @patch("analyze_user_trades.mcporter_call_safe")
    def test_fetch_orders_filters_with_parsed_timestamps(self, mcporter_call_safe):
        mcporter_call_safe.return_value = {
            "closedPositions": [
                {"coin": "BTC", "closeTime": "2026-04-03T00:00:00.500Z"},
                {"coin": "ETH", "closeTime": "2026-04-02T23:59:59.500Z"},
            ]
        }

        orders = analyze_user_trades.fetch_orders(
            "0xabc",
            "2026-04-03T00:00:00Z",
            "2026-04-09T23:59:59Z",
        )

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["coin"], "BTC")

    @patch("analyze_user_trades.mcporter_call_safe")
    def test_fetch_audit_logs_filters_with_parsed_timestamps(self, mcporter_call_safe):
        mcporter_call_safe.return_value = {
            "auditLogs": [
                {"toolName": "t1", "timestamp": "2026-04-03T00:00:00.500Z"},
                {"toolName": "t2", "timestamp": "2026-04-02T23:59:59.999Z"},
            ]
        }

        logs = analyze_user_trades.fetch_audit_logs(
            "strategy-1",
            "2026-04-03T00:00:00Z",
            "2026-04-09T23:59:59Z",
        )

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["tool"], "t1")


if __name__ == "__main__":
    unittest.main()
