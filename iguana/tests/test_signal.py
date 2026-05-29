#!/usr/bin/env python3
"""Unit tests for Iguana's pure functions (trend_strength, trend_direction,
pick_strongest_trend). Stubs iguana_config + senpi_runtime_helpers.
Run: python3 iguana/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("iguana_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["iguana_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "iguana-producer.py"
_spec = importlib.util.spec_from_file_location("iguana_producer", _path)
ip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ip)


def test_trend_strength_known_value():
    # 24 bars ago close = 100, latest = 105 → +5%
    closes = list(range(80, 105))   # 25 values, [0]=80...[24]=104? Let me recompute
    # Actually I want closes[-25] = 100 and closes[-1] = 105.
    closes = [100.0] * 25
    closes[-1] = 105.0
    assert abs(ip.trend_strength(closes, 24) - 5.0) < 1e-9


def test_trend_strength_insufficient_and_bad_ref():
    assert ip.trend_strength([100, 101], 24) is None
    closes = [0.0] * 25
    assert ip.trend_strength(closes, 24) is None     # ref <= 0


def test_trend_direction_thresholds():
    assert ip.trend_direction(3.0, 1.5) == "LONG"
    assert ip.trend_direction(-3.0, 1.5) == "SHORT"
    assert ip.trend_direction(1.0, 1.5) is None      # below threshold
    assert ip.trend_direction(None, 1.5) is None


def test_pick_strongest_trend_chooses_biggest_magnitude():
    strength = {"xyz:SP500": 2.0, "xyz:XYZ100": -4.5}
    picked = ip.pick_strongest_trend(strength, 1.5)
    assert picked == ("xyz:XYZ100", -4.5)    # larger magnitude wins regardless of sign


def test_pick_strongest_trend_filters_below_threshold():
    strength = {"xyz:SP500": 0.5, "xyz:XYZ100": -1.0}
    assert ip.pick_strongest_trend(strength, 1.5) is None


def test_pick_strongest_trend_handles_none_strengths():
    strength = {"xyz:SP500": None, "xyz:XYZ100": 2.0}
    picked = ip.pick_strongest_trend(strength, 1.5)
    assert picked == ("xyz:XYZ100", 2.0)


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS {fn.__name__}")
        except Exception:
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
