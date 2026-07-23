#!/usr/bin/env python3
"""Rooster + shared-engine unit tests — the clock gate, the pivot-plateau fix, the cascades.

These cover the two bugs found building this wave, both silent no-trades of the same family
we've been chasing all week:
  - pivot PLATEAU: a flat top registered the same pivot twice, so every higher-high / double-
    bottom comparison saw a 0% step and the strategy never fired (manta + bloodhound).
  - AOI on the trigger bar: requiring the current close INSIDE the zone rejected exactly the
    break that triggers entry, because a real break leaves the zone on that bar (manta).

Run: python3 strategies/rooster/tests/test_engine.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
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


class RoosterClock(unittest.TestCase):
    def setUp(self):
        self.S = _load("rooster", "scoring")

    def test_inside_pre_open_window(self):
        # 12:50 UTC, 45m window before 13:30 -> pre_open, 40m to go
        ph, om, mt = self.S.session_phase(12 * 60 + 50, ["13:30"], 45)
        self.assertEqual(ph, "pre_open")
        self.assertEqual(mt, 40)

    def test_at_and_after_open_is_idle(self):
        self.assertEqual(self.S.session_phase(13 * 60 + 30, ["13:30"], 45)[0], "idle")
        self.assertEqual(self.S.session_phase(13 * 60 + 45, ["13:30"], 45)[0], "idle")

    def test_outside_window_is_idle(self):
        self.assertEqual(self.S.session_phase(3 * 60, ["13:30"], 45)[0], "idle")

    def test_midnight_wrap(self):
        # a 00:30 open has its pre-open window in the PRIOR day's 23:xx
        self.assertEqual(self.S.session_phase(23 * 60 + 50, ["00:30"], 45)[0], "pre_open")
        self.assertEqual(self.S.session_phase(0 * 60 + 31, ["00:30"], 45)[0], "idle")

    def test_nearest_of_multiple_opens(self):
        ph, om, mt = self.S.session_phase(7 * 60 + 30, ["13:30", "08:00"], 45)
        self.assertEqual((ph, om, mt), ("pre_open", 8 * 60, 30))

    def test_drift_on_volume_is_a_setup(self):
        base = [_c(100 + i * 0.001, 1000) for i in range(40)]
        setup = base + [_c(100.2, 2500), _c(100.45, 2800), _c(100.7, 3100)]
        th = self.S.build_thesis("BTC", setup, [], 20, {})
        self.assertIsNotNone(th)
        self.assertEqual(th["direction"], "LONG")

    def test_flat_thin_is_no_setup(self):
        base = [_c(100 + i * 0.001, 1000) for i in range(40)]
        flat = base + [_c(100.03, 900), _c(100.02, 850), _c(100.04, 880)]
        self.assertIsNone(self.S.build_thesis("BTC", flat, [], 20, {}))


class PivotPlateau(unittest.TestCase):
    """The flat-top duplicate-pivot bug, on BOTH engines that share the fractal finder."""

    def _zig(self, S, base, legs, up=True, amp=0.010, ret=0.004):
        out, p = [], base
        for _ in range(legs):
            for x in range(5):
                out.append(p * (1 + (amp * x if up else -amp * x)))
            p = p * (1 + amp * 4) if up else p * (1 - amp * 4)
            for x in range(3):
                out.append(p * (1 - (ret * x if up else -ret * x)))
            p = p * (1 - ret * 2) if up else p * (1 + ret * 2)
        return [_c(round(v, 6)) for v in out]

    def test_manta_bias_reads_a_clean_uptrend(self):
        S = _load("manta", "scoring")
        up = self._zig(S, 1.10, 5)
        # before the plateau fix this returned NEUTRAL (duplicate pivots -> 0% step)
        self.assertEqual(S.timeframe_bias(up), "UP")

    def test_no_duplicate_pivots(self):
        S = _load("manta", "scoring")
        highs, lows = S.swing_points(self._zig(S, 1.10, 5), 2)
        idxs = [i for i, _ in highs]
        self.assertEqual(len(idxs), len(set(idxs)))
        # adjacent pivots must differ by more than k bars (no plateau twins)
        self.assertTrue(all(idxs[i] - idxs[i - 1] > 2 for i in range(1, len(idxs))))

    def test_bloodhound_still_finds_a_W(self):
        S = _load("bloodhound", "scoring")
        w = [_c(110)] * 25 + [_c(x) for x in
                              [110, 106, 102, 98, 94, 90, 92, 95, 98, 100, 97, 94, 91, 90.5, 93, 96, 99, 101.5]]
        th = S.build_thesis("T", w, [], {})
        self.assertIsNotNone(th)
        self.assertEqual((th["pattern"], th["direction"]), ("double_bottom", "LONG"))

    def test_bloodhound_no_false_positive_on_noise(self):
        S = _load("bloodhound", "scoring")
        # deterministic pseudo-noise (no Math.random in engine; fixed LCG here)
        seed, out = 12345, []
        for _ in range(60):
            seed = (1103515245 * seed + 12345) % (2 ** 31)
            out.append(_c(round(100 + (seed / 2 ** 31 - 0.5) * 0.8, 4)))
        self.assertIsNone(S.build_thesis("T", out, [], {}))


class MantaCascade(unittest.TestCase):
    def setUp(self):
        self.S = _load("manta", "scoring")

    def _zig(self, base, legs, up=True, amp=0.010, ret=0.004):
        out, p = [], base
        for _ in range(legs):
            for x in range(5):
                out.append(p * (1 + (amp * x if up else -amp * x)))
            p = p * (1 + amp * 4) if up else p * (1 - amp * 4)
            for x in range(3):
                out.append(p * (1 - (ret * x if up else -ret * x)))
            p = p * (1 - ret * 2) if up else p * (1 + ret * 2)
        return [_c(round(v, 6)) for v in out]

    def _entry_15m(self, aoi):
        mid = (aoi[0] + aoi[1]) / 2
        m = [_c(round(mid * (1 + 0.0004 * (1 if i % 2 else -1)), 6)) for i in range(20)]
        return m + [_c(round(mid * 0.9996, 6)), _c(round(mid * 1.0003, 6)), _c(round(mid * 1.0018, 6))]

    def test_full_cascade_fires(self):
        D = self._zig(1.10, 5)
        aoi = self.S.find_aoi(self._zig(1.10, 6), "UP", {})
        th = self.S.build_thesis("xyz:EUR", D, self._zig(1.10, 6), self._zig(1.10, 6),
                                 self._entry_15m(aoi), {})
        self.assertIsNotNone(th, "textbook aligned setup must fire")
        self.assertEqual(th["direction"], "LONG")

    def test_break_bar_leaving_zone_still_counts(self):
        # the AOI regression: the trigger bar closes ABOVE the zone, and that must be OK
        aoi = self.S.find_aoi(self._zig(1.10, 6), "UP", {})
        m15 = self._entry_15m(aoi)
        self.assertGreater(self.S._close(m15[-1]), aoi[1])   # closed outside the zone
        touched, _ = self.S.touched_aoi(m15, aoi, 8)
        self.assertTrue(touched)                              # ...but tagged it earlier -> valid

    def test_one_dissenting_timeframe_blocks(self):
        D, H4 = self._zig(1.10, 5), self._zig(1.10, 6)
        aoi = self.S.find_aoi(H4, "UP", {})
        down_1h = self._zig(1.10, 5, up=False)
        self.assertIsNone(self.S.build_thesis("xyz:EUR", D, H4, down_1h, self._entry_15m(aoi), {}))


class IbisRegime(unittest.TestCase):
    def setUp(self):
        self.S = _load("ibis", "scoring")

    def test_clean_trend_is_TREND(self):
        up = [_c(round(100 * (1.012 ** i), 4)) for i in range(26)]
        self.assertEqual(self.S.classify_regime(up, {})[0], "TREND")

    def test_oscillation_is_RANGE(self):
        chop = [_c(100 + (2 if i % 2 else -2)) for i in range(26)]
        self.assertEqual(self.S.classify_regime(chop, {})[0], "RANGE")

    def test_trend_entry_requires_oi_baseline(self):
        up = [_c(round(100 * (1.012 ** i), 4)) for i in range(26)]
        _r, er, st, stg = self.S.classify_regime(up, {})
        self.assertIsNone(self.S.trend_thesis("BTC", up, er, st, stg, None, {}))   # no baseline
        self.assertIsNone(self.S.trend_thesis("BTC", up, er, st, stg, 0.1, {}))    # OI flat
        self.assertIsNotNone(self.S.trend_thesis("BTC", up, er, st, stg, 2.0, {}))  # OI rising

    def test_range_fade_skipped_on_extreme_funding(self):
        rng = [_c(100 + (3 if i % 4 in (1, 2) else -3)) for i in range(30)] + [_c(97.2)]
        _r, er, _s, _st = self.S.classify_regime(rng, {})
        self.assertIsNone(self.S.range_thesis("BTC", rng, er, 60.0, {}))    # extreme -> skip
        self.assertIsNotNone(self.S.range_thesis("BTC", rng, er, 5.0, {}))  # calm -> fade

    def test_funding_annualization(self):
        # '0.0000125' hourly -> ~10.95% APR
        self.assertAlmostEqual(self.S.annualized_funding_pct("0.0000125"), 10.95, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
