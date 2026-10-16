"""The honest answer to "which templates are proven?" is resident in SKILL.md — in the skill the agent
loads, not in a reference it may skip.

    python3 -m pytest senpi-strategy-discover/tests/test_skill_surface_discover.py -q
"""
import os
import re

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")


def test_proven_templates_answer_is_resident():
    text = open(SKILL, encoding="utf-8").read()
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    body = text[m.end():] if m else text
    for needle in ("has no backtest", "mirror-only"):
        assert needle in body, needle


def test_skill_says_the_fee_math_on_micro_specs_and_links_the_public_source():
    text = open(SKILL, encoding="utf-8").read()
    for needle in ("do the fee math out loud",
                   "the only fee that tool carries",
                   "Never save an impossible spec",
                   "https://github.com/Senpi-ai/senpi-skills/tree/main/strategies/",
                   "Never hand-roll a tarball"):
        assert needle in text, needle


def test_skill_forbids_recommending_from_the_unthemed_neutral_order_shortlist():
    """discover.py returns 8 of the eligible set by default; without --theme those 8 are alphabetical,
    so the skill must route soft preferences into --theme (or ask) instead of picking from them."""
    text = " ".join(open(SKILL, encoding="utf-8").read().split())
    for needle in ("Never recommend from a neutral-order shortlist",
                   "`meta.families_count > meta.returned_n`",
                   "Re-run with `--theme` built from what the user has told you",
                   "say how many fit (`eligible_count`) and ask one short question",
                   "Soft preferences go in `--theme`, never in a filter",
                   "`--limit N` is the browse path only"):
        assert needle in text, needle
    assert "Keep risk, belief, horizon, and worldview in your head" not in text
