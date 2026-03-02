"""
Tests for roar-analyst.py — ROAR meta-optimizer.
Verifies build_scorecard aggregation, generate_proposed_changes rules,
and windowed revert logic.
"""

import copy
import pytest
from datetime import datetime, timezone, timedelta

from conftest import import_script
import roar_config

roar = import_script("roar_analyst", "roar-analyst.py")


class TestBuildScorecard:
    def test_basic_aggregation(self):
        trades = [
            {"pattern": "COMPRESSION_BREAKOUT", "pnl_pct": 5.0},
            {"pattern": "COMPRESSION_BREAKOUT", "pnl_pct": -2.0},
            {"pattern": "COMPRESSION_BREAKOUT", "pnl_pct": 3.0},
        ]
        sc = roar.build_scorecard(trades, {})

        p = sc["by_pattern"]["COMPRESSION_BREAKOUT"]
        assert p["trades"] == 3
        assert p["wins"] == 2  # 5.0 and 3.0
        assert p["losses"] == 1  # -2.0
        # win_rate = 2/3
        assert p["win_rate"] == pytest.approx(0.667, abs=0.001)
        # avg_pnl = (5 - 2 + 3) / 3 = 2.0
        assert p["avg_pnl_pct"] == 2.0
        # expectancy = same = 2.0
        assert p["expectancy"] == 2.0

        assert sc["total_trades"] == 3
        assert sc["overall_win_rate"] == pytest.approx(0.667, abs=0.001)
        assert sc["overall_avg_pnl"] == 2.0

    def test_multiple_patterns(self):
        trades = [
            {"pattern": "COMPRESSION_BREAKOUT", "pnl_pct": 5.0},
            {"pattern": "CORRELATION_LAG", "pnl_pct": -3.0},
            {"pattern": "COMPRESSION_BREAKOUT", "pnl_pct": -1.0},
            {"pattern": "CORRELATION_LAG", "pnl_pct": 4.0},
        ]
        sc = roar.build_scorecard(trades, {})

        assert len(sc["by_pattern"]) == 2
        cb = sc["by_pattern"]["COMPRESSION_BREAKOUT"]
        cl = sc["by_pattern"]["CORRELATION_LAG"]
        assert cb["trades"] == 2
        assert cl["trades"] == 2
        assert sc["total_trades"] == 4
        # overall_win_rate = 2/4 = 0.5 (5.0 and 4.0 are wins)
        assert sc["overall_win_rate"] == 0.5

    def test_empty_trades(self):
        sc = roar.build_scorecard([], {})
        assert sc["total_trades"] == 0
        assert sc["overall_win_rate"] == 0
        assert sc["by_pattern"] == {}

    def test_zero_pnl_counts_as_loss(self):
        """pnl_pct = 0 should count as a loss (not > 0)."""
        trades = [{"pattern": "P1", "pnl_pct": 0.0}]
        sc = roar.build_scorecard(trades, {})
        assert sc["by_pattern"]["P1"]["wins"] == 0
        assert sc["by_pattern"]["P1"]["losses"] == 1

    def test_hold_duration_calculated(self):
        """Hold duration from entry_time to exit_time."""
        now = datetime.now(timezone.utc)
        trades = [{
            "pattern": "P1", "pnl_pct": 5.0,
            "entry_time": (now - timedelta(hours=2)).isoformat(),
            "exit_time": now.isoformat(),
        }]
        sc = roar.build_scorecard(trades, {})
        # 2 hours = 120 minutes
        assert sc["by_pattern"]["P1"]["avg_hold_minutes"] == pytest.approx(120, abs=1)

    def test_dsl_exit_tier_averaged(self):
        trades = [
            {"pattern": "P1", "pnl_pct": 5.0, "dsl_exit_tier": 2},
            {"pattern": "P1", "pnl_pct": 3.0, "dsl_exit_tier": 4},
        ]
        sc = roar.build_scorecard(trades, {})
        assert sc["by_pattern"]["P1"]["avg_dsl_exit_tier"] == 3.0

    def test_confluence_score_averaged(self):
        trades = [
            {"pattern": "P1", "pnl_pct": 5.0, "confluence_score": 0.7},
            {"pattern": "P1", "pnl_pct": 3.0, "confluence_score": 0.5},
        ]
        sc = roar.build_scorecard(trades, {})
        assert sc["by_pattern"]["P1"]["avg_confluence_at_entry"] == pytest.approx(0.6, abs=0.001)


