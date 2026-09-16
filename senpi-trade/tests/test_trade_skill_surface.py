#!/usr/bin/env python3
"""The protection protocol is resident in senpi-trade/SKILL.md, its four regression cases exist, and the
version the README advertises is the version the skill carries."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "senpi-trade" / "SKILL.md"
CASES = REPO / "senpi-trade" / "references" / "protection-cases.md"


def _body(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


class ProtectionProtocolIsResident(unittest.TestCase):
    def test_the_six_rules_and_their_words_are_in_the_body(self):
        body = _body(SKILL)
        for needle in ("## The protection protocol", "Read in the same turn", "`status: DELETED`", "Quote, never compute",
                       "`tierFloorPrice`", "floorRoe = highWaterRoe × lockRoe / 100", "A short's stop sits above the price",
                       "Leverage is the exchange's", "read → say → yes → act → read back", "Consent comes from this conversation",
                       "nor consent to add one", "a health check never changes protection", "REPLACES a static SL",
                       "references/protection-cases.md"):
            self.assertIn(needle, body, needle)

    def test_the_sizing_readout_and_its_thresholds_are_in_the_body(self):
        body = _body(SKILL)
        # One needle per rule, never a sentence opener: the three numbers, the two thresholds, the fresh-yes
        # clause, the applied-leverage read-back, and the ladder-not-a-cron rule.
        for needle in ("share of the account", "dollars at the stop", "leverage that will actually apply",
                       "50% of the account in one position", "20% of the account at the stop",
                       "a yes to the setup is not a yes to the concentration",
                       "Read back the leverage", "is the ladder, never a cron"):
            self.assertIn(needle, body, needle)

    def test_the_pair_is_never_promised(self):
        body = _body(SKILL)
        self.assertNotIn("Offer both; don't call the pair", body)
        self.assertNotIn("pair with (b) for a cap", body)

    def test_the_four_cases_exist_with_must_and_must_not(self):
        text = CASES.read_text(encoding="utf-8")
        for n in range(1, 5):
            self.assertIn(f"## Case {n}", text)
        self.assertGreaterEqual(text.count("**Must"), 8)
        self.assertIn("$2,095", text) and self.assertIn("$1,557", text)

    def test_readme_row_matches_the_skill_version(self):
        version = re.search(r'version: "([0-9.]+)"', SKILL.read_text(encoding="utf-8")).group(1)
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"| [`senpi-trade`](senpi-trade/) | {version} |", readme)


if __name__ == "__main__":
    unittest.main()
