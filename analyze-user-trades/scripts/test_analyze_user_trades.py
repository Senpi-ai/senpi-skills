#!/usr/bin/env python3
"""
Tests for analyze-user-trades skill.

Run from the skill root:
  python3 scripts/test_analyze_user_trades.py

No external dependencies required — all MCP calls are mocked.
"""

import json
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# ── Import modules under test ──────────────────────────────────────────────
sys.path.insert(0, "scripts")
from analyze_user_trades_config import compute_week_boundaries
import analyze_user_trades as aut


# ── Fixtures ───────────────────────────────────────────────────────────────

WEEK_START = "2026-04-09T00:00:00+00:00"
WEEK_END   = "2026-04-15T23:59:59+00:00"

STRATEGY = {
    "strategyWalletId":      "strat-1",
    "strategyWalletAddress": "0xABC",
    "status":                "ACTIVE",
    "skillName":             "wolf-strategy",
    "createdAt":             "2026-04-01T00:00:00Z",
}

POSITION_IN_RANGE = {
    "coin": "BTC", "entryPx": "80000", "exitPx": "85000",
    "leverage": "5", "openTime": "2026-04-10T10:00:00Z",
    "closeTime": "2026-04-10T14:00:00Z",
    "szi": "0.1", "realizedPnl": "500", "totalFees": "12",
}
POSITION_OUT_OF_RANGE = {**POSITION_IN_RANGE, "closeTime": "2026-04-01T00:00:00Z"}

AUDIT_IN_RANGE = {
    "toolName": "create_position", "aiReasoning": "strong momentum",
    "timestamp": "2026-04-10T10:00:00Z",
}
AUDIT_OUT_OF_RANGE = {**AUDIT_IN_RANGE, "timestamp": "2026-04-01T00:00:00Z"}


def _args(**kwargs):
    """Build a mock argparse Namespace."""
    defaults = dict(username=None, user_id=None, top_n=None,
                    start_time=None, end_time=None)
    defaults.update(kwargs)
    return MagicMock(**defaults)


# ── compute_week_boundaries ────────────────────────────────────────────────

class TestComputeWeekBoundaries(unittest.TestCase):

    def _now(self, iso):
        return datetime.fromisoformat(iso)

    def test_week1_anchor(self):
        """During week 1 the boundaries equal the anchor week."""
        start, end = compute_week_boundaries(_now=self._now("2026-03-28T12:00:00+00:00"))
        self.assertEqual(start, "2026-03-26T00:00:00+00:00")
        self.assertEqual(end,   "2026-04-01T23:59:59+00:00")

    def test_week3(self):
        """Week 3 (Apr 9–15) computes correct boundaries."""
        start, end = compute_week_boundaries(_now=self._now("2026-04-13T00:00:00+00:00"))
        self.assertEqual(start, "2026-04-09T00:00:00+00:00")
        self.assertEqual(end,   "2026-04-15T23:59:59+00:00")

    def test_negative_offset(self):
        """week_offset=-1 returns the previous week."""
        start, end = compute_week_boundaries(
            week_offset=-1, _now=self._now("2026-04-13T00:00:00+00:00")
        )
        self.assertEqual(start, "2026-04-02T00:00:00+00:00")
        self.assertEqual(end,   "2026-04-08T23:59:59+00:00")


# ── resolve_users ──────────────────────────────────────────────────────────

