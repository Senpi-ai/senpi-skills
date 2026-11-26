#!/usr/bin/env python3
"""Hermetic tests for `status.py --recent` (no MCP, no openclaw).

The agent asks "go live now, or simulate a few options first?" when a user's recent strategies went
straight live, so the count must hold only what says how they choose to start: package strategies
started live since simulations existed, split by whether each was taken live from a simulation. Run:
    python3 senpi-strategy-ops/tests/test_status_recent.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import status  # noqa: E402

NOW = datetime(2026, 12, 20, tzinfo=timezone.utc)


def _row(sid, created, skill="hawk", status_="ACTIVE", trader=None, from_sim=None):
    row = {"strategyId": sid, "status": status_, "createdAt": created,
           "strategyWalletAddress": "0x" + sid.rjust(40, "0"), "strategyMetadata": {"skillName": skill}}
    if trader:
        row["traderAddress"] = trader
    if from_sim:
        row["identity"] = {"group": None, "variant": None, "fromSimulationId": from_sim}
    return row


class _FakeMCP:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, []

    def mcp_call(self, name, timeout=None, **kw):
        self.calls.append((name, kw))
        if self.fail:
            raise RuntimeError("gateway timeout")
        return {"success": True, "data": {"strategies": self.rows}}


class TestRecent(unittest.TestCase):
    def test_counts_live_starts_since_simulations_and_those_from_a_simulation(self):
        mcp = _FakeMCP([
            _row("a1", "2026-12-15T10:00:00.000Z"),                                   # straight live
            _row("a2", "2026-12-18T10:00:00.000Z", from_sim="simulation-2e21048d"),   # from a simulation
            _row("a3", "2026-12-05T10:00:00.000Z", status_="CLOSED"),                 # closed still counts
            _row("old", "2026-11-28T10:00:00.000Z"),                  # before simulations existed
            _row("copy", "2026-12-16T10:00:00.000Z", trader="0x" + "1" * 40),        # copy-trading
            _row("manual", "2026-12-16T10:00:00.000Z", skill=""),                    # no package
            _row("nodate", None),                                                    # unreadable date
        ])
        r = status.recent(mcp, days=30, now=NOW)
        self.assertEqual(r, {"since": "2026-12-01", "live_started": 3, "from_simulation": 1})
        # Every status: no server-side status filter on this read.
        self.assertEqual(mcp.calls, [("strategy_list", {})])

    def test_the_window_is_the_later_of_days_ago_and_the_launch(self):
        mcp = _FakeMCP([_row("a1", "2026-12-12T00:00:00.000Z"), _row("a2", "2026-12-08T00:00:00.000Z")])
        r = status.recent(mcp, days=10, now=NOW)
        self.assertEqual(r["since"], "2026-12-10")
        self.assertEqual(r["live_started"], 1)

    def test_an_unread_list_is_none_with_its_cause_never_zero(self):
        why = []
        self.assertIsNone(status.recent(_FakeMCP(fail=True), now=NOW, why=why))
        self.assertIn("gateway timeout", why[0])

    def test_cli_prints_one_line_and_json(self):
        orig = status.MCPClient
        status.MCPClient = lambda: _FakeMCP([_row("a1", datetime.now(timezone.utc).isoformat())])
        orig_since = status.SIMULATIONS_SINCE
        status.SIMULATIONS_SINCE = "2026-01-01"
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(status.main(["status.py", "--recent"]), 0)
            self.assertRegex(out.getvalue(), r"^Since \d{4}-\d{2}-\d{2}: 1 strategy started live, "
                                             r"0 of them from a simulation\.\n$")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status.main(["status.py", "--recent", "--json"])
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["world"], "live")
            self.assertEqual(payload["recent"]["live_started"], 1)
        finally:
            status.MCPClient, status.SIMULATIONS_SINCE = orig, orig_since


if __name__ == "__main__":
    unittest.main()
