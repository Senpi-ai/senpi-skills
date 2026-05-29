#!/usr/bin/env python3
"""Unit tests for Lynx's pure functions — self-tuning logic
(parse_score_from_reasoning, compute_bucket_stats, recommend_min_score,
should_update_threshold) AND scoring logic (pct_move, trend_direction,
lynx_score). Stubs lynx_config + senpi_runtime_helpers.
Run: python3 lynx/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("lynx_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.read_lynx_state = lambda: {}
_cfg.write_lynx_state = lambda s: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["lynx_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "lynx-producer.py"
_spec = importlib.util.spec_from_file_location("lynx_producer", _path)
lx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lx)


# ── parse_score_from_reasoning ─────────────────────────────────

def test_parse_score_from_reasoning_lowercase():
    assert lx.parse_score_from_reasoning("score 7 BTC LONG, trend strong") == 7


def test_parse_score_from_reasoning_capital_with_colon():
    assert lx.parse_score_from_reasoning("Score: 11 (apex tier)") == 11


def test_parse_score_from_reasoning_equals_sign():
    assert lx.parse_score_from_reasoning("SCORE=4, marginal setup") == 4


def test_parse_score_from_reasoning_missing_returns_none():
    assert lx.parse_score_from_reasoning("trade closed at +5%") is None
    assert lx.parse_score_from_reasoning("") is None
    assert lx.parse_score_from_reasoning(None) is None


# ── compute_bucket_stats ───────────────────────────────────────

def test_compute_bucket_stats_basic():
    trades = [
        {"score": 7, "roe_pct": -3.0},
        {"score": 7, "roe_pct": -5.0},
        {"score": 7, "roe_pct": +2.0},
        {"score": 10, "roe_pct": +12.0},
        {"score": 10, "roe_pct": +8.0},
    ]
    stats = lx.compute_bucket_stats(trades)
    assert stats[7]["n"] == 3
    assert abs(stats[7]["avg_roe_pct"] - (-2.0)) < 1e-9    # (-3 + -5 + 2) / 3
    assert abs(stats[7]["win_rate_pct"] - (100.0 / 3)) < 1e-6
    assert stats[10]["n"] == 2
    assert abs(stats[10]["avg_roe_pct"] - 10.0) < 1e-9
    assert stats[10]["win_rate_pct"] == 100.0


def test_compute_bucket_stats_drops_no_score():
    trades = [
        {"score": None, "roe_pct": -5.0},
        {"score": 8, "roe_pct": -2.0},
    ]
    stats = lx.compute_bucket_stats(trades)
    assert list(stats.keys()) == [8]


def test_compute_bucket_stats_handles_empty():
    assert lx.compute_bucket_stats([]) == {}
    assert lx.compute_bucket_stats(None) == {}


# ── recommend_min_score ────────────────────────────────────────

def test_recommend_min_score_raises_when_bucket_bleeds():
    # Current floor is 4. Score 5 bucket has 10 trades averaging -3% → bleed.
    # → recommend 5 + 1 = 6.
    stats = {
        4: {"n": 5, "avg_roe_pct": -0.5, "win_rate_pct": 40},   # not enough n
        5: {"n": 10, "avg_roe_pct": -3.0, "win_rate_pct": 20},   # bleeding
        7: {"n": 8, "avg_roe_pct": +5.0, "win_rate_pct": 60},
    }
    assert lx.recommend_min_score(stats, current_min_score=4, min_bucket_n=8, bucket_bleed_pct=-1.0, max_min_score=9) == 6


def test_recommend_min_score_holds_when_no_bleed():
    stats = {
        5: {"n": 10, "avg_roe_pct": +2.0, "win_rate_pct": 60},
        7: {"n": 8, "avg_roe_pct": +5.0, "win_rate_pct": 60},
    }
    assert lx.recommend_min_score(stats, current_min_score=5, min_bucket_n=8, bucket_bleed_pct=-1.0, max_min_score=9) == 5


def test_recommend_min_score_ignores_below_floor():
    # A bucket BELOW the current floor is already culled — don't double-act on it
    stats = {
        3: {"n": 20, "avg_roe_pct": -5.0, "win_rate_pct": 10},   # below floor — ignore
        5: {"n": 10, "avg_roe_pct": +3.0, "win_rate_pct": 60},
    }
    assert lx.recommend_min_score(stats, current_min_score=4, min_bucket_n=8, bucket_bleed_pct=-1.0, max_min_score=9) == 4


def test_recommend_min_score_caps_at_max():
    # Bleeding bucket at score 8 would recommend 9; max is 7 → cap at 7
    stats = {
        8: {"n": 10, "avg_roe_pct": -3.0, "win_rate_pct": 20},
    }
    assert lx.recommend_min_score(stats, current_min_score=4, min_bucket_n=8, bucket_bleed_pct=-1.0, max_min_score=7) == 7


def test_recommend_min_score_picks_highest_bleeding():
    # Multiple bleeding buckets → recommend above the HIGHEST one
    stats = {
        5: {"n": 10, "avg_roe_pct": -2.0, "win_rate_pct": 20},   # bleeding
        6: {"n": 10, "avg_roe_pct": -3.0, "win_rate_pct": 15},   # bleeding (higher)
        7: {"n": 8, "avg_roe_pct": +5.0, "win_rate_pct": 60},
    }
    # Highest bleeding = 6, recommended = 7
    assert lx.recommend_min_score(stats, current_min_score=4, min_bucket_n=8, bucket_bleed_pct=-1.0, max_min_score=9) == 7


# ── should_update_threshold ────────────────────────────────────

def test_should_update_threshold_hysteresis():
    assert lx.should_update_threshold(4, 5) is True       # raised by 1, hysteresis met
    assert lx.should_update_threshold(4, 4) is False      # no change
    assert lx.should_update_threshold(4, 6) is True       # raised by 2
    assert lx.should_update_threshold(4, 3) is False      # would lower (never)


def test_should_update_threshold_handles_none():
    assert lx.should_update_threshold(None, 5) is False
    assert lx.should_update_threshold(4, None) is False


# ── pct_move / trend_direction / lynx_score ───────────────────

def test_pct_move_known_value():
    closes = [100.0] * 7
    closes[-1] = 105.0
    assert abs(lx.pct_move(closes, 6) - 5.0) < 1e-9


def test_trend_direction_thresholds():
    assert lx.trend_direction(2.0, 1.0) == "LONG"
    assert lx.trend_direction(-2.0, 1.0) == "SHORT"
    assert lx.trend_direction(0.5, 1.0) is None


def test_lynx_score_full_stack():
    # 4h +5% (>= 4 → +3) + 1h aligned (+2) + SM aligned (+2) + vol rising (+1) = 8
    assert lx.lynx_score(5.0, True, True, True) == 8


def test_lynx_score_partial():
    # 4h +1.5% (>= 1 but < 2 → +1) + no 1h + no SM + no vol = 1
    assert lx.lynx_score(1.5, False, False, False) == 1


def test_lynx_score_no_trend():
    assert lx.lynx_score(None, True, True, True) == 0


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
