"""
Tests for prescreener.py — instrument scoring and pre-screening.
Verifies scoring components, filters, and instance dir file output.
"""

import json
import time
import os
import pytest

import prescreener
import tiger_config


class TestScoreInstrument:
    """Score calculation with hand-computed expected values."""

    def _make_instrument(self, name="ETH", max_leverage=20, day_vol=100_000_000,
                         prev_day_px=100, mark_px=105, funding=0.0005,
                         oi=5_000_000, is_delisted=False):
        return {
            "name": name,
            "max_leverage": max_leverage,
            "is_delisted": is_delisted,
            "context": {
                "dayNtlVlm": str(day_vol),
                "prevDayPx": str(prev_day_px),
                "markPx": str(mark_px),
                "funding": str(funding),
                "openInterest": str(oi),
            }
        }

    def test_full_score_calculation(self):
        """All components maxed out."""
        inst = self._make_instrument(
            prev_day_px=100, mark_px=105,   # 5% change -> momentum=1.0
            funding=0.0005,                  # ann=54.75% -> funding=min(54.75/50,1)=1.0
            oi=5_000_000, day_vol=1_000_000, # oi_vol=5.0 -> oi_score=min((5-0.5)/4,1)=1.0
        )
        config = {"minLeverage": 5}
        result = prescreener.score_instrument(inst, config)

        assert result is not None
        # momentum_score = min(5/5, 1) = 1.0
        assert result["momentum_score"] == 1.0
        # funding_score = min(54.75/50, 1) = 1.0
        assert result["funding_score"] == 1.0
        # oi_score = min(max(5.0-0.5, 0)/4, 1) = min(4.5/4, 1) = 1.0
        assert result["oi_score"] == 1.0
        # partial = 1.0*0.35 + 1.0*0.20 + 1.0*0.15 = 0.70
        assert result["partial_score"] == pytest.approx(0.70, abs=0.001)

    def test_zero_change(self):
        inst = self._make_instrument(prev_day_px=100, mark_px=100, funding=0, oi=0)
        config = {"minLeverage": 5}
        result = prescreener.score_instrument(inst, config)

        assert result is not None
        assert result["momentum_score"] == 0.0
        assert result["funding_score"] == 0.0
        assert result["oi_score"] == 0.0
        assert result["partial_score"] == 0.0

    def test_momentum_caps_at_1(self):
        """10% price change → momentum_score = min(10/5, 1) = 1.0"""
        inst = self._make_instrument(prev_day_px=100, mark_px=110)
        config = {"minLeverage": 5}
        result = prescreener.score_instrument(inst, config)

        assert result["momentum_score"] == 1.0

    def test_partial_momentum(self):
        """2.5% price change → momentum_score = 2.5/5 = 0.5"""
        inst = self._make_instrument(prev_day_px=100, mark_px=102.5)
        config = {"minLeverage": 5}
        result = prescreener.score_instrument(inst, config)

        assert result["momentum_score"] == pytest.approx(0.5, abs=0.001)

    def test_negative_price_change(self):
        """Negative moves also score for momentum (abs value)."""
        inst = self._make_instrument(prev_day_px=100, mark_px=95)
        config = {"minLeverage": 5}
        result = prescreener.score_instrument(inst, config)

        # abs_change = 5% → momentum = 1.0
        assert result["momentum_score"] == 1.0
        assert result["price_change_pct"] == -5.0

    # --- Filters ---

    def test_delisted_returns_none(self):
        inst = self._make_instrument(is_delisted=True)
        assert prescreener.score_instrument(inst, {"minLeverage": 5}) is None

    def test_blacklisted_returns_none(self):
        inst = self._make_instrument(name="PUMP")
        assert prescreener.score_instrument(inst, {"minLeverage": 5}) is None

    def test_low_leverage_returns_none(self):
        inst = self._make_instrument(max_leverage=3)
        assert prescreener.score_instrument(inst, {"minLeverage": 5}) is None

    def test_zero_volume_returns_none(self):
        inst = self._make_instrument(day_vol=0)
        assert prescreener.score_instrument(inst, {"minLeverage": 5}) is None

    def test_zero_price_returns_none(self):
        inst = self._make_instrument(mark_px=0)
        assert prescreener.score_instrument(inst, {"minLeverage": 5}) is None

    def test_uses_config_min_leverage(self):
        inst = self._make_instrument(max_leverage=8)
        assert prescreener.score_instrument(inst, {"minLeverage": 10}) is None
        assert prescreener.score_instrument(inst, {"minLeverage": 5}) is not None


class TestMainIntegration:
    def test_writes_to_instance_dir(self, tmp_runtime):
        """Prescreened.json must be written in instance dir."""
        config = tiger_config.load_config(runtime=tmp_runtime)

        instruments = [
            {
                "name": f"ASSET{i}", "max_leverage": 20, "is_delisted": False,
                "context": {
                    "dayNtlVlm": str(10_000_000 + i * 1_000_000),
                    "prevDayPx": "100", "markPx": str(100 + i),
                    "funding": "0.0001", "openInterest": str(50000 + i * 1000),
                }
            }
            for i in range(40)
        ]

        captured = []
        deps = tiger_config.resolve_dependencies(
            runtime=tmp_runtime,
            overrides={
                "output": lambda payload: captured.append(payload),
                "get_all_instruments": lambda: instruments,
            },
        )

        prescreener.main(deps)

        # Check file was written in instance dir
        expected_path = os.path.join(tmp_runtime.state_dir, "test-strategy", "prescreened.json")
        assert os.path.exists(expected_path)

        with open(expected_path) as f:
            data = json.load(f)

        assert len(data["candidates"]) == prescreener.TOP_N
        assert "group_a" in data
        assert "group_b" in data
        assert data["total_screened"] > 0