class TestResolveUsers(unittest.TestCase):

    @patch("analyze_user_trades.mcporter_call", return_value={"userId": "M123"})
    def test_username_resolves(self, _):
        users, err = aut.resolve_users(_args(username="alice"))
        self.assertIsNone(err)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["senpiUserId"], "M123")
        self.assertEqual(users[0]["senpiUserName"], "alice")
        self.assertIsNone(users[0]["rank"])

    @patch("analyze_user_trades.mcporter_call", return_value={})
    def test_username_not_found(self, _):
        users, err = aut.resolve_users(_args(username="ghost"))
        self.assertIsNone(users)
        self.assertIn("ghost", err)

    def test_user_id_passthrough(self):
        users, err = aut.resolve_users(_args(user_id="M999"))
        self.assertIsNone(err)
        self.assertEqual(users[0]["senpiUserId"], "M999")
        self.assertIsNone(users[0]["senpiUserName"])
        self.assertIsNone(users[0]["rank"])

    @patch("analyze_user_trades.mcporter_call", return_value={
        "leaderboard": [
            {"senpiUserId": "M1", "senpiUserName": "alice", "rank": 1,
             "roePct": "42.5", "totalPnl": "1250"},
            {"senpiUserId": "M2", "senpiUserName": "bob",   "rank": 2,
             "roePct": "30.1", "totalPnl": "800"},
        ]
    })
    def test_top_n_populates_rank_fields(self, _):
        users, err = aut.resolve_users(_args(top_n=2))
        self.assertIsNone(err)
        self.assertEqual(len(users), 2)
        self.assertEqual(users[0]["rank"], 1)
        self.assertEqual(users[0]["roePct"], "42.5")
        self.assertEqual(users[1]["senpiUserId"], "M2")

    @patch("analyze_user_trades.mcporter_call", return_value=None)
    def test_top_n_empty_response(self, _):
        users, err = aut.resolve_users(_args(top_n=5))
        self.assertIsNone(users)
        self.assertIsNotNone(err)


# ── fetch_strategies ───────────────────────────────────────────────────────

class TestFetchStrategies(unittest.TestCase):

    @patch("analyze_user_trades.mcporter_call_safe",
           return_value={"strategies": [STRATEGY]})
    def test_maps_fields(self, _):
        result = aut.fetch_strategies("M123")
        self.assertEqual(len(result), 1)
        s = result[0]
        self.assertEqual(s["strategyId"], "strat-1")
        self.assertEqual(s["address"],    "0xABC")
        self.assertEqual(s["skillName"],  "wolf-strategy")

    @patch("analyze_user_trades.mcporter_call_safe", return_value=None)
    def test_empty_on_failure(self, _):
        self.assertEqual(aut.fetch_strategies("M123"), [])

    @patch("analyze_user_trades.mcporter_call_safe",
           return_value={"strategies": []})
    def test_empty_strategies_list(self, _):
        self.assertEqual(aut.fetch_strategies("M123"), [])


# ── fetch_orders ───────────────────────────────────────────────────────────

class TestFetchOrders(unittest.TestCase):

    @patch("analyze_user_trades.mcporter_call_safe", return_value={
        "closedPositions": [POSITION_IN_RANGE, POSITION_OUT_OF_RANGE]
    })
    def test_filters_by_close_time(self, _):
        orders = aut.fetch_orders("0xABC", WEEK_START, WEEK_END)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["coin"], "BTC")
        self.assertEqual(orders[0]["realizedPnl"], "500")

    @patch("analyze_user_trades.mcporter_call_safe", return_value={
        "closedPositions": [POSITION_OUT_OF_RANGE]
    })
    def test_all_filtered_out(self, _):
        self.assertEqual(aut.fetch_orders("0xABC", WEEK_START, WEEK_END), [])

    @patch("analyze_user_trades.mcporter_call_safe", return_value=None)
    def test_empty_on_failure(self, _):
        self.assertEqual(aut.fetch_orders("0xABC", WEEK_START, WEEK_END), [])

    @patch("analyze_user_trades.mcporter_call_safe", return_value={
        "closedPositions": [POSITION_IN_RANGE]
    })
    def test_output_fields(self, _):
        orders = aut.fetch_orders("0xABC", WEEK_START, WEEK_END)
        self.assertIn("entryPx",     orders[0])
        self.assertIn("exitPx",      orders[0])
        self.assertIn("leverage",    orders[0])
        self.assertIn("totalFees",   orders[0])


