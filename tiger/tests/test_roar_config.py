"""
Tests for roar_config.py — bounds enforcement, revert logic, pattern management.
Verifies H4 fix (windowed revert) and bounds correctness.
"""

import json
import os
import copy
import pytest
from datetime import datetime, timezone, timedelta

import roar_config


class TestClampToBounds:
    def test_within_bounds(self):
        # dslRetrace.phase1 bounds: (0.008, 0.03)
        assert roar_config.clamp_to_bounds("dslRetrace.phase1", 0.015) == 0.015

    def test_above_max(self):
        assert roar_config.clamp_to_bounds("dslRetrace.phase1", 0.05) == 0.03

    def test_below_min(self):
        assert roar_config.clamp_to_bounds("dslRetrace.phase1", 0.001) == 0.008

    def test_pattern_confluence_override(self):
        # patternConfluenceOverrides.* uses generic 0.25-0.85 range
        assert roar_config.clamp_to_bounds("patternConfluenceOverrides.TEST", 0.90) == 0.85
        assert roar_config.clamp_to_bounds("patternConfluenceOverrides.TEST", 0.10) == 0.25

    def test_unknown_key_passthrough(self):
        # Unknown keys are not clamped
        assert roar_config.clamp_to_bounds("unknownKey", 999) == 999


class TestIsProtected:
    def test_budget_protected(self):
        assert roar_config.is_protected("budget") is True

    def test_max_leverage_protected(self):
        assert roar_config.is_protected("maxLeverage") is True

    def test_strategy_wallet_protected(self):
        assert roar_config.is_protected("strategyWallet") is True

    def test_scanner_threshold_not_protected(self):
        assert roar_config.is_protected("bbSqueezePercentile") is False

    def test_confluence_not_protected(self):
        assert roar_config.is_protected("minConfluenceScore.NORMAL") is False


class TestIsWithinBounds:
    def test_within(self):
        assert roar_config.is_within_bounds("bbSqueezePercentile", 25) is True

    def test_at_boundary(self):
        # bbSqueezePercentile: (15, 45)
        assert roar_config.is_within_bounds("bbSqueezePercentile", 15) is True
        assert roar_config.is_within_bounds("bbSqueezePercentile", 45) is True

    def test_outside(self):
        assert roar_config.is_within_bounds("bbSqueezePercentile", 50) is False
        assert roar_config.is_within_bounds("bbSqueezePercentile", 10) is False


class TestGetSetNested:
    def test_get_flat(self):
        d = {"a": 1}
        assert roar_config.get_nested(d, "a") == 1

    def test_get_nested(self):
        d = {"x": {"y": {"z": 42}}}
        assert roar_config.get_nested(d, "x.y.z") == 42

    def test_get_missing(self):
        assert roar_config.get_nested({}, "a.b.c") is None

    def test_set_creates_path(self):
        d = {}
        roar_config.set_nested(d, "a.b.c", 99)
        assert d["a"]["b"]["c"] == 99

    def test_set_overwrites(self):
        d = {"a": {"b": 1}}
        roar_config.set_nested(d, "a.b", 2)
        assert d["a"]["b"] == 2


class TestShouldRevert:
    def test_both_worse_triggers_revert(self):
        current = {"overall_win_rate": 0.40, "overall_avg_pnl": -0.5, "total_trades": 10}
        previous = {"overall_win_rate": 0.55, "overall_avg_pnl": 1.0}
        assert roar_config.should_revert(current, previous) is True

    def test_only_win_rate_worse_no_revert(self):
        current = {"overall_win_rate": 0.40, "overall_avg_pnl": 1.5, "total_trades": 10}
        previous = {"overall_win_rate": 0.55, "overall_avg_pnl": 1.0}
        assert roar_config.should_revert(current, previous) is False

    def test_only_pnl_worse_no_revert(self):
        current = {"overall_win_rate": 0.60, "overall_avg_pnl": -0.5, "total_trades": 10}
        previous = {"overall_win_rate": 0.55, "overall_avg_pnl": 1.0}
        assert roar_config.should_revert(current, previous) is False

    def test_insufficient_trades_no_revert(self):
        """H4 fix: must have >= MIN_POST_ADJUSTMENT_TRADES to judge."""
        current = {"overall_win_rate": 0.20, "overall_avg_pnl": -5.0, "total_trades": 3}
        previous = {"overall_win_rate": 0.55, "overall_avg_pnl": 1.0}
        assert roar_config.should_revert(current, previous) is False

    def test_no_previous_stats_no_revert(self):
        assert roar_config.should_revert({"overall_win_rate": 0.5}, None) is False
        assert roar_config.should_revert(None, {"overall_win_rate": 0.5}) is False

    def test_exactly_min_trades(self):
        """At exactly MIN_POST_ADJUSTMENT_TRADES, revert should be possible."""
        min_trades = roar_config.MIN_POST_ADJUSTMENT_TRADES
        current = {"overall_win_rate": 0.30, "overall_avg_pnl": -1.0, "total_trades": min_trades}
        previous = {"overall_win_rate": 0.55, "overall_avg_pnl": 1.0}
        assert roar_config.should_revert(current, previous) is True


