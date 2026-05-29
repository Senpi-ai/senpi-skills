#!/usr/bin/env python3
"""Unit tests for Stag's pure functions (pct_change, sma, is_above_sma,
recent_high_bars_ago, volume_surge, is_accelerating, parabolic_score).
Stubs stag_config + senpi_runtime_helpers.
Run: python3 stag/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("stag_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["stag_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "stag-producer.py"
_spec = importlib.util.spec_from_file_location("stag_producer", _path)
stg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stg)


# ── pct_change ──────────────────────────────────────────────────

def test_pct_change_known_value():
    # 42 bars ago = 40, latest = 60 → +50%
    closes = [40.0] * 43
    closes[-1] = 60.0
    assert abs(stg.pct_change(closes, 42) - 50.0) < 1e-9


def test_pct_change_insufficient_and_bad_ref():
    assert stg.pct_change([100, 101], 42) is None
    closes = [0.0] * 43
    assert stg.pct_change(closes, 42) is None


# ── sma + is_above_sma ─────────────────────────────────────────

def test_sma_simple_average():
    closes = [10.0] * 200
    assert stg.sma(closes, 200) == 10.0
    closes2 = list(range(1, 201))  # 1..200, sum=20100, mean=100.5
    assert abs(stg.sma(closes2, 200) - 100.5) < 1e-9


def test_sma_insufficient_returns_none():
    assert stg.sma([1, 2, 3], 200) is None


def test_is_above_sma_true_when_latest_above_mean():
    closes = list(range(1, 201))  # rising → latest (200) > mean (100.5)
    assert stg.is_above_sma(closes, 200) is True
    falling = list(range(200, 0, -1))  # falling → latest (1) < mean (100.5)
    assert stg.is_above_sma(falling, 200) is False


# ── recent_high_bars_ago ───────────────────────────────────────

def test_recent_high_bars_ago_zero_when_latest_is_high():
    closes = [10.0, 12, 14, 16, 18, 20]
    # lookback 5 → window = closes[-6:] = all 6; high is at idx 5 (=20)
    assert stg.recent_high_bars_ago(closes, 5) == 0


def test_recent_high_bars_ago_counts_back_correctly():
    # high made 3 bars ago
    closes = [10.0, 12, 18, 20, 19, 18, 17]   # 20 at idx 3, latest idx 6, bars_ago = 3
    assert stg.recent_high_bars_ago(closes, 6) == 3


def test_recent_high_bars_ago_insufficient():
    assert stg.recent_high_bars_ago([1, 2], 42) is None


# ── volume_surge ───────────────────────────────────────────────

def test_volume_surge_passes_when_recent_double_baseline():
    # 36 bars @ 100 then 6 bars @ 200. baseline (all 42) mean = (3600 + 1200) / 42 ≈ 114.29.
    # recent (last 6) mean = 200. ratio = 200 / 114.29 ≈ 1.75 → passes 1.5 threshold.
    volumes = [100.0] * 36 + [200.0] * 6
    passed, ratio = stg.volume_surge(volumes, recent_bars=6, baseline_bars=42, min_ratio=1.5)
    expected_ratio = 200.0 / (4800.0 / 42.0)
    assert passed is True
    assert abs(ratio - expected_ratio) < 1e-9


def test_volume_surge_explicit_math():
    # baseline 10 bars: 6 zeros + 4 ones... too messy. Use clean values.
    # baseline 10 bars all = 50; recent 2 bars = [100, 100] → recent mean 100, baseline mean (8*50 + 2*100)/10 = (400+200)/10 = 60
    # ratio = 100/60 = 1.667 → passes 1.5
    volumes = [50.0] * 8 + [100.0, 100.0]
    passed, ratio = stg.volume_surge(volumes, recent_bars=2, baseline_bars=10, min_ratio=1.5)
    assert passed is True
    assert abs(ratio - (100.0 / 60.0)) < 1e-9


def test_volume_surge_blocks_quiet():
    # All flat → ratio 1.0 → blocked
    volumes = [50.0] * 42
    passed, ratio = stg.volume_surge(volumes, recent_bars=6, baseline_bars=42, min_ratio=1.5)
    assert passed is False and abs(ratio - 1.0) < 1e-9


def test_volume_surge_insufficient_returns_none_ratio():
    passed, ratio = stg.volume_surge([1, 2], recent_bars=6, baseline_bars=42, min_ratio=1.5)
    assert passed is False and ratio is None


# ── is_accelerating ────────────────────────────────────────────

def test_is_accelerating_recent_keeps_pace():
    # 7d move +30%, 4d move +15% → 4d is exactly half the 7d → accelerating True
    assert stg.is_accelerating(short_strength_pct=15.0, long_strength_pct=30.0) is True
    # 4d move 20% > half of 7d 30% (15%) → still True
    assert stg.is_accelerating(short_strength_pct=20.0, long_strength_pct=30.0) is True


def test_is_accelerating_blocks_decelerating():
    # 7d +30%, 4d only +5% → 4d is < half of 7d → decelerating
    assert stg.is_accelerating(short_strength_pct=5.0, long_strength_pct=30.0) is False


def test_is_accelerating_blocks_negative_long():
    # Long-window trend is down → asymmetric LONG-only gate fails
    assert stg.is_accelerating(short_strength_pct=10.0, long_strength_pct=-5.0) is False


def test_is_accelerating_handles_none():
    assert stg.is_accelerating(None, 10.0) is False
    assert stg.is_accelerating(10.0, None) is False


# ── parabolic_score ────────────────────────────────────────────

def test_parabolic_score_base_plus_bonuses():
    # All gates passing, trend moderate (25%), accelerating, vol 1.5x, sm aligned
    score, reasons = stg.parabolic_score(25.0, True, True, 1.5, True, strong_trend_pct=40.0)
    # base 3 + accel 1 = 4 (trend not strong, vol not surge ≥ 2x)
    assert score == 4
    assert "accelerating" in reasons


def test_parabolic_score_strong_trend_bonus():
    # Trend > strong threshold (40%) → +2
    score, reasons = stg.parabolic_score(50.0, True, True, 2.5, True, strong_trend_pct=40.0)
    # base 3 + strong 2 + accel 1 + vol_surge 1 = 7
    assert score == 7
    assert any("strong" in r for r in reasons)
    assert any("vol_surge_2.5x" in r for r in reasons)


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
