#!/usr/bin/env python3
"""The AI-credits rules are resident in senpi-account-status/SKILL.md — the meter is explained, never
read, while the plan catalog and the free-credit ladder ARE published — the router can find the
credits questions from the frontmatter, and the version the README advertises is the version the
skill carries."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "senpi-account-status" / "SKILL.md"
README = REPO / "README.md"
SECTION = "## AI credits — the usage meter"
PLANS = "## Plans & free credits"


def _split(path):
    """(frontmatter, body) — the body is everything after the closing `---`."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def _flat(text):
    """Prose needles must survive a re-wrap: collapse every run of whitespace to one space."""
    return re.sub(r"\s+", " ", text)


def _section(body, heading=SECTION):
    start = body.find(heading)
    assert start >= 0, f"SKILL.md lost its {heading!r} section"
    rest = body[start + len(heading):]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


class CreditsAreTheUsageMeter(unittest.TestCase):
    def test_the_section_and_its_rules_are_in_the_body(self):
        body = _flat(_split(SKILL)[1])
        self.assertIn(SECTION, body)
        for needle in (
            "I can't read it from here",
            "the whole conversation is re-sent each turn",
            "cannot be traded, withdrawn, deposited into a strategy, or moved anywhere",
            '"chatting is free"',
            "depends on your plan",
        ):
            self.assertIn(needle, body, needle)

    def test_the_never_say_column_carries_the_four_failure_modes(self):
        section = _flat(_section(_split(SKILL)[1]))
        self.assertIn("| Say | Never say |", section)
        for needle in (
            '"chatting is free"',
            "plan size you did not read",
            "tradable or withdrawable",
            "Blaming a shrinking meter on trading losses",
        ):
            self.assertIn(needle, section, needle)

    def test_a_strategy_question_and_any_language_land_on_the_meter(self):
        # "credits" asked about a strategy, or in another language, is still the AI-credit meter: the
        # runtime ticks without a model call, the agent's own crons are what spend, and an unclear
        # "credits" is asked about rather than read as funding or leverage
        section = _flat(_section(_split(SKILL)[1]))
        for needle in (
            "ticks without a model call",
            "decision_mode: llm",
            "each firing is a full model call",
            "does my strategy use credits?",
            "in any language",
            "unless the user says leverage, a loan or funding",
            "a wallet or strategy balance offered as proof about credits",
        ):
            self.assertIn(needle, section, needle)
        self.assertIn("does my strategy use credits?", _flat(_split(SKILL)[0]))

    def test_no_plan_size_or_dollar_amount_in_the_section(self):
        # plan sizes change; the text says "depends on your plan" and never carries a figure
        section = _flat(_section(_split(SKILL)[1]))
        self.assertNotIn("$", section)
        self.assertIsNone(re.search(r"\b\d[\d,]*\s*credits\b", section), "a plan size crept in")

    def test_the_router_can_find_the_credits_questions(self):
        frontmatter = _split(SKILL)[0]
        desc = re.search(r"description: >-\n((?:  .*(?:\n|$))+)", frontmatter).group(1)
        for needle in ("credits", "meter", "bubble"):
            self.assertIn(needle, desc, needle)

    def test_readme_row_matches_the_skill_version(self):
        if not README.is_file():
            self.skipTest("README not present (installed skill layout)")
        version = re.search(r'version: "([0-9.]+)"', SKILL.read_text(encoding="utf-8")).group(1)
        self.assertIn(f"| [`senpi-account-status`](senpi-account-status/) | {version} |",
                      README.read_text(encoding="utf-8"))


class PlansAndFreeCreditsArePublished(unittest.TestCase):
    """The catalog is the half of this that IS quotable — a user asking what a plan costs gets the
    table, not a shrug at the meter. The figures come from the subscription card, so the rules that
    survive a price change (no rollover, instant upgrade, not retroactive, card-wins) matter as much
    as the numbers."""

    def test_the_four_plans_and_the_cycle_rules_are_in_the_table(self):
        section = _flat(_section(_split(SKILL)[1], PLANS))
        for plan in ("Starter", "Pro (default)", "Advanced", "Quant"):
            self.assertIn(plan, section, plan)
        for needle in (
            "Every plan includes every model",
            "no rollover, no top-ups, no overage",
            "Upgrading is instant",
            "takes effect at the next renewal",
            "Payment is web only",
        ):
            self.assertIn(needle, section, needle)

    def test_the_ladder_carries_the_milestones_and_the_rules_that_generate_tickets(self):
        section = _flat(_section(_split(SKILL)[1], PLANS))
        for needle in (
            "Deploy your agent",
            "Launch your first strategy",     # replaced the retired "fund your wallet" milestone
            "Subscribe to a plan",
            "Not retroactive",
            "expire at the next renewal",
            "Volume progress is not live",
            "never lands in Balances as USDC",
            "The card is the source of truth",
        ):
            self.assertIn(needle, section, needle)

    def test_the_trial_figure_stays_unpublished(self):
        # plan prices are fixed and quotable; the trial grant has moved more than once, so the
        # catalog may carry every plan number and still no trial number
        section = _flat(_section(_split(SKILL)[1], PLANS))
        self.assertIn("Never quote a trial figure", section)
        self.assertIn("open positions are NOT auto-closed", section)

    def test_the_meter_section_still_defers_the_balance_to_the_page(self):
        # the catalog being quotable must not leak into the balance: "what is left" is still theirs
        section = _flat(_section(_split(SKILL)[1]))
        self.assertIn("how much is **left** depends on your plan and your usage", section)


if __name__ == "__main__":
    unittest.main()
