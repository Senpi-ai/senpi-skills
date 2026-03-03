"""
Tests for goal-engine.py — clearinghouse response guards.
Verifies that recalculate_goal handles error/empty clearinghouse responses
gracefully instead of crashing.
"""

import pytest
from datetime import datetime, timezone

from conftest import import_script

goal = import_script("goal_engine", "goal-engine.py")


def _make_deps(ch_response):
    """Build minimal deps for recalculate_goal with a given clearinghouse response."""
    return {
        "get_clearinghouse": lambda wallet: ch_response,
        "days_remaining": lambda config: 5,
        "day_number": lambda config: 2,
        "now_utc": lambda: datetime(2025, 1, 1, tzinfo=timezone.utc),
        "set_halt_state": lambda state, halt, reason: None,
        "get_active_positions": lambda state: {},
    }


class TestClearinghouseGuard:
    """Tests for empty response guards in recalculate_goal."""

    def test_error_response_returns_error(self, sample_config, sample_state):
        """Clearinghouse returning error dict → returns error, no crash."""
        deps = _make_deps({"error": "timeout after 3 attempts"})
        result = goal.recalculate_goal(sample_config, sample_state, deps)
        assert "error" in result
        assert "Clearinghouse fetch failed" in result["error"]

    def test_none_response_returns_error(self, sample_config, sample_state):
        """Clearinghouse returning None → returns error, no crash."""
        deps = _make_deps(None)
        result = goal.recalculate_goal(sample_config, sample_state, deps)
        assert "error" in result
        assert "no response" in result["error"]

    def test_empty_dict_response_returns_error(self, sample_config, sample_state):
        """Clearinghouse returning {} → falsy empty dict."""
        deps = _make_deps({})
        result = goal.recalculate_goal(sample_config, sample_state, deps)
        assert "error" in result

    def test_no_wallet_returns_error(self, sample_state):
        """Missing strategyWallet → returns error before calling clearinghouse."""
        import tiger_config
        config = tiger_config._to_alias_dict(tiger_config.deep_merge(
            tiger_config.DEFAULT_CONFIG,
            {"strategyWallet": ""}
        ))
        deps = _make_deps(None)
        result = goal.recalculate_goal(config, sample_state, deps)
        assert "error" in result
        assert "wallet" in result["error"].lower()
