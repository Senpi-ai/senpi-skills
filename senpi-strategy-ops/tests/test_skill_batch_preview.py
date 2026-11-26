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
        self.assertIn("and its budget ($1,000 when none is named, or for a replacement the replaced one's); the total budget;", VARIATIONS)

    def test_the_cost_comes_from_the_runtime_dry_run(self):
        self.assertIn("deploy.py call senpi.simulations.startBatch --params", VARIATIONS)
        self.assertIn('"dryRun":true', VARIATIONS)
        self.assertIn("`creditsPerHourAfter`", VARIATIONS)
        self.assertIn("`would_refuse`", VARIATIONS)

    def test_the_dry_run_params_are_json(self):
        params = re.search(r"startBatch --params '(\{.*?\})'", VARIATIONS).group(1)
        self.assertIn('"requestId":', params)
        self.assertIn('"budgetUsd":', params)

    def test_a_budget_per_variation_goes_in_its_variant_never_the_total(self):
        params = re.search(r"startBatch --params '(\{.*?\})'", VARIATIONS).group(1)
        self.assertIn('{"label":"<name>","budgetUsd":<its budget>,"settings":{…}}', params)
        self.assertIn("never their total as the batch's `budgetUsd`", VARIATIONS)
        self.assertIn("each row's `budgetUsd`", VARIATIONS)
        start = VARIATIONS[VARIATIONS.find("After the yes, start them all"):VARIATIONS.find("The box validates and starts them")]
        self.assertIn('{"label":"<name>","budgetUsd":<its budget>,"settings":{"<key>":<value>}}', start)

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

    def test_a_refused_dry_run_still_shows_the_batch_and_waits(self):
        self.assertIn("nothing starts before the user says yes to the list, on every start path", VARIATIONS)
        self.assertLess(VARIATIONS.find("on every start path"), VARIATIONS.find("takes the fork path"))
        refused = VARIATIONS[VARIATIONS.find("When the dry run is refused or unreadable"):]
        self.assertIn("still show every variation, its budget and the total", refused)
        self.assertIn("never a reason to start another way", refused)

    def test_the_description_holds_the_rule_for_a_refused_dry_run(self):
        description = SKILL[SKILL.find("description:"):SKILL.find("license:")]
        self.assertIn("no start (batch, `create`, fork) before a yes to the shown list, total and cost", description)
        self.assertIn("even when the dry run is refused", description)

    def test_the_description_holds_the_rule_for_one_simulation_of_several_wallets(self):
        description = " ".join(SKILL[SKILL.find("description:"):SKILL.find("license:")].split())
        self.assertIn("ONE SIMULATION OR SEVERAL", description)
        self.assertIn("one simulation: its cost and \"Go?\"", description)
        self.assertIn("`create --simulation` starts nothing without `--user-said-yes`: pass it only after their yes.", description)


class NoBudgetNamedStartsAt1000(unittest.TestCase):
    def test_one_simulation_with_no_budget_named_confirms_1000(self):
        self.assertIn("No budget named: $1,000 for a new simulation.", STEP_075)
        self.assertNotIn("No budget named: ask for it", SKILL)

    def test_one_simulation_whose_dry_run_is_refused_still_waits_for_the_yes(self):
        self.assertIn("and waits for the yes; then `create <id> --simulation --user-said-yes`.", STEP_075)
        self.assertIn("A refused dry run gets the same.", STEP_075)

    def test_a_package_of_several_wallets_is_known_before_any_dry_run_and_skips_it(self):
        rule = STEP_075[STEP_075.find("A package of several wallets"):STEP_075.find("A refused dry run gets the same.")]
        self.assertIn("`wallet_count` above 1", rule)
        self.assertIn("`deploy.py validate <id>`", rule)
        self.assertIn("takes no dry run", rule)
        self.assertIn('"billed by the hour" with no number, and waits for the yes', rule)


if __name__ == "__main__":
    unittest.main()
