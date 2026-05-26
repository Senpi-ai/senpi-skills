#!/usr/bin/env python3
"""Unit tests for Cuckoo's pure functions (performance_weight,
mirror_direction, tally_consensus, consensus_score). Stubs cuckoo_config +
senpi_runtime_helpers so the producer loads without the helpers package or a
runtime workspace.
Run: python3 cuckoo/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("cuckoo_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["cuckoo_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "cuckoo-producer.py"
_spec = importlib.util.spec_from_file_location("cuckoo_producer", _path)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)


def test_performance_weight_scaling_and_bounds():
    assert abs(cp.performance_weight(0) - 1.0) < 1e-9       # flat strategy weighs 1.0
    assert abs(cp.performance_weight(50) - 2.0) < 1e-9      # +50% → 2.0
    assert cp.performance_weight(1000, cap=3.0) == 3.0      # capped
    assert cp.performance_weight(-100) == 0.5               # floored


def test_mirror_direction_explicit_and_szi():
    assert cp.mirror_direction({"direction": "LONG"}) == "LONG"
    assert cp.mirror_direction({"side": "short"}) == "SHORT"
    assert cp.mirror_direction({"szi": 2.0}) == "LONG"
    assert cp.mirror_direction({"szi": -2.0}) == "SHORT"
    assert cp.mirror_direction({"szi": 0}) is None


def test_tally_consensus_aggregates_weight_and_count():
    entries = [
        {"asset": "BTC", "direction": "LONG", "weight": 2.0},
        {"asset": "btc", "direction": "LONG", "weight": 1.5},   # case-insensitive
        {"asset": "BTC", "direction": "SHORT", "weight": 1.0},  # opposite side separate
        {"asset": "ETH", "direction": "LONG", "weight": 3.0},
    ]
    agg = cp.tally_consensus(entries)
    btc_long = agg[("BTC", "LONG")]
    assert btc_long["count"] == 2 and abs(btc_long["weight"] - 3.5) < 1e-9
    assert agg[("BTC", "SHORT")]["count"] == 1
    assert agg[("ETH", "LONG")]["count"] == 1


def test_tally_consensus_skips_bad_entries():
    entries = [
        {"asset": "", "direction": "LONG", "weight": 2.0},      # no asset
        {"asset": "SOL", "direction": "FLAT", "weight": 2.0},   # bad direction
    ]
    assert cp.tally_consensus(entries) == {}


def test_consensus_score_tiers():
    # 1 strategy, low weight → base 2
    assert cp.consensus_score(1, 1.0) == 2
    # 2 strategies → +1 = 3
    assert cp.consensus_score(2, 3.0) == 3
    # 3 strategies → +2 = 4
    assert cp.consensus_score(3, 5.0) == 4
    # 4 strategies + high weight → 2 + 3 + 1 = 6
    assert cp.consensus_score(4, 7.0, high_weight=6.0) == 6


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
