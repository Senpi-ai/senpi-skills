#!/usr/bin/env python3
"""Unit tests for Marlin's pure signal functions (book_imbalance,
imbalance_direction, price_move_pct). Stubs marlin_config +
senpi_runtime_helpers so the producer loads without the helpers package
or a runtime workspace. Run: python3 marlin/tests/test_signal.py
"""
import importlib.util
import math
import sys
import types
from pathlib import Path

# ── Stub the top-level imports the producer makes ──
_cfg = types.ModuleType("marlin_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["marlin_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "marlin-producer.py"
_spec = importlib.util.spec_from_file_location("marlin_producer", _path)
mp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mp)


def _lvl(sz):
    return {"px": 0, "sz": sz, "n": 1}


def _book(bids, asks):
    return {"data": {"order_book": {"levels": [bids, asks]}}}


def test_book_imbalance_bid_heavy():
    r, b, a = mp.book_imbalance(_book([_lvl(60), _lvl(40)], [_lvl(20), _lvl(20)]))
    assert b == 100 and a == 40 and abs(r - 2.5) < 1e-9


def test_book_imbalance_ask_heavy():
    r, _, _ = mp.book_imbalance(_book([_lvl(20)], [_lvl(50), _lvl(30)]))
    assert abs(r - 0.25) < 1e-9


def test_book_imbalance_empty_and_missing():
    assert mp.book_imbalance({"data": {"order_book": {"levels": []}}})[0] is None
    assert mp.book_imbalance({})[0] is None


def test_book_imbalance_no_asks_is_inf():
    r, b, a = mp.book_imbalance(_book([_lvl(10)], []))
    assert r == math.inf and a == 0.0


def test_book_imbalance_levels_n_truncation():
    bids = [_lvl(10) for _ in range(20)]
    asks = [_lvl(10) for _ in range(20)]
    r, b, a = mp.book_imbalance(_book(bids, asks), levels_n=5)
    assert b == 50 and a == 50 and abs(r - 1.0) < 1e-9


def test_imbalance_direction():
    assert mp.imbalance_direction(2.5, 1.5) == "LONG"
    assert mp.imbalance_direction(0.4, 1.5) == "SHORT"   # 0.4 <= 1/1.5
    assert mp.imbalance_direction(1.0, 1.5) is None       # balanced
    assert mp.imbalance_direction(None, 1.5) is None
    assert mp.imbalance_direction(math.inf, 1.5) == "LONG"


def test_price_move_pct():
    assert abs(mp.price_move_pct([{"close": 100}, {"close": 110}], 1) - 10.0) < 1e-9
    assert abs(mp.price_move_pct([{"close": 100}, {"close": 95}], 1) + 5.0) < 1e-9
    assert mp.price_move_pct([{"close": 100}], 1) == 0.0


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
