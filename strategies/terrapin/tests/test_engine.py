#!/usr/bin/env python3
"""Terrapin engine tests — Donchian/ATR/MACD, the frozen-anchor pyramid ladder, the MACD filter.

The load-bearing property is the LADDER: unit k must arm only once price has extended k·½N
beyond the FROZEN breakout anchor, so the four wallets build a real pyramid rather than four
copies of the same entry. Also guards the same silent-no-trade family we've been chasing:
a live (sliding) channel would leave u3/u4 perpetually un-armed in a normal trend.

Run: python3 strategies/terrapin/tests/test_engine.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load():
    d = ROOT / "strategies" / "terrapin" / "u1" / "scanners"
    sys.path.insert(0, str(d))
    try:
        sys.modules.pop("scoring", None)
        spec = importlib.util.spec_from_file_location("terrapin_scoring", d / "scoring.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.pop(0)


def _c(px, hi=None, lo=None):
    return {"o": str(px), "h": str(hi if hi else px * 1.004), "l": str(lo if lo else px * 0.996),
            "c": str(px), "v": "1000"}


class Indicators(unittest.TestCase):
    def setUp(self):
        self.S = _load()

    def test_donchian_excludes_current_bar(self):
        cs = [_c(100 + i) for i in range(21)] + [_c(200)]   # last bar is a spike
        hi, lo = self.S.donchian(cs, 20)
        self.assertLess(hi, 200)                             # the 200 spike is the current bar → excluded

    def test_atr_positive_on_moving_series(self):
        cs = [_c(100 + i * 0.5) for i in range(25)]
        self.assertGreater(self.S.atr(cs, 20), 0)

    def test_macd_sign_tracks_trend(self):
        # a flat base then a fresh accelerating move — the sign the filter actually keys on
        up = [_c(100)] * 30 + [_c(100 + i * 0.8) for i in range(12)]
        dn = [_c(100)] * 30 + [_c(100 - i * 0.8) for i in range(12)]
        self.assertGreater(self.S.macd_hist(up, 12, 26, 9), 0)
        self.assertLess(self.S.macd_hist(dn, 12, 26, 9), 0)


class FrozenAnchorLadder(unittest.TestCase):
    """The pyramid ladder on REALISTIC breakout data — a range with a sticky prior high/low that
    the Donchian channel locks onto, then a clean break that runs. (A monotonic ramp is
    pathological: every bar prints a new high, so the channel never sticks and no real breakout
    exists to anchor to — which is not how markets or the strategy behave.)"""

    def setUp(self):
        self.S = _load()

    def _range_then_break(self, run, cap=101.0, floor=None):
        """40 bars ranging under a sticky resistance `cap` (or over support `floor`), then `run`."""
        base = []
        for i in range(40):
            p = 99.0 + (2.0 if i % 6 == 0 else 0.0) - (1.5 if i % 4 == 0 else 0.0) + (0.3 if i % 2 else -0.3)
            if floor is None:
                p = min(p, cap)
                base.append(_c(round(p, 3), hi=round(min(p * 1.004, cap + 0.2), 3)))
            else:
                p = max(200 - p, floor)
                base.append(_c(round(p, 3), lo=round(max(p * 0.996, floor - 0.2), 3)))
        return base + [_c(round(x, 3)) for x in run]

    def test_units_arm_progressively(self):
        series = self._range_then_break([101.5, 102.4, 103.3, 104.2])
        armed = [u for u in range(4)
                 if self.S.build_thesis("BTC", series, u, {"requireMacd": False})]
        self.assertEqual(armed, [0, 1, 2, 3], "a full-extension breakout must arm all four rungs")

    def test_lower_rung_arms_before_upper(self):
        just_broke = self._range_then_break([101.4])         # only just past resistance
        armed = [u for u in range(4)
                 if self.S.build_thesis("BTC", just_broke, u, {"requireMacd": False})]
        self.assertIn(0, armed)                              # base arms
        self.assertNotIn(3, armed)                           # tip does not — price hasn't extended 1½N

    def test_anchor_is_frozen_not_sliding(self):
        """A slow, steady breakout must still arm the upper units — the live-channel bug would not."""
        slow = self._range_then_break([101.5, 102.4, 103.3, 104.2, 105.0])
        self.assertIsNotNone(self.S.build_thesis("BTC", slow, 3, {"requireMacd": False}),
                             "u4 must arm in a sustained trend (frozen anchor), not only a spike")
        # the anchor stays put across the whole run
        a1 = self.S.breakout_anchor(self._range_then_break([101.5]), 20, "LONG")
        a2 = self.S.breakout_anchor(slow, 20, "LONG")
        self.assertAlmostEqual(a1, a2, delta=0.5)

    def test_no_breakout_no_arm(self):
        flat = [_c(100 + (0.3 if i % 2 else -0.3)) for i in range(45)]
        for u in range(4):
            self.assertIsNone(self.S.build_thesis("BTC", flat, u, {"requireMacd": False}))

    def test_short_pyramid_is_symmetric(self):
        down = self._range_then_break([98.5, 97.6, 96.7, 95.8], floor=99.0)
        armed = [self.S.build_thesis("BTC", down, u, {"requireMacd": False})
                 for u in range(4)]
        armed = [t for t in armed if t]
        self.assertTrue(armed and all(t["direction"] == "SHORT" for t in armed))
        self.assertGreaterEqual(len(armed), 3)


class MacdFilter(unittest.TestCase):
    """A Donchian-20 breakout means price is at a 20-bar extreme, which almost always drags MACD
    the same way — so the filter rarely vetoes a FRESH breakout (a good property: it doesn't
    neuter the strategy). Its job is the marginal re-test. That makes an organic opposing-momentum
    breakout near-impossible to construct, so the veto CONTRACT is tested by forcing the sign."""

    def setUp(self):
        self.S = _load()

    def _long_breakout(self):
        base = []
        for i in range(38):
            p = min(99.0 + (0.3 if i % 2 else -0.3) + (1.5 if i % 6 == 0 else 0), 100.5)
            base.append(_c(round(p, 3), hi=round(min(p * 1.003, 100.6), 3)))
        return base + [_c(101.5), _c(102.5)]                 # clean break above ~100.5

    def test_valid_breakout_fires_with_macd_on(self):
        """The filter must NOT block a legitimate breakout where momentum agrees."""
        th = self.S.build_thesis("BTC", self._long_breakout(), 0, {})
        self.assertIsNotNone(th)
        self.assertEqual(th["direction"], "LONG")

    def test_opposing_macd_is_vetoed(self):
        """Force MACD to disagree with a valid long-breakout geometry -> must veto."""
        series = self._long_breakout()
        orig = self.S.macd_hist
        self.S.macd_hist = lambda *a, **k: -5.0            # momentum opposes the long breakout
        try:
            self.assertIsNone(self.S.build_thesis("BTC", series, 0, {}),
                              "opposing MACD must veto")
            self.assertIsNotNone(self.S.build_thesis("BTC", series, 0, {"requireMacd": False}),
                                 "with the filter off, geometry alone still arms")
        finally:
            self.S.macd_hist = orig


class MarginTier(unittest.TestCase):
    def setUp(self):
        self.S = _load()

    def test_margin_is_percent_scaled(self):
        # base 40% of the unit's wallet; a strong score nudges up but stays a sane percent
        self.assertEqual(self.S.margin_tier_pct(5, 40), 40)
        self.assertAlmostEqual(self.S.margin_tier_pct(6, 40), 44.0)
        self.assertAlmostEqual(self.S.margin_tier_pct(8, 40), 50.0)
        self.assertLessEqual(self.S.margin_tier_pct(9, 40), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
