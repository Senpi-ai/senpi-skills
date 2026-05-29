#!/usr/bin/env python3
"""Unit tests for Sheep's pure functions (ema, is_stacked_bullish,
stack_score, fast_slow_spread). Stubs sheep_config + senpi_runtime_helpers.
Run: python3 sheep/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("sheep_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["sheep_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "sheep-producer.py"
_spec = importlib.util.spec_from_file_location("sheep_producer", _path)
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


def test_ema_constant_series_equals_constant():
    # EMA of a constant series is the constant
    closes = [100.0] * 30
    assert abs(sp.ema(closes, 9) - 100.0) < 1e-9
    assert abs(sp.ema(closes, 21) - 100.0) < 1e-9


def test_ema_rising_series_lags_below_latest():
    # Linearly rising series: EMA should be < latest close (it's smoothed)
    closes = list(range(1, 31))  # 1..30
    e = sp.ema(closes, 9)
    assert e is not None
    assert e < 30.0    # below the latest
    assert e > 20.0    # but tracking up


def test_ema_insufficient_data_returns_none():
    assert sp.ema([1, 2, 3], 9) is None
    assert sp.ema([], 9) is None


def test_is_stacked_bullish_rising_and_falling():
    rising = list(range(1, 31))
    falling = list(range(30, 0, -1))
    assert sp.is_stacked_bullish(rising, 9, 21) is True     # fast EMA above slow on rising series
    assert sp.is_stacked_bullish(falling, 9, 21) is False   # fast below slow on falling


def test_stack_score_counts_trues():
    assert sp.stack_score([True, True, True]) == 3
    assert sp.stack_score([True, False, True]) == 2
    assert sp.stack_score([False, False, False]) == 0
    assert sp.stack_score([]) == 0


def test_fast_slow_spread_sign_and_magnitude():
    rising = list(range(1, 31))
    spread = sp.fast_slow_spread(rising, 9, 21)
    assert spread is not None and spread > 0     # positive spread on rising series
    falling = list(range(30, 0, -1))
    neg = sp.fast_slow_spread(falling, 9, 21)
    assert neg is not None and neg < 0


def test_fast_slow_spread_insufficient_returns_none():
    assert sp.fast_slow_spread([1, 2, 3], 9, 21) is None


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
