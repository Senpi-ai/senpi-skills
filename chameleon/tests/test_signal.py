#!/usr/bin/env python3
"""Unit tests for Chameleon's pure functions (ratio_zscore,
reversion_direction). Stubs chameleon_config + senpi_runtime_helpers so the
producer loads without the helpers package or a runtime workspace.
Run: python3 chameleon/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("chameleon_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["chameleon_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "chameleon-producer.py"
_spec = importlib.util.spec_from_file_location("chameleon_producer", _path)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)


def test_ratio_zscore_known_value():
    # ratios = [2,2,2,2,4] → mean 2.4, pstdev 0.8, latest 4 → z = 2.0
    a = [2, 2, 2, 2, 4]
    b = [1, 1, 1, 1, 1]
    z, ratio, mean, std = cp.ratio_zscore(a, b, 5)
    assert abs(z - 2.0) < 1e-9
    assert abs(mean - 2.4) < 1e-9 and abs(std - 0.8) < 1e-9 and ratio == 4.0


def test_ratio_zscore_flat_returns_none():
    assert cp.ratio_zscore([3, 3, 3, 3], [1, 1, 1, 1], 4) is None  # std == 0


def test_ratio_zscore_insufficient_returns_none():
    assert cp.ratio_zscore([2, 2], [1, 1], 5) is None


def test_reversion_direction_leg_is_numerator():
    # rich (z>0): numerator expensive → SHORT the numerator leg; cheap → LONG
    assert cp.reversion_direction(2.0, "ETH", "ETH", 2.0) == "SHORT"
    assert cp.reversion_direction(-2.0, "ETH", "ETH", 2.0) == "LONG"


def test_reversion_direction_leg_is_denominator():
    # rich (z>0): denominator cheap → LONG the denominator leg; cheap → SHORT
    assert cp.reversion_direction(2.0, "BTC", "ETH", 2.0) == "LONG"
    assert cp.reversion_direction(-2.0, "BTC", "ETH", 2.0) == "SHORT"


def test_reversion_direction_below_threshold_and_none():
    assert cp.reversion_direction(1.0, "ETH", "ETH", 2.0) is None
    assert cp.reversion_direction(None, "ETH", "ETH", 2.0) is None


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
