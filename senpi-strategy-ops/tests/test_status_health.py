#!/usr/bin/env python3
"""Hermetic tests for the status.py health taxonomy (no MCP, no openclaw).

Pins the fail-closed contract: any PRESENT verdict _cli.health_verdict cannot classify as
healthy/broken — the runtime's `unknown` (scanner not yet proven by a tick), `disabled`,
future vocabulary — must map to "unknown", never None: None lets the caller's "running"
fallback paint an unproven runtime ✅. None is only for payloads with no health field. Run:
    python3 senpi-strategy-ops/tests/test_status_health.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli    # noqa: E402
import status  # noqa: E402


class TestHealthVerdict(unittest.TestCase):
    def test_unknown_passes_through(self):
        self.assertEqual(_cli.health_verdict({"health": "unknown"}), "unknown")

    def test_unknown_is_not_coerced_to_none(self):
        # None would trigger status.py's `or "running"` fallback — the fail-open path.
        self.assertIsNotNone(_cli.health_verdict({"overallHealth": "unknown"}))

    def test_known_verdicts(self):
        self.assertEqual(_cli.health_verdict({"health": "healthy"}), "healthy")
        self.assertEqual(_cli.health_verdict({"health": "degraded"}), "degraded")
        self.assertEqual(_cli.health_verdict({"health": "unhealthy"}), "unhealthy")

    def test_disabled_is_not_coerced_to_none(self):
        # `disabled` is real runtime vocabulary (ComponentHealth): every component disabled.
        # None would trigger the `or "running"` fallback — a fully-disabled runtime painted ✅.
        self.assertEqual(_cli.health_verdict({"overallHealth": "disabled"}), "unknown")

    def test_unrecognized_present_verdict_is_unknown(self):
        # Fail-closed against future vocabulary: a verdict we can't classify is unproven, not green.
        self.assertEqual(_cli.health_verdict({"health": "something-new"}), "unknown")

    def test_absent_health_field_is_none(self):
        # None is reserved for payloads with NO health field — the caller's "running" fallback
        # is only correct when the runtime said nothing at all.
        self.assertIsNone(_cli.health_verdict({}))
        self.assertIsNone(_cli.health_verdict({"positions": 3}))


class TestRuntimeRunning(unittest.TestCase):
    def test_no_entry_scanners_is_running(self):
        # "running — NO ENTRY SCANNERS" is a RUNNING runtime with a wiring failure. Reading it
        # as stopped would send deploy.py's create down the close-and-recreate path on a live
        # runtime (the Bugbot finding on PR #505).
        self.assertTrue(_cli.runtime_running({"status": "running — NO ENTRY SCANNERS"}))
        self.assertTrue(_cli.runtime_running({"status": "running"}))
        self.assertFalse(_cli.runtime_running({"status": "stopped"}))

    def test_no_entry_scanners_predicate(self):
        self.assertTrue(_cli.runtime_no_entry_scanners({"status": "running — NO ENTRY SCANNERS"}))
        self.assertFalse(_cli.runtime_no_entry_scanners({"status": "running"}))
        self.assertFalse(_cli.runtime_no_entry_scanners({}))


class TestStatusBuckets(unittest.TestCase):
    def test_every_health_class_is_rendered(self):
        # Every health class status.py can assign must have an icon — a missing entry
        # renders a blank and drops the row from every summary bucket.
        for cls in ("healthy", "running", "degraded", "unhealthy", "unknown", "no-entry-scanners",
                    "runtime-stopped", "no-runtime", "runtime-unknown", "copy", "manual"):
            self.assertIn(cls, status._ICON, f"no icon for health class {cls!r}")


if __name__ == "__main__":
    unittest.main()


class TriageCommandsAreReadOnly(unittest.TestCase):
    """status.py is where SKILL.md now sends an agent to MONITOR, so nothing it prints per row may be a
    command that funds, installs or starts trading. `deploy.py verify <pkg>` is exactly that command."""

    def setUp(self):
        self._build, self._mcp = status.build, status.MCPClient

    def tearDown(self):
        status.build, status.MCPClient = self._build, self._mcp

    @staticmethod
    def _row(health):
        return {"package": "spider", "is_pkg": True, "strategyId": "str-1234abcd",
                "wallet": "0x1234567890abcdef", "status": "ACTIVE", "funded": "$300.00",
                "positions": 0, "runtime": "spider-main", "health": health}

    def _render(self, health):
        status.MCPClient = lambda *a, **k: None
        status.build = lambda *a, **k: ([self._row(health)], [], True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status.main(["status.py"])
        return out.getvalue()

    def test_a_degraded_row_emits_read_only_triage_only(self):
        text = self._render("degraded")
        self.assertNotIn("deploy.py verify spider", text)   # would install + start trading
        self.assertIn("openclaw senpi scanner -r spider-main", text)

    def test_an_unknown_row_emits_read_only_checks_only(self):
        text = self._render("unknown")
        self.assertNotIn("deploy.py verify spider", text)
        self.assertIn("openclaw senpi scanner -r spider-main", text)

    def test_the_resume_escape_is_named_once_and_says_it_moves_money(self):
        text = self._render("degraded")
        self.assertIn("deploy.py verify <id>", text)        # named as the escape, not per row
        self.assertIn("can move money", text)
