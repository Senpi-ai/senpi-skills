#!/usr/bin/env python3
"""Unit tests for Coyote's pure functions (pct_move, realized_vol_pct,
dispersion_pct, classify_regime, regime_to_direction). Stubs coyote_config +
senpi_runtime_helpers.
Run: python3 coyote/tests/test_signal.py
"""
import importlib.util
import math
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("coyote_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["coyote_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "coyote-producer.py"
_spec = importlib.util.spec_from_file_location("coyote_producer", _path)
co = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(co)


# ── pct_move ───────────────────────────────────────────────────

def test_pct_move_known_value():
    closes = [100.0] * 43
    closes[-1] = 110.0
    assert abs(co.pct_move(closes, 42) - 10.0) < 1e-9


def test_pct_move_insufficient_and_bad_ref():
    assert co.pct_move([1, 2, 3], 42) is None
    assert co.pct_move([0.0] * 43, 42) is None


# ── realized_vol_pct ───────────────────────────────────────────

def test_realized_vol_constant_series_is_zero():
    # Flat series → zero realized vol
    closes = [100.0] * 50
    v = co.realized_vol_pct(closes, lookback=42)
    assert v is not None
    assert abs(v) < 1e-9


def test_realized_vol_oscillating_series_is_positive():
    # +1% / -1% alternation has real variance
    closes = [100.0]
    for i in range(50):
        # alternate up/down
        prev = closes[-1]
        closes.append(prev * 1.01 if i % 2 == 0 else prev * 0.99)
    v = co.realized_vol_pct(closes, lookback=42)
    assert v is not None and v > 0


def test_realized_vol_insufficient_returns_none():
    assert co.realized_vol_pct([100, 101], lookback=42) is None


# ── dispersion_pct ─────────────────────────────────────────────

def test_dispersion_pct_synchronized_market_low():
    # All assets moved exactly the same → near-zero dispersion
    returns = {"BTC": 5.0, "ETH": 5.0, "SOL": 5.0, "HYPE": 5.0}
    d = co.dispersion_pct(returns)
    assert d is not None and d < 1e-9


def test_dispersion_pct_mixed_market_high():
    # Assets diverge → positive dispersion
    returns = {"BTC": 5.0, "ETH": -3.0, "SOL": 12.0, "HYPE": -8.0}
    d = co.dispersion_pct(returns)
    assert d is not None and d > 5.0


def test_dispersion_pct_insufficient_returns_none():
    # Only one asset with data → cannot compute
    assert co.dispersion_pct({"BTC": 5.0}) is None
    assert co.dispersion_pct({"BTC": None, "ETH": None}) is None


# ── classify_regime ────────────────────────────────────────────

def test_classify_regime_trend_up():
    # BTC +8% over 7d, vol moderate (50% annualized) → TREND_UP
    assert co.classify_regime(8.0, 50.0, 5.0, 5.0, 80.0, 60.0) == "TREND_UP"


def test_classify_regime_trend_down_requires_vol_confirmation():
    # BTC -8% with HIGH vol (70%) → TREND_DOWN (crash regime confirmed)
    assert co.classify_regime(-8.0, 70.0, 5.0, 5.0, 80.0, 60.0) == "TREND_DOWN"
    # BTC -8% but LOW vol (40%) — slow grind down doesn't qualify as TREND_DOWN
    assert co.classify_regime(-8.0, 40.0, 5.0, 5.0, 80.0, 60.0) == "CHOP"


def test_classify_regime_trend_up_blocked_by_vol():
    # BTC +8% but vol is 100% (extreme) → not a clean uptrend → CHOP
    assert co.classify_regime(8.0, 100.0, 5.0, 5.0, 80.0, 60.0) == "CHOP"


def test_classify_regime_below_thresholds_is_chop():
    # Small trend, moderate vol → CHOP
    assert co.classify_regime(2.0, 50.0, 5.0, 5.0, 80.0, 60.0) == "CHOP"


def test_classify_regime_missing_inputs():
    assert co.classify_regime(None, 50.0, 5.0, 5.0, 80.0, 60.0) == "UNKNOWN"
    assert co.classify_regime(8.0, None, 5.0, 5.0, 80.0, 60.0) == "UNKNOWN"


# ── regime_to_direction ────────────────────────────────────────

def test_regime_to_direction_mapping():
    assert co.regime_to_direction("TREND_UP") == "LONG"
    assert co.regime_to_direction("TREND_DOWN") == "SHORT"
    assert co.regime_to_direction("CHOP") is None
    assert co.regime_to_direction("UNKNOWN") is None


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