class TestGenerateProposedChanges:
    def _base_config(self):
        return {
            "minConfluenceScore": {"NORMAL": 0.5, "CONSERVATIVE": 0.7, "ELEVATED": 0.4},
            "patternConfluenceOverrides": {},
            "dslRetrace": {"phase1": 0.015, "phase2": 0.012},
        }

    def test_rule1_low_win_rate_raises_threshold(self):
        """Rule 1: win_rate < 40% → raise confluence threshold by 0.05."""
        scorecard = {
            "by_pattern": {
                "COMPRESSION_BREAKOUT": {
                    "trades": 15, "win_rate": 0.35, "expectancy": -0.5,
                    "avg_dsl_exit_tier": None, "last_entry_ts": None,
                }
            },
            "total_trades": 15,
        }
        config = self._base_config()
        roar_state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)

        changes = roar.generate_proposed_changes(scorecard, config, roar_state)

        # Rule 1 triggers for COMPRESSION_BREAKOUT
        confluece_changes = [c for c in changes if c["key"].startswith("patternConfluenceOverrides")]
        assert len(confluece_changes) >= 1
        c = confluece_changes[0]
        # old = None fallback to NORMAL = 0.5, new = 0.5 + 0.05 = 0.55
        assert c["new"] == 0.55

    def test_rule2_high_win_rate_lowers_threshold(self):
        """Rule 2: win_rate > 70% → lower confluence threshold by 0.03."""
        scorecard = {
            "by_pattern": {
                "COMPRESSION_BREAKOUT": {
                    "trades": 15, "win_rate": 0.75, "expectancy": 2.0,
                    "avg_dsl_exit_tier": None, "last_entry_ts": None,
                }
            },
            "total_trades": 15,
        }
        config = self._base_config()
        roar_state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)

        changes = roar.generate_proposed_changes(scorecard, config, roar_state)

        conf_changes = [c for c in changes if c["key"].startswith("patternConfluenceOverrides")]
        assert len(conf_changes) >= 1
        # old = 0.5, new = 0.5 - 0.03 = 0.47
        assert conf_changes[0]["new"] == 0.47

    def test_rule3_low_exit_tier_loosens_retrace(self):
        """Rule 3: avg DSL exit tier < 2 → loosen phase1 retrace by 0.002."""
        scorecard = {
            "by_pattern": {
                "COMPRESSION_BREAKOUT": {
                    "trades": 10, "win_rate": 0.55, "expectancy": 1.0,
                    "avg_dsl_exit_tier": 1.5, "last_entry_ts": None,
                }
            },
            "total_trades": 10,
        }
        config = self._base_config()
        roar_state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)

        changes = roar.generate_proposed_changes(scorecard, config, roar_state)

        retrace_changes = [c for c in changes if c["key"] == "dslRetrace.phase1"]
        assert len(retrace_changes) >= 1
        # old = 0.015, new = 0.015 + 0.002 = 0.017
        assert retrace_changes[0]["new"] == 0.017

    def test_rule4_high_exit_tier_tightens_retrace(self):
        """Rule 4: avg DSL exit tier >= 4 → tighten phase1 retrace by 0.001."""
        scorecard = {
            "by_pattern": {
                "COMPRESSION_BREAKOUT": {
                    "trades": 10, "win_rate": 0.55, "expectancy": 1.0,
                    "avg_dsl_exit_tier": 4.5, "last_entry_ts": None,
                }
            },
            "total_trades": 10,
        }
        config = self._base_config()
        roar_state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)

        changes = roar.generate_proposed_changes(scorecard, config, roar_state)

        retrace_changes = [c for c in changes if c["key"] == "dslRetrace.phase1"]
        assert len(retrace_changes) >= 1
        # old = 0.015, new = 0.015 - 0.001 = 0.014
        assert retrace_changes[0]["new"] == 0.014

    def test_rule5_stale_signal_lowers_threshold(self):
        """Rule 5: No entries in 48h → lower confluence by 0.02."""
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        scorecard = {
            "by_pattern": {
                "COMPRESSION_BREAKOUT": {
                    "trades": 10, "win_rate": 0.55, "expectancy": 1.0,
                    "avg_dsl_exit_tier": None, "last_entry_ts": old_ts,
                }
            },
            "total_trades": 10,
        }
        config = self._base_config()
        roar_state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)

        changes = roar.generate_proposed_changes(scorecard, config, roar_state)

        stale_changes = [c for c in changes if "no entries" in c.get("reason", "")]
        assert len(stale_changes) >= 1
        # old = 0.5, new = 0.5 - 0.02 = 0.48
        assert stale_changes[0]["new"] == 0.48

    def test_rule6_negative_expectancy_disables_pattern(self):
        """Rule 6: Negative expectancy with >= 20 trades → disable pattern."""
        scorecard = {
            "by_pattern": {
                "BAD_PATTERN": {
                    "trades": 25, "win_rate": 0.30, "expectancy": -1.5,
                    "avg_dsl_exit_tier": None, "last_entry_ts": None,
                }
            },
            "total_trades": 25,
        }
        config = self._base_config()
        roar_state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)

        changes = roar.generate_proposed_changes(scorecard, config, roar_state)

        disable_changes = [c for c in changes if c["key"].startswith("_disable_pattern")]
        assert len(disable_changes) >= 1
        assert "BAD_PATTERN" in roar_state["disabled_patterns"]

    def test_no_changes_insufficient_trades(self):
        """Rules require minimum trade counts."""
        scorecard = {
            "by_pattern": {
                "COMPRESSION_BREAKOUT": {
                    "trades": 3, "win_rate": 0.10, "expectancy": -5.0,
                    "avg_dsl_exit_tier": 1.0, "last_entry_ts": None,
                }
            },
            "total_trades": 3,
        }
        config = self._base_config()
        roar_state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)

        changes = roar.generate_proposed_changes(scorecard, config, roar_state)
        # Rule 1 needs 10 trades, Rule 3 needs 5, Rule 6 needs 20
        # 3 trades is insufficient for all rules
        assert len(changes) == 0

    def test_bounds_enforcement(self):
        """Rule 1 output must be clamped to TUNABLE_BOUNDS."""
        # If old confluence is already near max (0.85), adding 0.05 should clamp
        scorecard = {
            "by_pattern": {
                "COMPRESSION_BREAKOUT": {
                    "trades": 15, "win_rate": 0.35, "expectancy": -0.5,
                    "avg_dsl_exit_tier": None, "last_entry_ts": None,
                }
            },
            "total_trades": 15,
        }
        config = self._base_config()
        config["patternConfluenceOverrides"] = {"COMPRESSION_BREAKOUT": 0.83}
        roar_state = copy.deepcopy(roar_config.DEFAULT_ROAR_STATE)

        changes = roar.generate_proposed_changes(scorecard, config, roar_state)

        conf_changes = [c for c in changes if c["key"].startswith("patternConfluenceOverrides")]
        assert len(conf_changes) >= 1
        # 0.83 + 0.05 = 0.88, clamped to 0.85
        assert conf_changes[0]["new"] == 0.85


