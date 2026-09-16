#!/usr/bin/env python3
"""The AI-credits rules are resident in senpi-account-status/SKILL.md — the meter is explained, never
read — the router can find the credits questions from the frontmatter, and the version the README
advertises is the version the skill carries."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "senpi-account-status" / "SKILL.md"
README = REPO / "README.md"
SECTION = "## AI credits — the usage meter"


def _split(path):
    """(frontmatter, body) — the body is everything after the closing `---`."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def _flat(text):
    """Prose needles must survive a re-wrap: collapse every run of whitespace to one space."""
    return re.sub(r"\s+", " ", text)


def _section(body):
    start = body.find(SECTION)
    assert start >= 0, "SKILL.md lost its AI-credits section"
    rest = body[start + len(SECTION):]
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


if __name__ == "__main__":
    unittest.main()
