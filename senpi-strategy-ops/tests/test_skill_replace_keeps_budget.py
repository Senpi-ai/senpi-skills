import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = " ".join((OPS / "SKILL.md").read_text().split())
STEP_075 = SKILL[SKILL.find("**Step 0.75"):SKILL.find("**Step 1 ")]
RULE = STEP_075[STEP_075.find("**A replacement**"):]
RULE = RULE[:RULE.find("Create nothing before the yes")]


class ReplacementKeepsTheReplacedBudget(unittest.TestCase):
    def test_the_1000_default_is_for_a_new_simulation_only(self):
        self.assertIn("No budget named: $1,000 for a new simulation.", STEP_075)

    def test_each_way_of_asking_for_a_replacement_is_named(self):
        for ask in ('"replace X with a version that…"', '"improve X and replace the running one"',
                    "closing X and starting its package or variation again"):
            self.assertIn(ask, RULE)

    def test_the_budget_is_read_from_the_replaced_simulation(self):
        self.assertIn("starts at X's `initialBudget` from `strategy_list {simulation: true}` unless the user names one",
                      RULE)

    def test_the_budget_goes_on_every_quote_and_start_path(self):
        self.assertIn("in each variant's `budgetUsd`", RULE)
        self.assertIn("`--budget` on the `--dry-run` quote and the `--user-said-yes` create", RULE)

    def test_the_quote_before_the_yes_states_it(self):
        self.assertIn("the quote states that budget", RULE)


if __name__ == "__main__":
    unittest.main()
