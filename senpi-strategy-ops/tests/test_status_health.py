#!/usr/bin/env python3
"""Hermetic tests for the status.py health taxonomy (no MCP, no openclaw).

Pins the fail-closed contract: the runtime's `unknown` verdict (scanner not yet proven
by a tick) must survive _cli.health_verdict verbatim — coercing it to None lets the
caller's "running" fallback paint an unproven runtime ✅. Run:
    python3 senpi-strategy-ops/tests/test_status_health.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
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

    def test_unrecognized_is_none(self):
        self.assertIsNone(_cli.health_verdict({"health": "something-new"}))
        self.assertIsNone(_cli.health_verdict({}))


class TestStatusBuckets(unittest.TestCase):
    def test_unknown_class_is_rendered(self):
        # Every health class status.py can assign must have an icon — a missing entry
        # renders a blank and drops the row from every summary bucket.
        for cls in ("healthy", "running", "degraded", "unhealthy", "unknown",
                    "runtime-stopped", "no-runtime", "runtime-unknown", "copy", "manual"):
            self.assertIn(cls, status._ICON, f"no icon for health class {cls!r}")


if __name__ == "__main__":
    unittest.main()
