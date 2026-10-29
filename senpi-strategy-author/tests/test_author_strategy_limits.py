"""Running strategies side by side is normal, but only as many as the user's strategy limits allow.

The skill used to call "multiple strategies / wallets at once" normal AND encouraged, with no word about
limits, so the agent proposed a 3-wallet "fund" to a user with one running slot left; strategy-manager
then created wallet 1 and refused wallet 2. Every wallet is one strategy against the plan's limits, so a
proposal of more than one is sized to the headroom `strategy_get_limits` reports, never to memory."""

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"
HEADING = "## Wallets & concurrency"


def _section():
    text = SKILL.read_text()
    start = text.find(HEADING)
    assert start >= 0, "SKILL.md lost its wallets & concurrency section"
    end = text.find("\n## ", start + len(HEADING))
    return " ".join(text[start: end if end >= 0 else len(text)].split())


class ManyAtOnceOnlyWithinTheLimits(unittest.TestCase):
    def test_encouraged_is_gone(self):
        self.assertNotIn("normal and encouraged", _section())

    def test_more_than_one_is_sized_to_the_headroom(self):
        section = _section()
        for needle in (
            "**Multiple strategies / wallets at once is normal — within the user's strategy limits.**",
            "every wallet is one strategy against the plan's limits",
            "before proposing more than one, call `strategy_get_limits`",
            "when `enforced` is true, size the proposal to `runningLeft` and `startsLeft`",
            "a `null` left is no limit",
            "**two instances / two wallets**, two slots",
        ):
            self.assertIn(needle, section, needle)

    def test_only_the_strategy_limits_block(self):
        section = _section()
        for needle in (
            "**Never tell the user they must stop an existing strategy to start a new one**",
            "only the strategy limits can (running and new per cycle)",
            "Closing one frees only a running slot, and is the user's choice",
        ):
            self.assertIn(needle, section, needle)
        self.assertNotIn("only a plan's running limit can", section)

    def test_no_limit_figure_is_written_down(self):
        self.assertIsNone(re.search(r"\b\d+ (running|new) strateg", _section()))


if __name__ == "__main__":
    unittest.main()
