#!/usr/bin/env python3
"""Unit tests for Tortoise's pure functions (seconds_since, is_dca_due,
pick_next_dca_asset). Stubs tortoise_config + senpi_runtime_helpers.
Run: python3 tortoise/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("tortoise_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.read_dca_history = lambda: {}
_cfg.record_dca = lambda a, t=None: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["tortoise_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "tortoise-producer.py"
_spec = importlib.util.spec_from_file_location("tortoise_producer", _path)
tp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tp)

NOW = 1_700_000_000.0
HOUR = 3600.0
DAY = 86400.0


def test_seconds_since_known_and_unknown():
    assert tp.seconds_since(NOW - 600, NOW) == 600
    assert tp.seconds_since(None, NOW) is None
    assert tp.seconds_since("oops", NOW) is None
    # Defensive: future timestamp clamps to 0 (clock skew safety)
    assert tp.seconds_since(NOW + 100, NOW) == 0.0


def test_is_dca_due_threshold_and_unknown():
    interval = DAY
    assert tp.is_dca_due(DAY + 1, interval) is True
    assert tp.is_dca_due(DAY, interval) is True       # >=, not >
    assert tp.is_dca_due(DAY - 1, interval) is False
    assert tp.is_dca_due(None, interval) is True      # never-DCA'd is always due


def test_pick_next_dca_oldest_overdue_wins():
    # BTC last 25h ago, ETH last 30h ago, SOL last 12h ago (not due), interval 24h
    history = {"BTC": NOW - 25 * HOUR, "ETH": NOW - 30 * HOUR, "SOL": NOW - 12 * HOUR}
    chosen = tp.pick_next_dca_asset(["BTC", "ETH", "SOL"], history, DAY, NOW)
    assert chosen == "ETH"   # most overdue past the interval


def test_pick_next_dca_never_dcad_beats_recent_overdue():
    # HYPE has never been DCA'd → should beat BTC that's only 25h overdue
    history = {"BTC": NOW - 25 * HOUR}
    chosen = tp.pick_next_dca_asset(["BTC", "HYPE"], history, DAY, NOW)
    assert chosen == "HYPE"


def test_pick_next_dca_none_when_all_in_window():
    # Everyone DCA'd recently → nothing due
    history = {"BTC": NOW - 10 * HOUR, "ETH": NOW - 5 * HOUR}
    assert tp.pick_next_dca_asset(["BTC", "ETH"], history, DAY, NOW) is None


def test_pick_next_dca_empty_assets():
    assert tp.pick_next_dca_asset([], {}, DAY, NOW) is None


def test_pick_next_dca_case_normalized():
    # Asset symbols in config can be lowercase; history is upper-cased
    history = {"BTC": NOW - 25 * HOUR}
    chosen = tp.pick_next_dca_asset(["btc"], history, DAY, NOW)
    assert chosen == "BTC"


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
