#!/usr/bin/env python3
"""Unit tests for Meerkat's pure functions (event_age_minutes,
event_direction, momentum_tier, event_score). Stubs meerkat_config +
senpi_runtime_helpers so the producer loads without the helpers package or a
runtime workspace.
Run: python3 meerkat/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("meerkat_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["meerkat_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "meerkat-producer.py"
_spec = importlib.util.spec_from_file_location("meerkat_producer", _path)
mp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mp)


def test_event_age_minutes_seconds_and_millis():
    now = 1_700_000_000.0   # realistic 2023 epoch so the ms value clears the 1e12 detector
    assert abs(mp.event_age_minutes(now - 600, now) - 10.0) < 1e-9        # 600s ago = 10min
    # millisecond timestamp (now-600s expressed in ms) → same 10min
    assert abs(mp.event_age_minutes((now - 600) * 1000.0, now) - 10.0) < 1e-9


def test_event_age_minutes_bad_input():
    assert mp.event_age_minutes(None, 1_700_000_000.0) is None
    assert mp.event_age_minutes("oops", 1_700_000_000.0) is None
    assert mp.event_age_minutes(0, 1_700_000_000.0) is None


def test_event_direction_explicit_and_magnitude():
    assert mp.event_direction({"direction": "LONG"}) == "LONG"
    assert mp.event_direction({"side": "short"}) == "SHORT"
    assert mp.event_direction({"momentum": 8.0}) == "LONG"
    assert mp.event_direction({"change_pct": -8.0}) == "SHORT"
    assert mp.event_direction({"momentum": 0}) is None


def test_momentum_tier_thresholds():
    assert mp.momentum_tier(12.0, 5.0, 10.0) == 3   # >= tier3
    assert mp.momentum_tier(7.0, 5.0, 10.0) == 2    # >= tier2
    assert mp.momentum_tier(3.0, 5.0, 10.0) == 1    # below both
    assert mp.momentum_tier(-12.0, 5.0, 10.0) == 3  # uses magnitude


def test_event_score_components():
    # tier 3 + fresh + sm + vol = 3 + 2 + 1 + 1 = 7
    assert mp.event_score(3, True, True, True) == 7
    # tier 2 + fresh only = 2 + 2 = 4
    assert mp.event_score(2, True, False, False) == 4
    # tier 1 not fresh = 1
    assert mp.event_score(1, False, False, False) == 1


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
