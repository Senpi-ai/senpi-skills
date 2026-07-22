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


class ScannerVerdict(unittest.TestCase):
    def test_ticked(self):
        st = {"scanners": [{"name": "sc1", "runCount": 5, "enabled": True}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st)[0], "ticked")

    def test_scheduled(self):
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": True}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st)[0], "scheduled")

    def test_disabled_is_broken(self):
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": False}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st)[0], "broken")

    def test_erroring_is_broken(self):
        st = {"scanners": [{"name": "sc1", "runCount": 3, "lastError": "boom"}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st)[0], "broken")

    def test_not_mounted_is_broken(self):
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), {"scanners": []})[0], "broken")

    # ── the REAL `openclaw senpi state --json` shape (verified in prod: M408027's lion deploy) ──
    # nested states[].components.scanners.state.scanners[], keyed by `scannerId` + `health`, NOT `name`.
    # Before the scannerId fix, verify reported a healthy deployed scanner as "not mounted" (false broken).
    @staticmethod
    def _prod_state(run_count, health="healthy"):
        return {"states": [{"components": {"scanners": {"state": {"scanners": [
            {"scannerId": "position_tracker", "runCount": 56, "health": "healthy"},
            {"scannerId": "sc1", "runCount": run_count, "health": health},
        ]}}}}]}

    def test_prod_schema_ticked(self):
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), self._prod_state(3))[0], "ticked")

    def test_prod_schema_scheduled(self):
        # lion-short's case: healthy scanner, runCount 0 (slow first tick) = scheduled = LIVE, not broken
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), self._prod_state(0))[0], "scheduled")

    def test_prod_schema_unhealthy_is_broken(self):
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), self._prod_state(0, "broken"))[0], "broken")


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