class TestPatternDisableEnable:
    def test_disable_sets_expiry(self):
        state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)
        roar_config.disable_pattern(state, "TEST_PATTERN")
        assert "TEST_PATTERN" in state["disabled_patterns"]
        expiry = datetime.fromisoformat(state["disabled_patterns"]["TEST_PATTERN"])
        now = datetime.now(timezone.utc)
        # Should expire ~48h from now
        assert (expiry - now).total_seconds() > 47 * 3600
        assert (expiry - now).total_seconds() < 49 * 3600

    def test_is_pattern_disabled(self):
        state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)
        assert roar_config.is_pattern_disabled(state, "X") is False
        roar_config.disable_pattern(state, "X")
        assert roar_config.is_pattern_disabled(state, "X") is True

    def test_check_re_enable_expired(self):
        state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        state["disabled_patterns"]["EXPIRED"] = past
        re_enabled = roar_config.check_re_enable(state)
        assert "EXPIRED" in re_enabled
        assert "EXPIRED" not in state["disabled_patterns"]

    def test_check_re_enable_not_expired(self):
        state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        state["disabled_patterns"]["ACTIVE"] = future
        re_enabled = roar_config.check_re_enable(state)
        assert re_enabled == []
        assert "ACTIVE" in state["disabled_patterns"]


class TestApplyChangeset:
    def test_applies_changes(self):
        config = {"bbSqueezePercentile": 30}
        state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)
        changeset = [{"key": "bbSqueezePercentile", "old": 30, "new": 25, "reason": "test", "confidence": 0.8}]
        result = roar_config.apply_changeset(changeset, config, state)
        assert result["bbSqueezePercentile"] == 25

    def test_skips_protected_keys(self):
        config = {"budget": 1000}
        state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)
        changeset = [{"key": "budget", "old": 1000, "new": 500, "reason": "test", "confidence": 1.0}]
        result = roar_config.apply_changeset(changeset, config, state)
        assert result["budget"] == 1000  # Unchanged

    def test_clamps_to_bounds(self):
        config = {"bbSqueezePercentile": 30}
        state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)
        changeset = [{"key": "bbSqueezePercentile", "old": 30, "new": 99, "reason": "test", "confidence": 1.0}]
        result = roar_config.apply_changeset(changeset, config, state)
        # bbSqueezePercentile max = 45
        assert result["bbSqueezePercentile"] == 45

    def test_saves_previous_config(self):
        config = {"bbSqueezePercentile": 30}
        state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)
        changeset = [{"key": "bbSqueezePercentile", "old": 30, "new": 25, "reason": "test", "confidence": 0.8}]
        roar_config.apply_changeset(changeset, config, state)
        assert state["previous_config"]["bbSqueezePercentile"] == 30


class TestRoarStateFile:
    def test_in_instance_dir(self, tmp_runtime):
        """H5 fix: roar-state.json must be in instance dir."""
        config = tiger_config.load_config(runtime=tmp_runtime)
        path = roar_config._roar_state_file(config=config, runtime=tmp_runtime)
        assert "test-strategy" in path
        assert path.endswith("roar-state.json")

    def test_save_load_roundtrip(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        state = roar_config.load_roar_state(config=config, runtime=tmp_runtime)
        state["run_count"] = 42
        roar_config.save_roar_state(state, config=config, runtime=tmp_runtime)
        loaded = roar_config.load_roar_state(config=config, runtime=tmp_runtime)
        assert loaded["run_count"] == 42


# Need tiger_config for fixtures
import tiger_config
