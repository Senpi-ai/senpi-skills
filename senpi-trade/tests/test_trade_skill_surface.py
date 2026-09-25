#!/usr/bin/env python3
"""The protection protocol is resident in senpi-trade/SKILL.md, its five regression cases exist, and the
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
                       "Read back the leverage", "is the ladder, never a cron",
                       "The add binds to the position open now"):
            self.assertIn(needle, body, needle)

    def test_a_close_reason_is_read_from_the_record(self):
        # The agent closed a position by hand, then told the user its take-profit had hit. Both halves
        # of the rule have to stay: read the record, and your own close is not a TP fill.
        body = _body(SKILL)
        for needle in ('**"Why did it close?" is a record question.**', "execution_get_closed_position_details",
                       "did not hit its take-profit"):
            self.assertIn(needle, body, needle)

    def test_the_pair_is_never_promised(self):
        body = _body(SKILL)
        self.assertNotIn("Offer both; don't call the pair", body)
        self.assertNotIn("pair with (b) for a cap", body)

    def test_the_five_cases_exist_with_must_and_must_not(self):
        text = CASES.read_text(encoding="utf-8")
        for n in range(1, 6):
            self.assertIn(f"## Case {n}", text)
        self.assertGreaterEqual(text.count("**Must"), 10)
        self.assertIn("$2,095", text) and self.assertIn("$1,557", text)

    def test_a_sent_stop_price_is_checked_against_the_live_mark(self):
        """2026-09-24/25: two users had a stop refused for sitting on the wrong side of the mark
        (56.695 >= 56.180 on a LONG; 0.20691 >= 0.15933, +30%). On edit_position the size change
        commits first, so each refusal left the position open and UNPROTECTED — and both agents
        re-sent the identical price rather than recomputing the side."""
        body = _body(SKILL)
        self.assertIn("correct side of the live mark", body)
        self.assertIn("BELOW", body)
        self.assertIn("ABOVE", body)
        # the consequence, not just the rule — this is why it is worth the lines
        self.assertIn("UNPROTECTED", body)
        # the mark must come from the venue at call time, not from entry or an earlier quote
        self.assertIn("strategy_get_clearinghouse_state", body)
        # re-sending the same price is the one move that cannot work
        self.assertIn("Re-sending the same price", body)

    def test_readme_row_matches_the_skill_version(self):
        version = re.search(r'version: "([0-9.]+)"', SKILL.read_text(encoding="utf-8")).group(1)
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"| [`senpi-trade`](senpi-trade/) | {version} |", readme)


if __name__ == "__main__":
    unittest.main()