class TestWindowedRevert:
    """H4 fix: revert should compare post-adjustment trades only."""

    def test_revert_not_triggered_with_few_post_trades(self):
        """Even if stats are bad, don't revert if post-adjustment trades < 5."""
        current_stats = {"overall_win_rate": 0.20, "overall_avg_pnl": -5.0, "total_trades": 3}
        previous_stats = {"overall_win_rate": 0.60, "overall_avg_pnl": 2.0}
        assert roar_config.should_revert(current_stats, previous_stats) is False

    def test_revert_triggered_with_enough_trades(self):
        """Revert when both metrics worse AND enough post-adjustment trades."""
        current_stats = {"overall_win_rate": 0.30, "overall_avg_pnl": -1.0, "total_trades": 10}
        previous_stats = {"overall_win_rate": 0.60, "overall_avg_pnl": 2.0}
        assert roar_config.should_revert(current_stats, previous_stats) is True

    def test_no_revert_when_only_win_rate_worse(self):
        current_stats = {"overall_win_rate": 0.40, "overall_avg_pnl": 3.0, "total_trades": 10}
        previous_stats = {"overall_win_rate": 0.60, "overall_avg_pnl": 2.0}
        assert roar_config.should_revert(current_stats, previous_stats) is False

    def test_no_revert_when_only_pnl_worse(self):
        current_stats = {"overall_win_rate": 0.65, "overall_avg_pnl": -0.5, "total_trades": 10}
        previous_stats = {"overall_win_rate": 0.60, "overall_avg_pnl": 2.0}
        assert roar_config.should_revert(current_stats, previous_stats) is False
