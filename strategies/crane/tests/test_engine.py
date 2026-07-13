"""Crane pair-engine tests. Pure/deterministic. The centerpiece is the naked-leg
safety invariant. Run: python3 strategies/crane/tests/test_engine.py"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402


def test_log_spread_and_guard():
    assert abs(scoring.log_spread(110, 100) - math.log(1.1)) < 1e-9
    assert scoring.log_spread(0, 100) is None      # non-positive price guarded
    assert scoring.log_spread(100, None) is None


def test_zscore_needs_full_window_and_dispersion():
    assert scoring.zscore([0.0] * 5, 10) is None    # window not full
    assert scoring.zscore([1.0] * 10, 10) is None    # zero dispersion → None (no div/0)
    hist = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.3]
    z = scoring.zscore(hist, 10)
    assert z is not None and z > 2                    # last point is a clear outlier


def test_naked_leg_always_closes_first():
    inp = {"entryZ": 2, "exitZ": 0.5, "stopZ": 3.5}
    # exactly one leg held → CLOSE_NAKED regardless of z (even a "fine" z)
    for z in (0.0, 1.0, 2.5, None):
        act, _ = scoring.decide_pair_action(z, a_held=True, b_held=False, inputs=inp)
        assert act == scoring.CLOSE_NAKED, f"naked leg not flattened at z={z}"
        act2, _ = scoring.decide_pair_action(z, a_held=False, b_held=True, inputs=inp)
        assert act2 == scoring.CLOSE_NAKED


def test_pair_state_machine():
    inp = {"entryZ": 2, "exitZ": 0.5, "stopZ": 3.5}
    # neither held: open only when dislocated and z known
    assert scoring.decide_pair_action(2.4, False, False, inp)[0] == scoring.OPEN_BOTH
    assert scoring.decide_pair_action(1.0, False, False, inp)[0] == scoring.HOLD
    assert scoring.decide_pair_action(None, False, False, inp)[0] == scoring.HOLD
    # both held: reversion and blowout both close; in-between holds
    assert scoring.decide_pair_action(0.3, True, True, inp)[0] == scoring.CLOSE_BOTH   # reverted
    assert scoring.decide_pair_action(3.9, True, True, inp)[0] == scoring.CLOSE_BOTH   # blowout stop
    assert scoring.decide_pair_action(1.5, True, True, inp)[0] == scoring.HOLD


def test_entry_legs_direction():
    pair = {"a": "BTC", "b": "ETH"}
    hi = scoring.entry_legs(2.5, pair)     # A rich → short A / long B
    assert hi[0] == {"asset": "BTC", "direction": "SHORT"}
    assert hi[1] == {"asset": "ETH", "direction": "LONG"}
    lo = scoring.entry_legs(-2.5, pair)
    assert lo[0]["direction"] == "LONG" and lo[1]["direction"] == "SHORT"


def test_leg_sizing_caps():
    lev, mgn = scoring.leg_sizing(5.0, {"legMarginPct": 8, "maxLegMarginPct": 12,
                                        "entryZ": 2, "legLeverage": 3, "maxLeverage": 5})
    assert 1 <= lev <= 5 and 0 < mgn <= 12


def test_push_spread_bounded():
    h = []
    for i in range(100):
        h = scoring.push_spread(h, float(i), window=10)
    assert len(h) <= 30 and h[-1] == 99.0        # bounded to ~3×window, newest kept


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} CRANE ENGINE TESTS PASS")
