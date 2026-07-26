#!/usr/bin/env python3
"""Hen unit tests — the shared clock engine + the one thing Hen adds over Rooster: a
TRADING-DAY gate, so a weekend open (which equities do not have) positions into nothing.

The gate is the whole reason Hen is not just "Rooster with a different asset list": cloning
Rooster's clock verbatim onto equities would fire at 12:45 UTC on a Saturday, into an open
that never happens. These tests pin that it does not.

Run: python3 strategies/hen/tests/test_engine.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import calendar
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load(strategy, module):
    d = ROOT / "strategies" / strategy / "main" / "scanners"
    sys.path.insert(0, str(d))
    try:
        sys.modules.pop("scoring", None)
        spec = importlib.util.spec_from_file_location(f"{strategy}_{module}", d / f"{module}.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.pop(0)


def _c(px, v=1000):
    return {"o": str(px), "h": str(px * 1.0008), "l": str(px * 0.9992), "c": str(px), "v": str(v)}


class TradingDayGate(unittest.TestCase):
    """The addition over Rooster. weekday: 0=Mon .. 6=Sun (time.gmtime().tm_wday)."""

    def setUp(self):
        self.S = _load("hen", "scoring")

    def test_weekdays_are_trading_days(self):
        for wd in range(0, 5):                       # Mon-Fri
            self.assertTrue(self.S.is_trading_day(wd, [0, 1, 2, 3, 4]), f"weekday {wd}")

    def test_weekend_is_not_a_trading_day(self):
        self.assertFalse(self.S.is_trading_day(5, [0, 1, 2, 3, 4]))   # Sat
        self.assertFalse(self.S.is_trading_day(6, [0, 1, 2, 3, 4]))   # Sun

    def test_empty_list_means_every_day(self):
        """Rooster's behaviour: no gate configured → every day trades (BTC has no weekend)."""
        for wd in range(0, 7):
            self.assertTrue(self.S.is_trading_day(wd, []))
            self.assertTrue(self.S.is_trading_day(wd, None))

    def test_garbage_config_fails_open_to_every_day(self):
        self.assertTrue(self.S.is_trading_day(5, ["not-a-day"]))

    def test_string_weekday_values_are_coerced(self):
        self.assertTrue(self.S.is_trading_day(2, ["0", "1", "2", "3", "4"]))
        self.assertFalse(self.S.is_trading_day(6, ["0", "1", "2", "3", "4"]))

    def test_a_known_saturday_is_gated(self):
        """2026-07-18 is a Saturday; its 13:30 UTC 'open' must be a non-trading day."""
        wd = calendar.weekday(2026, 7, 18)           # 5 = Sat
        self.assertEqual(wd, 5)
        self.assertFalse(self.S.is_trading_day(wd, [0, 1, 2, 3, 4]))

    def test_a_known_monday_is_open(self):
        wd = calendar.weekday(2026, 7, 20)           # 0 = Mon
        self.assertEqual(wd, 0)
        self.assertTrue(self.S.is_trading_day(wd, [0, 1, 2, 3, 4]))


class SharedClock(unittest.TestCase):
    """Hen inherits Rooster's clock verbatim — a couple of anchors so a divergence is caught."""

    def setUp(self):
        self.S = _load("hen", "scoring")

    def test_inside_pre_open_window(self):
        ph, om, mt = self.S.session_phase(12 * 60 + 50, ["13:30"], 45)
        self.assertEqual(ph, "pre_open")
        self.assertEqual(om, 13 * 60 + 30)
        self.assertEqual(mt, 40)

    def test_at_and_after_open_is_idle(self):
        self.assertEqual(self.S.session_phase(13 * 60 + 30, ["13:30"], 45)[0], "idle")

    def test_outside_window_is_idle(self):
        self.assertEqual(self.S.session_phase(3 * 60, ["13:30"], 45)[0], "idle")


class BasketScoring(unittest.TestCase):
    """The pure thesis is unchanged from Rooster; verify it still reads a real setup and
    rejects a thin one, so a bad clone of scoring.py is caught."""

    def setUp(self):
        self.S = _load("hen", "scoring")
        self.inp = {"minDriftPct": 0.35, "minVolumeRatio": 1.15, "windowBars": 3,
                    "baselineBars": 16, "priorRangeBars": 24}

    def test_drift_on_volume_is_a_setup(self):
        base = [_c(100, 1000) for _ in range(40)]
        window = [_c(100.4, 3000), _c(100.7, 3200), _c(101.0, 3400)]   # up-drift on heavy volume
        th = self.S.build_thesis("xyz:NVDA", base + window, [], 40, self.inp)
        self.assertIsNotNone(th)
        self.assertEqual(th["direction"], "LONG")
        self.assertGreaterEqual(th["score"], 5)

    def test_flat_thin_is_no_setup(self):
        flat = [_c(100, 1000) for _ in range(43)]
        self.assertIsNone(self.S.build_thesis("xyz:NVDA", flat, [], 40, self.inp))

    def test_margin_tier_is_percent_scaled(self):
        self.assertEqual(self.S.margin_tier_pct(5, 12), 12)
        self.assertEqual(self.S.margin_tier_pct(6, 12), 15.0)
        self.assertEqual(self.S.margin_tier_pct(8, 12), 18.0)


class PackageContract(unittest.TestCase):
    """Config-level guarantees that make Hen an equity basket, not a Rooster copy."""

    def setUp(self):
        import yaml
        self.cat = yaml.safe_load((ROOT / "strategies" / "hen" / "strategy.yaml").read_text())["catalog"]
        self.rt = yaml.safe_load((ROOT / "strategies" / "hen" / "main" / "runtime.yaml").read_text())

    def test_basket_is_all_xyz_equities(self):
        assets = self.cat["assets"]
        self.assertGreaterEqual(len(assets), 8)
        self.assertTrue(all(a.startswith("xyz:") for a in assets), assets)

    def test_multi_slot(self):
        self.assertEqual(self.rt["strategy"]["slots"], 4)
        self.assertEqual(self.cat["max_slots"], 4)

    def test_trading_day_gate_is_configured_weekdays(self):
        self.assertEqual(self.rt["scanners"][1]["inputs"]["tradingDaysUtc"], [0, 1, 2, 3, 4])

    def test_hard_timeout_enabled_for_weekend_gap(self):
        """The book must be flat before the Fri-Sun oracle gap — hard_timeout ON, <= a session."""
        ht = self.rt["exit"]["dsl_preset"]["hard_timeout"]
        self.assertTrue(ht["enabled"])
        self.assertLessEqual(ht["interval_in_minutes"], 600)

    def test_margin_pct_is_percent_not_fraction(self):
        self.assertGreater(self.rt["strategy"]["margin_pct"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
