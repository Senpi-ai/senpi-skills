"""Unit tests for Phalanx scoring math (pure, no I/O).
Run: python3 -m pytest strategies/phalanx/tests -q  or  python3 strategies/phalanx/tests/test_scoring.py"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scoring

def _approx(a, b, tol=0.01):
    return abs(a - b) < tol

def test_net_tilt_balanced():
    lr, tilt = scoring.net_tilt(10, 10)
    assert _approx(lr, 50.0), f"long_ratio={lr}"
    assert _approx(tilt, 0.0), f"net_tilt={tilt}"

def test_net_tilt_long_heavy():
    lr, tilt = scoring.net_tilt(15, 5)
    assert _approx(lr, 75.0), f"long_ratio={lr}"
    assert _approx(tilt, 25.0), f"net_tilt={tilt}"

def test_net_tilt_empty():
    lr, tilt = scoring.net_tilt(0, 0)
    assert _approx(lr, 50.0)
    assert _approx(tilt, 0.0)

def test_one_sidedness_rout():
    # 43 short vs 4 long = 91.5% one-sided
    s = scoring.one_sidedness(4, 43)
    assert _approx(s, 91.5, 0.1), f"one_sidedness={s}"

def test_one_sidedness_noise():
    # 429 vs 380 = 53% — barely leans
    s = scoring.one_sidedness(429, 380)
    assert _approx(s, 53.0, 0.1), f"one_sidedness={s}"

def test_one_sidedness_below_min_sample():
    # 4 vs 1 = below min_sample (10) -> 0.0
    s = scoring.one_sidedness(4, 1)
    assert s == 0.0, f"one_sidedness={s} (should be 0 for small sample)"

def test_one_sidedness_enough():
    # 13 long vs 7 short = 65% — just enough
    s = scoring.one_sidedness(13, 7)
    assert _approx(s, 65.0, 0.1), f"one_sidedness={s}"

def test_directional_delta_long_growing():
    # prev: 13L/7S (net +6), curr: 15L/6S (net +9), delta = +3
    d = scoring.directional_delta(13, 7, 15, 6, "LONG")
    assert _approx(d, 3.0), f"delta={d}"

def test_directional_delta_short_growing():
    # prev: 4L/15S (net -11), curr: 3L/18S (net -15), SHORT delta = prev_net - curr_net = -11 - (-15) = +4
    d = scoring.directional_delta(4, 15, 3, 18, "SHORT")
    assert _approx(d, 4.0), f"delta={d}"

def test_directional_delta_stale():
    # no change
    d = scoring.directional_delta(13, 7, 13, 7, "LONG")
    assert _approx(d, 0.0), f"delta={d}"

def test_directional_delta_fading():
    # prev: 15L/5S (net +10), curr: 13L/7S (net +6), LONG delta = 6-10 = -4
    d = scoring.directional_delta(15, 5, 13, 7, "LONG")
    assert _approx(d, -4.0), f"delta={d}"

def test_price_conviction_mult_confirmed():
    assert scoring.price_conviction_mult("BULLISH", "LONG") == 1.2
    assert scoring.price_conviction_mult("BEARISH", "SHORT") == 1.2

def test_price_conviction_mult_fighting():
    assert scoring.price_conviction_mult("BEARISH", "LONG") == 0.7
    assert scoring.price_conviction_mult("BULLISH", "SHORT") == 0.7

def test_price_conviction_mult_neutral():
    assert scoring.price_conviction_mult("NEUTRAL", "LONG") == 1.0
    assert scoring.price_conviction_mult("NEUTRAL", "SHORT") == 1.0

def test_is_divergent_yes():
    # cohort long (60), crowd short (40) -> divergent
    assert scoring.is_divergent(60.0, 40.0) is True

def test_is_divergent_no():
    # both long -> not divergent
    assert scoring.is_divergent(65.0, 70.0) is False

def test_is_divergent_edge():
    # cohort exactly 50 -> SHORT side; crowd 55 -> LONG side -> divergent
    assert scoring.is_divergent(50.0, 55.0) is True

def test_margin_pct_for_high_conviction():
    assert _approx(scoring.margin_pct_for(85, 15), 22.5), "should be 15 * 1.5"

def test_margin_pct_for_mid_conviction():
    assert _approx(scoring.margin_pct_for(70, 15), 18.75), "should be 15 * 1.25"

def test_margin_pct_for_low_conviction():
    assert _approx(scoring.margin_pct_for(50, 15), 15.0), "should be base"

def test_margin_pct_for_max_cap():
    assert _approx(scoring.margin_pct_for(90, 15, max_pct=20), 20.0), "should cap at 20"

def test_trend_structure_bullish():
    # higher lows: 100, 102, 104, 106, 108, 110
    candles = [{"l": l, "h": l + 5, "c": l + 2} for l in [100, 102, 104, 106, 108, 110]]
    label, strength = scoring.trend_structure(candles)
    assert label == "BULLISH", f"label={label}"
    assert strength > 0.5

def test_trend_structure_bearish():
    # lower highs: 110, 108, 106, 104, 102, 100
    candles = [{"l": h - 5, "h": h, "c": h - 2} for h in [110, 108, 106, 104, 102, 100]]
    label, strength = scoring.trend_structure(candles)
    assert label == "BEARISH", f"label={label}"
    assert strength > 0.5

def test_trend_structure_neutral():
    # mixed: 100, 98, 105, 103, 100, 102
    candles = [{"l": l, "h": l + 3, "c": l + 1} for l in [100, 98, 105, 103, 100, 102]]
    label, _ = scoring.trend_structure(candles)
    assert label == "NEUTRAL", f"label={label}"

def test_trend_structure_too_few():
    label, strength = scoring.trend_structure([{"l": 100, "h": 105}])
    assert label == "NEUTRAL"
    assert strength == 0.0

def test_breakout_check_long_new_high():
    # 12 candles, last close is the highest
    candles = [{"c": 100 + i, "h": 100 + i + 1, "l": 100 + i - 1} for i in range(12)]
    # make last close the highest
    candles[-1]["c"] = 200
    assert scoring.breakout_check(candles, "LONG") is True

def test_breakout_check_long_no_breakout():
    # last close is below the prior high — not a breakout
    candles = [{"c": 100 + i, "h": 100 + i + 2, "l": 100 + i - 1} for i in range(12)]
    candles[-1]["c"] = 90  # well below any prior high
    assert scoring.breakout_check(candles, "LONG") is False

def test_breakout_check_short_new_low():
    candles = [{"c": 100 - i, "h": 100 - i + 1, "l": 100 - i - 1} for i in range(12)]
    candles[-1]["c"] = 50
    assert scoring.breakout_check(candles, "SHORT") is True

def test_breakout_check_too_few():
    assert scoring.breakout_check([{"c": 100, "h": 105, "l": 95}], "LONG") is False

def test_asset_class_for():
    assert scoring.asset_class_for("BTC") == "crypto"
    assert scoring.asset_class_for("ETH") == "crypto"
    assert scoring.asset_class_for("xyz:NVDA") == "xyz"
    assert scoring.asset_class_for("xyz:JPY") == "xyz"

def test_bare_upper():
    assert scoring.bare_upper("xyz:NVDA") == "NVDA"
    assert scoring.bare_upper("BTC") == "BTC"
    assert scoring.bare_upper("xyz:jpy") == "JPY"

def test_venue_asset():
    assert scoring.venue_asset("NVDA", "xyz") == "xyz:NVDA"
    assert scoring.venue_asset("BTC", "") == "BTC"
    assert scoring.venue_asset("XYZ:NVDA", "xyz") == "xyz:NVDA"  # canonicalized

def test_accuracy_threshold_adjustment():
    """When hit rate is low, threshold goes up; when high, it goes down."""
    acc = scoring.new_accuracy_state()
    # Simulate 5 incorrect signals for crypto
    cls = acc["crypto"]
    cls["recent"] = [{"correct": False, "ts": 0} for _ in range(5)]
    cls["pending"] = []
    # recompute
    acc = scoring.update_accuracy(acc, "crypto", 999999, lambda a: 0.0)
    # hit rate 0% < 0.40 -> threshold_adj = +4
    eff = scoring.adjusted_threshold(65.0, "crypto", acc)
    assert eff == 69.0, f"threshold should be 65+4=69, got {eff}"

def test_accuracy_threshold_lowered():
    """When hit rate is high, threshold goes down (but floored at 52)."""
    acc = scoring.new_accuracy_state()
    cls = acc["crypto"]
    cls["recent"] = [{"correct": True, "ts": 0} for _ in range(6)]
    cls["pending"] = []
    acc = scoring.update_accuracy(acc, "crypto", 999999, lambda a: 0.0)
    eff = scoring.adjusted_threshold(65.0, "crypto", acc)
    assert eff == 63.0, f"threshold should be 65-2=63, got {eff}"

def test_accuracy_threshold_floor():
    """Threshold never goes below 52."""
    acc = scoring.new_accuracy_state()
    cls = acc["crypto"]
    cls["recent"] = [{"correct": True, "ts": 0} for _ in range(10)]
    cls["pending"] = []
    acc = scoring.update_accuracy(acc, "crypto", 999999, lambda a: 0.0)
    eff = scoring.adjusted_threshold(50.0, "crypto", acc)
    assert eff == 52.0, f"threshold floored at 52, got {eff}"

def test_accuracy_no_adjustment_with_few_samples():
    """With <5 evaluated signals, no adjustment."""
    acc = scoring.new_accuracy_state()
    cls = acc["crypto"]
    cls["recent"] = [{"correct": False, "ts": 0} for _ in range(3)]
    cls["pending"] = []
    acc = scoring.update_accuracy(acc, "crypto", 999999, lambda a: 0.0)
    eff = scoring.adjusted_threshold(65.0, "crypto", acc)
    assert eff == 65.0, f"no adjustment with <5 samples, got {eff}"

def test_rank_signals():
    candidates = [
        {"asset": "BTC", "conviction": 70},
        {"asset": "ETH", "conviction": 90},
        {"asset": "SOL", "conviction": 50},
    ]
    ranked = scoring.rank_signals(candidates)
    assert ranked[0]["asset"] == "ETH"
    assert ranked[1]["asset"] == "BTC"
    assert ranked[2]["asset"] == "SOL"


# ── run all tests without pytest ──────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
