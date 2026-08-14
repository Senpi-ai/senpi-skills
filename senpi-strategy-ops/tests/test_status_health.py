#!/usr/bin/env python3
"""Hermetic tests for the status.py health taxonomy (no MCP, no openclaw).

Pins the fail-closed contract: any PRESENT verdict _cli.health_verdict cannot classify as
healthy/broken — the runtime's `unknown` (scanner not yet proven by a tick), `disabled`,
future vocabulary — must map to "unknown", never None: None lets the caller's "running"
fallback paint an unproven runtime ✅. None is only for payloads with no health field. Run:
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


class TestScannerVerdictUnwired(unittest.TestCase):
    def test_unwired_health_payload_brands_broken(self):
        # A running-but-blind runtime (health payload scanners.unwired) must never fall
        # through to `supervised` = live — there are no per-scanner rows to downgrade on.
        import types
        import deploy
        inst = types.SimpleNamespace(external_scanner={"name": "x_signals"}, interval_seconds=60)
        payload = {"components": {"scanners": {"unwired": True, "unwiredPhase": "mount"}}}
        st, detail = deploy._scanner_verdict(inst, None, payload)
        self.assertEqual(st, "broken")
        self.assertIn("mount", detail)

    def test_wired_payload_unaffected(self):
        import types
        import deploy
        inst = types.SimpleNamespace(external_scanner={"name": "x_signals"}, interval_seconds=60)
        payload = {"components": {"scanners": {"unwired": False}}}
        st, _ = deploy._scanner_verdict(inst, None, payload)
        self.assertNotEqual(st, "broken")


class TestStatusBuckets(unittest.TestCase):
    def test_every_health_class_is_rendered(self):
        # Every health class status.py can assign must have an icon — a missing entry
        # renders a blank and drops the row from every summary bucket.
        for cls in ("healthy", "running", "degraded", "unhealthy", "unknown", "no-entry-scanners",
                    "runtime-stopped", "no-runtime", "runtime-unknown", "copy", "manual"):
            self.assertIn(cls, status._ICON, f"no icon for health class {cls!r}")


if __name__ == "__main__":
    unittest.main()
