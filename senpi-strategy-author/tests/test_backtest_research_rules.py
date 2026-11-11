"""An offline backtest of a strategy change is allowed, so its method has to be written down: an
agent that wrote its own replay used outcomes not yet known at entry, final closes of candles still
forming, a made-up "current" DSL ladder instead of the deployed one, and called the in-sample result
"validated". The resident line points at the rules; the rules name each of those failures."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "SKILL.md")
SHADOW = os.path.join(HERE, "..", "references", "shadow-testing.md")


def test_skill_points_backtests_at_the_rules():
    text = open(SKILL, encoding="utf-8").read()
    assert "an offline backtest follows the rules in" in text


def test_the_rules_name_each_leak():
    text = open(SHADOW, encoding="utf-8").read()
    for needle in ("## An offline backtest — the rules",
                   "known at that moment",
                   "A candle counts only once it has closed",
                   "skipped trade",
                   "deployed `runtime.yaml`",
                   "stop first",
                   "exploratory",
                   "never \"validated\"",
                   "lower bound ≤ upper bound"):
        assert needle in text, needle
