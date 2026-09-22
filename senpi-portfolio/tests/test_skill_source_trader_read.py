"""On a copy-following strategy, "what did the trader do?" is a question about the SOURCE trader.
The SKILL must send the agent to the source address, and must name the trap — the same discovery
tools run on the user's own strategy wallet return the user's copy of the trade and read as an answer."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")


def test_skill_resolves_the_trader_to_the_copied_trader():
    text = open(SKILL, encoding="utf-8").read()
    for needle in ("that is the COPIED trader, never the user's own wallet",
                   "Resolve the source",
                   "discovery_get_trader_state",
                   "discovery_get_trader_history",
                   "never substitute the user's wallet"):
        assert needle in text, needle
