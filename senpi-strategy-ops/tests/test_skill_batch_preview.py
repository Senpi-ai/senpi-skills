"""Several simulations start only after the user has seen every variation, the total budget and the
cost, and said yes. The agent once told users it could not quote a cost before a simulation existed;
the runtime's batch dry run answers it without starting anything."""

import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = " ".join((OPS / "SKILL.md").read_text().split())
VARIATIONS = SKILL[SKILL.find("**Running variations**"):SKILL.find("**A variation refused or failed")]
STEP_075 = SKILL[SKILL.find("**Step 0.75"):SKILL.find("**Step 1 ")]


class BatchIsShownWithItsCostBeforeAnyCreate(unittest.TestCase):
    def test_each_variation_is_listed_with_what_it_changes_and_its_budget(self):
        self.assertIn("every variation of the count, each with its name, what it changes", VARIATIONS)
        self.assertIn("and its budget; the total budget;", VARIATIONS)

    def test_the_cost_comes_from_the_runtime_dry_run(self):
        self.assertIn("senpi call senpi.simulations.startBatch --json --params", VARIATIONS)
        self.assertIn('"dryRun":true', VARIATIONS)
        self.assertIn("`creditsPerHourAfter`", VARIATIONS)
        self.assertIn("`would_refuse`", VARIATIONS)

    def test_the_dry_run_params_are_json(self):
        params = re.search(r"startBatch --json --params '(\{.*?\})'", VARIATIONS).group(1)
        self.assertIn('"requestId":', params)
        self.assertIn('"budgetUsd":', params)

    def test_nothing_is_created_before_the_yes(self):
        self.assertIn("create nothing until the user says yes", VARIATIONS)
        self.assertIn("show the new batch and its cost and ask again", VARIATIONS)

    def test_no_rule_forbids_quoting_the_cost_up_front(self):
        self.assertNotIn("quote a cost only as a simulation's record shows it", SKILL)
        self.assertIn("never an estimate of your own", VARIATIONS)

    def test_a_single_simulation_takes_one_short_confirm(self):
        self.assertIn("One simulation needs no list: Step 0.75's one-message confirm.", VARIATIONS)

    def test_one_simulation_skips_the_walkthrough_for_one_message_with_its_cost(self):
        rule = STEP_075[STEP_075.find("**One simulation skips it**"):STEP_075.find("keep what they have.") + 20]
        self.assertIn("no walkthrough, no levers, no name line", rule)
        self.assertIn("`creditsPerHourAfter`", rule)
        self.assertIn('"go?"', rule)
        self.assertIn("Create nothing before the yes", rule)
        self.assertIn("A live deploy, a simulation going live and several simulations keep what they have.", rule)

    def test_a_first_batch_starts_its_parent_from_the_list(self):
        self.assertIn("the first variation of the list is the parent", VARIATIONS)
        self.assertIn("Never start a base the list did not show; 5 asked = 5 simulations.", VARIATIONS)
