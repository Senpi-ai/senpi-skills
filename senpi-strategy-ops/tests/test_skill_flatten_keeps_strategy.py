""""Close all trades" is a request about the positions, not the strategy. With close.py as the only
close-shaped verb, an agent tore a watch-only strategy down (new wallet, scanner state lost) when the
user asked only to flatten it. The skill routes by what is being closed, and the flatten path must
name the coins: an empty or missing `coins` list is the close-all, which offboards the wallet."""

import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = (OPS / "SKILL.md").read_text()
LIFECYCLE = (OPS / "references" / "lifecycle.md").read_text()


def _section():
    start = LIFECYCLE.find("## Flatten — close the trades, keep the strategy")
    assert start >= 0, "lifecycle.md lost its flatten section"
    rest = LIFECYCLE[start + 3:]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


class FlattenKeepsTheStrategy(unittest.TestCase):
    def test_description_routes_close_my_trades_away_from_close_py(self):
        head = SKILL[: SKILL.find("\n---\n", 4)]
        self.assertIn('"close my trades / positions"', head)
        self.assertIn("strategy_close_positions", head)

    def test_the_verb_is_chosen_by_what_is_being_closed(self):
        self.assertIn("**What is being closed decides the verb.**", SKILL)
        self.assertIn("never `coins: []`, never omit `coins`", SKILL)

    def test_flatten_gates_first_names_coins_and_forbids_the_close_all(self):
        s = _section()
        self.assertLess(s.find("decision_mode: none"), s.find("strategy_close_positions"))
        self.assertIn("Never `coins: []` and never omit `coins`", s)
        self.assertIn("xyz", s)
        self.assertIn("`ACTIVE`", s)

    def test_selection_keeps_the_requested_scope(self):
        s = _section()
        self.assertIn("by coin and by side", s)
        self.assertIn("Every open coin\n   only on an explicit full flatten", s)
        self.assertIn("never widen the\n   list, never fall back to a close-all", s)
        self.assertIn("naming only the coins they asked for", SKILL)


if __name__ == "__main__":
    unittest.main()
