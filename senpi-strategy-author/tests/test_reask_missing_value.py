"""The agent asks "what dollar amount per position?", the user replies "So?", and the agent picks a
number itself and edits the live runtime.yaml. A reply without the asked-for value is not a choice."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")


def test_missing_value_is_asked_again_with_a_recommendation():
    text = open(SKILL, encoding="utf-8").read()
    for needle in ("A value you asked for and did not get is still open",
                   "I'd use $X because Y. OK?",
                   "edit\nnothing until they answer"):
        assert needle in text, needle
