#!/usr/bin/env python3
"""Unit tests for Osprey's pure functions (move_pct, catchup_gap,
lag_direction). Stubs osprey_config + senpi_runtime_helpers so the producer
loads without the helpers package or a runtime workspace.
Run: python3 osprey/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("osprey_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["osprey_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "osprey-producer.py"
_spec = importlib.util.spec_from_file_location("osprey_producer", _path)
op = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(op)


def test_move_pct_known_value():
    # close 4 bars ago = 100, latest = 105 → +5%
    closes = [100, 101, 102, 103, 105]
    assert abs(op.move_pct(closes, 4) - 5.0) < 1e-9


def test_move_pct_insufficient_and_bad_ref():
    assert op.move_pct([100, 101], 4) is None
    assert op.move_pct([0, 1, 2, 3, 4], 4) is None  # ref <= 0


def test_catchup_gap_math():
    # leader +5%, beta 1.8, proxy only +2% → expected +9%, gap +7%
    assert abs(op.catchup_gap(5.0, 2.0, 1.8) - 7.0) < 1e-9
    # proxy overshot: leader +5%, beta 1.8, proxy +12% → expected +9%, gap -3%
    assert abs(op.catchup_gap(5.0, 12.0, 1.8) - (-3.0)) < 1e-9


def test_lag_direction_long_when_proxy_lags_up():
    # leader up, proxy still owes upside → LONG
    assert op.lag_direction(5.0, 7.0, 2.0, 2.0) == "LONG"


def test_lag_direction_short_when_proxy_lags_down():
    # leader down, proxy still owes downside → SHORT
    assert op.lag_direction(-5.0, -7.0, 2.0, 2.0) == "SHORT"


def test_lag_direction_skips_overshoot():
    # leader up but proxy overshot (gap negative) → no trade
    assert op.lag_direction(5.0, -3.0, 2.0, 2.0) is None


def test_lag_direction_below_thresholds_and_none():
    assert op.lag_direction(1.0, 7.0, 2.0, 2.0) is None     # leader move too small
    assert op.lag_direction(5.0, 1.0, 2.0, 2.0) is None      # gap too small
    assert op.lag_direction(None, 7.0, 2.0, 2.0) is None
    assert op.lag_direction(5.0, None, 2.0, 2.0) is None


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