# ── fetch_audit_logs ───────────────────────────────────────────────────────

class TestFetchAuditLogs(unittest.TestCase):

    @patch("analyze_user_trades.mcporter_call_safe", return_value={
        "auditLogs": [AUDIT_IN_RANGE, AUDIT_OUT_OF_RANGE]
    })
    def test_client_side_time_filter(self, _):
        logs = aut.fetch_audit_logs("M123", WEEK_START, WEEK_END)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["tool"],         "create_position")
        self.assertEqual(logs[0]["ai_reasoning"], "strong momentum")

    @patch("analyze_user_trades.mcporter_call_safe", return_value={
        "auditLogs": [AUDIT_OUT_OF_RANGE]
    })
    def test_all_filtered_out(self, _):
        self.assertEqual(aut.fetch_audit_logs("M123", WEEK_START, WEEK_END), [])

    @patch("analyze_user_trades.mcporter_call_safe", return_value=None)
    def test_empty_on_failure(self, _):
        self.assertEqual(aut.fetch_audit_logs("M123", WEEK_START, WEEK_END), [])

    @patch("analyze_user_trades.mcporter_call_safe", return_value={"auditLogs": []})
    def test_no_start_time_on_call(self, mock_call):
        """Confirm start_time/end_time are NOT forwarded to the MCP tool."""
        aut.fetch_audit_logs("M123", WEEK_START, WEEK_END)
        call_kwargs = mock_call.call_args
        # The only kwarg sent to the tool should be user_ids
        args_sent = call_kwargs[1] if call_kwargs[1] else {}
        self.assertNotIn("start_time", args_sent)
        self.assertNotIn("end_time",   args_sent)


# ── output contract ────────────────────────────────────────────────────────

class TestOutputContract(unittest.TestCase):
    """Run the script as a subprocess and assert the output envelope."""

    def _run(self, *argv):
        import subprocess
        result = subprocess.run(
            ["python3", "scripts/analyze_user_trades.py"] + list(argv),
            capture_output=True, text=True,
        )
        return json.loads(result.stdout)

    def test_error_envelope_on_unknown_user(self):
        """Unknown username → structured error, not a crash."""
        with patch("analyze_user_trades.mcporter_call", return_value={}):
            with patch("analyze_user_trades.resolve_users",
                       return_value=(None, "No Senpi user found with username 'nobody'")):
                # Exercise main() directly to avoid subprocess env complexity
                sys.argv = ["analyze_user_trades.py", "--username", "nobody"]
                out_lines = []
                with patch("builtins.print", side_effect=out_lines.append):
                    try:
                        aut.main()
                    except SystemExit:
                        pass
                output = json.loads(out_lines[0])
        self.assertFalse(output["success"])
        self.assertIn("error",      output)
        self.assertIn("actionable", output)

    def test_success_envelope_shape(self):
        """Happy path returns correct top-level keys."""
        with patch("analyze_user_trades.resolve_users",
                   return_value=([{"senpiUserId": "M1", "senpiUserName": "alice",
                                   "rank": None, "roePct": None, "totalPnl": None}], None)), \
             patch("analyze_user_trades.fetch_strategies", return_value=[]), \
             patch("analyze_user_trades.compute_week_boundaries",
                   return_value=(WEEK_START, WEEK_END)):
            sys.argv = ["analyze_user_trades.py", "--username", "alice"]
            out_lines = []
            with patch("builtins.print", side_effect=out_lines.append):
                aut.main()
            output = json.loads(out_lines[0])

        self.assertTrue(output["success"])
        self.assertIn("startTime", output)
        self.assertIn("endTime",   output)
        self.assertIn("results",   output)
        self.assertEqual(len(output["results"]), 1)
        r = output["results"][0]
        self.assertIn("senpiUserId",   r)
        self.assertIn("senpiUserName", r)
        self.assertIn("strategies",    r)


# ── entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
