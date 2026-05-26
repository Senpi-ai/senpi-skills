#!/usr/bin/env python3
"""Unit tests for Remora's pure functions (position_notional,
mirror_direction, top_position, consensus_bonus). Stubs remora_config +
senpi_runtime_helpers so the producer loads without the helpers package or a
runtime workspace.
Run: python3 remora/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("remora_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["remora_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "remora-producer.py"
_spec = importlib.util.spec_from_file_location("remora_producer", _path)
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)


def test_position_notional_size_times_entry():
    assert abs(rp.position_notional({"szi": 2.0, "entryPx": 1000.0}) - 2000.0) < 1e-9


def test_position_notional_falls_back_to_margin():
    # no entry price → fall back to marginUsed
    assert abs(rp.position_notional({"szi": 0, "marginUsed": 750.0}) - 750.0) < 1e-9


def test_mirror_direction_explicit_and_szi():
    assert rp.mirror_direction({"direction": "LONG"}) == "LONG"
    assert rp.mirror_direction({"side": "short"}) == "SHORT"
    assert rp.mirror_direction({"szi": 3.0}) == "LONG"
    assert rp.mirror_direction({"szi": -3.0}) == "SHORT"
    assert rp.mirror_direction({"szi": 0}) is None


def test_top_position_picks_largest_notional():
    positions = [
        {"coin": "ETH", "szi": 1.0, "entryPx": 3000.0},     # 3000
        {"coin": "BTC", "szi": 0.1, "entryPx": 90000.0},    # 9000 ← largest
        {"coin": "SOL", "szi": 10.0, "entryPx": 150.0},     # 1500
    ]
    top = rp.top_position(positions)
    assert rp.position_asset(top) == "BTC"


def test_top_position_respects_min_notional():
    positions = [{"coin": "DOGE", "szi": 100.0, "entryPx": 0.1}]  # notional 10
    assert rp.top_position(positions, min_notional=5000.0) is None


def test_top_position_empty_returns_none():
    assert rp.top_position([]) is None
    assert rp.top_position([{"coin": "", "szi": 0}]) is None  # no asset, no direction


def test_consensus_bonus_tiers():
    assert rp.consensus_bonus(1) == 0
    assert rp.consensus_bonus(2) == 2
    assert rp.consensus_bonus(3) == 3
    assert rp.consensus_bonus(5) == 3


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
