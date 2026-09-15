"""A raw position's `protected: false` is narrated as "no ratchet," never as "no stop": the SKILL tells the
agent to read the wallet's resting orders before implying a hand-placed position has no stop."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")


def test_skill_reads_resting_orders_before_saying_no_stop():
    text = open(SKILL, encoding="utf-8").read()
    for needle in ("strategy_get_open_orders",
                   "reduce-only trigger order",
                   "a static stop rests at",
                   "a Take Profit order is not",
                   "no ratchet and no stop order"):
        assert needle in text, needle
