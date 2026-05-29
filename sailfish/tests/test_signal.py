#!/usr/bin/env python3
"""Unit tests for Sailfish's pure functions (relative_strength, rank_assets,
leader_above_runner_up). Stubs sailfish_config + senpi_runtime_helpers.
Run: python3 sailfish/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("sailfish_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["sailfish_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "sailfish-producer.py"
_spec = importlib.util.spec_from_file_location("sailfish_producer", _path)
sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sf)


def test_relative_strength_known_value():
    closes = [100.0] * 17
    closes[-1] = 103.0
    assert abs(sf.relative_strength(closes, 16) - 3.0) < 1e-9


def test_relative_strength_insufficient_and_bad_ref():
    assert sf.relative_strength([100, 101], 16) is None
    closes = [0.0] * 17
    assert sf.relative_strength(closes, 16) is None


def test_rank_assets_sorts_desc_and_drops_none():
    strength = {"BTC": 2.0, "ETH": -1.0, "SOL": None, "HYPE": 5.5}
    ranked = sf.rank_assets(strength)
    assert ranked == [("HYPE", 5.5), ("BTC", 2.0), ("ETH", -1.0)]


def test_leader_above_runner_up_passes_clean_lead():
    # HYPE +5.5, BTC +2.0 → margin 3.5pp, leader RS 5.5 > 1.0 → pass
    ranked = [("HYPE", 5.5), ("BTC", 2.0), ("ETH", -1.0)]
    out = sf.leader_above_runner_up(ranked, min_leader_rs_pct=1.0, margin_pct=1.5)
    assert out == ("HYPE", 5.5, 3.5)


def test_leader_above_runner_up_blocks_whipsaw():
    # Leader only 0.2pp ahead of runner-up → below 1.5pp margin → reject
    ranked = [("HYPE", 2.0), ("BTC", 1.8)]
    assert sf.leader_above_runner_up(ranked, min_leader_rs_pct=1.0, margin_pct=1.5) is None


def test_leader_above_runner_up_blocks_weak_leader():
    # Leader's own RS below min → reject even if margin is wide
    ranked = [("BTC", 0.5), ("ETH", -3.0)]
    assert sf.leader_above_runner_up(ranked, min_leader_rs_pct=1.0, margin_pct=1.5) is None


def test_leader_above_runner_up_solo_leader_passes_if_strong():
    # Only one asset has data → margin defaults to inf
    ranked = [("BTC", 3.0)]
    out = sf.leader_above_runner_up(ranked, min_leader_rs_pct=1.0, margin_pct=1.5)
    assert out is not None and out[0] == "BTC" and out[1] == 3.0


def test_leader_above_runner_up_empty_returns_none():
    assert sf.leader_above_runner_up([], 1.0, 1.5) is None


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
