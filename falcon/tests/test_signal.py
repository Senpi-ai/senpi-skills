#!/usr/bin/env python3
"""Unit tests for Falcon's pure functions (classify_instrument,
detect_conversion, momentum_pct, conversion_direction). Stubs falcon_config +
senpi_runtime_helpers so the producer loads without the helpers package or a
runtime workspace.
Run: python3 falcon/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("falcon_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.read_class_state = lambda: {}
_cfg.write_class_state = lambda s: None
_cfg.read_conversions = lambda: {}
_cfg.record_conversion = lambda n, t=None: None
_cfg.prune_conversions = lambda h: {}
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["falcon_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "falcon-producer.py"
_spec = importlib.util.spec_from_file_location("falcon_producer", _path)
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)

IPOP_F = 1e-7
IPOP_LEV = 5


def test_classify_ipop_by_funding_and_leverage():
    # tiny funding + low leverage cap → IPOP (pre-listing)
    assert fp.classify_instrument(6.25e-8, 5, IPOP_F, IPOP_LEV) == "IPOP"


def test_classify_standard_when_funding_normalizes():
    # funding jumped ~100x → STANDARD even if leverage cap still 5
    assert fp.classify_instrument(6.25e-6, 5, IPOP_F, IPOP_LEV) == "STANDARD"


def test_classify_standard_when_leverage_lifts():
    # leverage cap lifted above pre-listing → STANDARD even with tiny funding
    assert fp.classify_instrument(6.25e-8, 10, IPOP_F, IPOP_LEV) == "STANDARD"


def test_classify_bad_input_defaults_standard():
    assert fp.classify_instrument(None, 5, IPOP_F, IPOP_LEV) == "STANDARD"


def test_detect_conversion_only_on_ipop_to_standard():
    assert fp.detect_conversion("IPOP", "STANDARD") is True
    assert fp.detect_conversion("STANDARD", "STANDARD") is False
    assert fp.detect_conversion("IPOP", "IPOP") is False
    assert fp.detect_conversion(None, "STANDARD") is False  # first sighting, not a flip


def test_momentum_pct_known_value():
    # close 6 bars ago = 100, latest = 112 → +12%
    closes = [100, 101, 102, 103, 104, 105, 112]
    assert abs(fp.momentum_pct(closes, 6) - 12.0) < 1e-9


def test_momentum_pct_insufficient_and_bad_ref():
    assert fp.momentum_pct([100, 101], 6) is None
    assert fp.momentum_pct([0, 1, 2, 3, 4, 5, 6], 6) is None  # ref <= 0


def test_conversion_direction_rides_momentum():
    assert fp.conversion_direction(12.0, 3.0) == "LONG"
    assert fp.conversion_direction(-12.0, 3.0) == "SHORT"


def test_conversion_direction_below_threshold_and_none():
    assert fp.conversion_direction(1.5, 3.0) is None
    assert fp.conversion_direction(None, 3.0) is None


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
