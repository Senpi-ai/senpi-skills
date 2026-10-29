"""A recommendation of several strategies is sized to the user's strategy limits, not just their balance.

Layer 3 sized a multi-pick recommendation ("~$Y in Rhino, ~$Z in Spider") from free funds alone. Every
wallet is one strategy against the plan's running and new-strategy limits, so a set the user cannot
start in full was offered and then failed halfway through deploying. Before proposing more than one, the
skill reads `strategy_get_limits` and fits the set to the headroom it reports."""

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"


def _sizing():
    text = SKILL.read_text()
    start = text.find("3. **Size & lock in (Layer 3)**")
    assert start >= 0, "SKILL.md lost its Layer 3 sizing step"
    end = text.find("**\"Can I try it first", start)
    assert end >= 0, "SKILL.md lost the paper-trade bullet that closes the sizing rules"
    return " ".join(text[start:end].split())


class SeveralPicksOnlyWithinTheLimits(unittest.TestCase):
    def test_the_rule_sits_in_the_sizing_step(self):
        section = _sizing()
        for needle in (
            "**Several picks at once only within the user's strategy limits.**",
            "Every wallet is one strategy (a multi-instance card needs one per instance)",
            "Before proposing more than one to deploy, call `strategy_get_limits`",
            "when `enforced` is true and the picks need more wallets than `runningLeft` or `startsLeft`",
            "quote `summary`",
            "size the set to fit",
            "never limit numbers from memory",
        ):
            self.assertIn(needle, section, needle)

    def test_no_limit_figure_is_written_down(self):
        self.assertIsNone(re.search(r"\b\d+ (running|new) strateg", _sizing()))


if __name__ == "__main__":
    unittest.main()
