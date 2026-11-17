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
    return " ".join((rest if end < 0 else rest[:end]).split())


class FlattenKeepsTheStrategy(unittest.TestCase):
    def test_description_routes_close_my_trades_away_from_close_py(self):
        head = SKILL[: SKILL.find("\n---\n", 4)]
        self.assertIn('"close my trades / positions" (→ strategy_close_positions, coins named; not close.py)',
                      " ".join(head.split()))

    def test_the_verb_is_chosen_by_what_is_being_closed(self):
        self.assertIn("**What is being closed decides the verb.**", SKILL)
        self.assertIn("never `coins: []`, never omit `coins`", " ".join(SKILL.split()))

    def test_flatten_gates_first_names_coins_and_forbids_the_close_all(self):
        s = _section()
        gate, close = s.find("decision_mode: none"), s.find("strategy_close_positions")
        self.assertGreaterEqual(gate, 0, "the gate step is gone")
        self.assertGreaterEqual(close, 0, "the close step is gone")
        self.assertLess(gate, close)
        self.assertIn("Never `coins: []` and never omit `coins`", s)
        self.assertIn("xyz", s)
        self.assertIn("`ACTIVE`", s)

    def test_selection_keeps_the_requested_scope(self):
        s = _section()
        self.assertIn("by coin and by side", s)
        self.assertIn("Every open coin only on an explicit full flatten", s)
        self.assertIn("never widen the list, never fall back to a close-all", s)
        self.assertIn("naming only the coins they asked for", SKILL)

    def test_async_close_and_flat_coin_rules(self):
        s = _section()
        self.assertIn("read after step 1 returns", s)
        self.assertIn("one already flat fails the WHOLE call (`SERR043`)", s)
        self.assertIn("never re-send a coin whose close you already submitted", s)

    def test_late_arrivals_keep_the_scope_and_polling_is_bounded(self):
        s = _section()
        self.assertIn("passes the same step-2 filter", s)
        self.assertIn("one that does not match stays open and is reported", s)
        self.assertIn("for at most ~60 s", s)
        self.assertIn("**pending, unverified**", s)

    def test_step_075_header_survives_the_57_replay(self):
        # Every line added above Step 0.75 pushes its header out of what an OpenClaw 5.7
        # follow-up turn replays of this file. The flatten rule must not be what evicts it.
        import sys
        sys.path.insert(0, str(OPS.parent / "tests" / "context_budget"))
        try:
            from openclaw_measure import replay_57_layout
        except ImportError as e:  # the measure needs PyYAML, as CI has
            self.skipTest(f"context-budget measure unavailable: {e}")
        _mode, head, _tail = replay_57_layout(SKILL)
        at = SKILL.find("**Step 0.75 — the walkthrough")
        self.assertGreaterEqual(at, 0)
        self.assertLessEqual(at + len("**Step 0.75 — the walkthrough"), head)


if __name__ == "__main__":
    unittest.main()
