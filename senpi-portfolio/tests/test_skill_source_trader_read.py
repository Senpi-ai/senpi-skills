"""On a copy-trade, "what did the trader do?" is a question about the COPIED trader. The same
discovery tools run on the user's own strategy wallet return the user's copy of the trade and read
as an answer — the rule has to name that, and has to sit where a plain `read` delivers it."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")
# OpenClaw keeps ~11.8k chars of a long tool result's head (plus a tail); this SKILL.md is ~90k.
READ_HEAD = 11_000


def test_copy_trade_question_resolves_to_the_copied_trader_in_the_delivered_head():
    text = open(SKILL, encoding="utf-8").read()
    start = text.index('"What did the trader do?" on a copy-trade')
    rule = text[start:text.index("\n> - ", start)]
    assert start + len(rule) < READ_HEAD, f"rule ends at char {start + len(rule)} — past what `read` delivers"
    for needle in ("the COPIED trader, never the user's wallet",
                   "`mirror_of` / `traderAddress` are masked",   # MCP withShortTraderAddress masks both
                   "ask for the full address",
                   "discovery_get_trader_state", "discovery_get_trader_history"):
        assert needle in rule, needle
