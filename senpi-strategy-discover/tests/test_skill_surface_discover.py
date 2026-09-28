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


def test_card_format_never_prints_the_raw_tier_field():
    """The card example once ended in a `[{tier}]` placeholder, so every template line came out as a raw
    `[{tier: advanced}]` tag. The badge is the literal `[STARTER]`, only for `tier == "starter"`."""
    text = open(SKILL, encoding="utf-8").read()
    section = text.split("### Card format", 1)[1].split("\n## ", 1)[0]
    example = section.split("```")[1]
    assert "{tier" not in example and "[{" not in example, example
    flat = " ".join(section.split())
    for needle in ('`[STARTER]` after the name iff `tier == "starter"`',
                   "carries no tier label at all",
                   "never printed raw"):
        assert needle in flat, needle
