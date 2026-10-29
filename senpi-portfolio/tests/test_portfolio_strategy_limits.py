"""A multi-wallet strategy is ONE strategy to reason about, but every wallet takes its own slot.

"A strategy is ALL its wallets" is a rule about analysis. Read as a rule about plan limits it is wrong:
strategy-manager counts every wallet against the running and new-strategy limits, so the rule names both
facts side by side."""

import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"


class OneStrategyButASlotPerWallet(unittest.TestCase):
    def test_the_rule_names_the_slot_per_wallet(self):
        text = " ".join(SKILL.read_text().split())
        start = text.find("## A strategy is ALL its wallets")
        self.assertGreaterEqual(start, 0, "SKILL.md lost its 'A strategy is ALL its wallets' section")
        section = text[start: text.find(" ## ", start + 1)]
        self.assertIn("is **ONE strategy across N wallets/instances**", section)
        self.assertIn("against the plan's strategy limits each wallet still takes its own slot", section)


if __name__ == "__main__":
    unittest.main()
