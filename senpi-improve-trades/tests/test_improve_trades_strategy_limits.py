"""A multi-wallet strategy is ONE strategy to judge, but every wallet takes its own slot.

"A strategy is ALL its wallets" is a rule about verdicts. Read as a rule about plan limits it is wrong:
strategy-manager counts every wallet against the running and new-strategy limits, so the rule says so."""

import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"


class OneStrategyButASlotPerWallet(unittest.TestCase):
    def test_the_inherited_rule_names_the_slot_per_wallet(self):
        text = " ".join(SKILL.read_text().split())
        start = text.find("**A strategy is ALL its wallets.**")
        self.assertGreaterEqual(start, 0, "SKILL.md lost its 'A strategy is ALL its wallets' rule")
        rule = text[start: text.find(" - **", start)]
        self.assertIn("is ONE strategy", rule)
        self.assertIn("each wallet still takes its own strategy-limit slot", rule)


if __name__ == "__main__":
    unittest.main()
