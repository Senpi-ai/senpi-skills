#!/usr/bin/env python3
"""Hermetic unit tests for the deploy liveness + budget gates.

No MCP, no openclaw, no network — every input is a plain dict/stub. Run:
    python3 senpi-strategy-ops/tests/test_deploy_gates.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _pkg    # noqa: E402
import deploy  # noqa: E402


def _inst(name, share):
    return types.SimpleNamespace(name=name, funding_share=share)


def _dsl_inst(runtime_doc):
    """A bare _pkg.Instance with only runtime_doc set — enough for the exit_block/has_dsl properties."""
    i = _pkg.Instance.__new__(_pkg.Instance)
    i.runtime_doc = runtime_doc
    return i


def _scan_inst():
    return types.SimpleNamespace(external_scanner={"name": "sc1"}, interval_seconds=300)


class PlanFunding(unittest.TestCase):
    def test_fits(self):
        amounts, short = deploy.plan_funding([_inst("a", 0.6), _inst("b", 0.4)], 1000, 1100)
        self.assertEqual(amounts, {"a": 600.0, "b": 400.0})
        self.assertIsNone(short)

    def test_incident_single_wallet_underfunded(self):
        # the M-incident: user asked $1000, only $100 available → HALT, never silently fund the floor
        _amounts, short = deploy.plan_funding([_inst("only", 1.0)], 1000, 100)
        self.assertIsNotNone(short)
        self.assertEqual(short["requested"], 1000.0)
        self.assertGreater(short["short_by"], 800)

    def test_floor_covered_by_balance_is_not_short(self):
        # $200 across 60/40 floors the small leg to $100 → $220 total; $300 available covers it, no halt
        amounts, short = deploy.plan_funding([_inst("a", 0.6), _inst("b", 0.4)], 200, 300)
        self.assertEqual(amounts, {"a": 120.0, "b": 100.0})
        self.assertIsNone(short)

    def test_floor_over_balance_is_short(self):
        _amounts, short = deploy.plan_funding([_inst("a", 0.6), _inst("b", 0.4)], 200, 200)
        self.assertIsNotNone(short)

    def test_available_unknown_never_halts(self):
        _amounts, short = deploy.plan_funding([_inst("a", 1.0)], 1000, None)
        self.assertIsNone(short)


class HasDsl(unittest.TestCase):
    def test_full_preset(self):
        self.assertTrue(_dsl_inst({"exit": {"engine": "dsl", "dsl_preset": {"phase1": {}}}}).has_dsl)

    def test_engine_only(self):
        self.assertTrue(_dsl_inst({"exit": {"engine": "dsl"}}).has_dsl)

    def test_preset_only(self):
        self.assertTrue(_dsl_inst({"exit": {"dsl_preset": {"phase1": {}}}}).has_dsl)

    def test_empty_exit_is_naked(self):
        self.assertFalse(_dsl_inst({"exit": {}}).has_dsl)

    def test_no_exit_is_naked(self):
        self.assertFalse(_dsl_inst({}).has_dsl)

    def test_non_dsl_engine_no_preset_is_naked(self):
        self.assertFalse(_dsl_inst({"exit": {"engine": "none"}}).has_dsl)


def _status_with_scanner(name, health):
    """A minimal `senpi status` (getHealthStatus) entry carrying one scanner's health verdict."""
    return {"components": {"scanners": {"scanners": [{"scannerId": name, "health": health}]}}}


class ScannerVerdict(unittest.TestCase):
    # --- state readable: the rich per-scanner row drives the verdict ---
    def test_ticked(self):
        st = {"scanners": [{"name": "sc1", "runCount": 5, "enabled": True}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "ticked")

    def test_scheduled(self):
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": True}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "scheduled")

    def test_heartbeat_without_runcount_is_ticked(self):
        # external scanner that has POSTed (barren, no-signal heartbeat) but runCount still 0
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": True, "lastAliveAt": 1784740000000}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "ticked")

    def test_disabled_is_broken(self):
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": False}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "broken")

    def test_erroring_is_broken(self):
        st = {"scanners": [{"name": "sc1", "runCount": 3, "lastError": "boom"}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "broken")

    def test_unhealthy_health_field_is_broken(self):
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": True, "health": "unhealthy"}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "broken")

    # --- state UNreadable: fall back to `senpi status`, never fail closed ---
    def test_state_none_status_healthy_is_live(self):
        # THE regression: getSystemState threw, but status says the scanner is healthy → live, not broken
        status = _status_with_scanner("sc1", "healthy")
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), None, status)[0], "ticked")

    def test_state_none_status_unhealthy_is_broken(self):
        status = _status_with_scanner("sc1", "unhealthy")
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), None, status)[0], "broken")

    def test_state_none_status_none_is_supervised(self):
        # BOTH reads flaky-empty — but the caller only calls this after confirming the runtime is
        # RUNNING (via `runtime list`), and the runtime supervises the declared scanner → live, not broken
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), None, None)[0], "supervised")

    def test_absent_from_state_and_status_is_supervised_not_broken(self):
        # the old false 'scanner not mounted' path — now live-but-unmeasured (runtime running + supervised)
        st = {"scanners": []}
        status = {"components": {"scanners": {"scanners": []}}}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, status)[0], "supervised")


class DslVerdict(unittest.TestCase):
    def test_config_missing(self):
        v = deploy._dsl_verdict(types.SimpleNamespace(has_dsl=False), {"dsl": {"enabled": True}})
        self.assertEqual(v[0], "config-missing")

    def test_wired_when_status_unreadable(self):
        self.assertEqual(deploy._dsl_verdict(types.SimpleNamespace(has_dsl=True), None)[0], "wired")

    def test_monitor_down(self):
        v = deploy._dsl_verdict(types.SimpleNamespace(has_dsl=True), {"dsl": {"enabled": False}})
        self.assertEqual(v[0], "monitor-down")

    def test_wired(self):
        v = deploy._dsl_verdict(types.SimpleNamespace(has_dsl=True), {"dsl": {"enabled": True}})
        self.assertEqual(v[0], "wired")


class BudgetVerdict(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(deploy._budget_verdict({"requested": 1000, "strategyId": "x"}, {"x": 1000.0})[0], "ok")

    def test_underfunded(self):
        self.assertEqual(deploy._budget_verdict({"requested": 1000, "strategyId": "x"}, {"x": 100.0})[0], "underfunded")

    def test_no_requested_is_ok(self):
        self.assertEqual(deploy._budget_verdict({"strategyId": "x"}, {"x": 100.0})[0], "ok")

    def test_funded_unreadable_is_ok(self):
        self.assertEqual(deploy._budget_verdict({"requested": 1000, "strategyId": "x"}, {})[0], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
