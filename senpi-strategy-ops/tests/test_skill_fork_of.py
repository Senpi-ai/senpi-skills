"""A copy started from a running strategy names that strategy: the id comes from the client's hand-off
line `Source strategy id: <id>` and goes to `--fork-of` on the copy and `--from-simulation` on a
take-live, and to `deploy.py plan --forked-from` (or `forkedFrom` on a raw `startBatch` call) for a variations
batch, which never sends `forkOf` for it. The rules live
in references/walkthrough.md; SKILL.md points there."""

import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = " ".join((OPS / "SKILL.md").read_text().split())
WALK = " ".join((OPS / "references" / "walkthrough.md").read_text().split())
HAND_OFF = WALK[WALK.find("## A `Source strategy id` hand-off"):]
FORK = SKILL[SKILL.find("**The fork is the verb's job:**"):SKILL.find("A user ID is never a name;")]


class ForkOfNamesTheSourceStrategy(unittest.TestCase):
    def test_skill_md_points_to_the_hand_off_rules(self):
        self.assertIn("Any `Source strategy id` hand-off: walkthrough.md.", FORK)
        self.assertGreater(WALK.find("## A `Source strategy id` hand-off"), 0)

    def test_the_code_change_copy_passes_fork_of(self):
        self.assertTrue(re.search(r"`[^`]*deploy\.py fork [^`]*--fork-of [^`]*`", HAND_OFF),
                        "the code-change `deploy.py fork` command carries no --fork-of")

    def test_the_copy_takes_the_hand_off_id_and_a_batch_never_sends_fork_of(self):
        self.assertIn("`<source id>`: the hand-off's `Source strategy id`", HAND_OFF)
        self.assertIn("never sends `forkOf`", HAND_OFF)
        self.assertNotIn('"forkOf"', HAND_OFF)

    def test_a_variations_batch_names_the_hand_off_strategy_through_plan(self):
        self.assertIn("`deploy.py plan <catalog id> --forked-from <Source strategy id>`", HAND_OFF)
        self.assertIn("`start-plan` sends that id as the batch's `forkedFrom`", HAND_OFF)

    def test_a_raw_start_batch_call_adds_forked_from_unless_it_varies_the_source(self):
        self.assertIn("A raw `startBatch` call (budgets that differ on a catalog id, or a `parentId` batch) adds "
                      "`\"forkedFrom\":\"<Source strategy id>\"` itself", HAND_OFF)
        self.assertIn("by `parentId` unless the `Source strategy id` is the simulation the batch varies", HAND_OFF)

    def test_the_hand_off_copy_is_deploy_py_and_not_the_public_simulation_fork(self):
        self.assertTrue(re.search(r"Source strategy id: <id>.*--fork-of <id>.*deploy\.py fork.*"
                                  r"Never `senpi deploy --fork-of`", HAND_OFF),
                        "the hand-off rules do not tie the hand-off id to deploy.py's --fork-of")
        self.assertIn("`Source strategy id` hand-off is never that", HAND_OFF)
        public = SKILL[SKILL.find("**Public simulations and forks.**"):]
        self.assertIn("`senpi deploy --fork-of <id>", public[:public.find("**", 40)])

    def test_take_live_reads_the_same_hand_off_line(self):
        self.assertIn("`Source strategy id` is that `simulation-<id>`", HAND_OFF)

    def test_skill_md_never_drops_the_hand_off_id_and_a_name_refusal_means_ask(self):
        self.assertIn("Never drop its `--fork-of`", FORK)
        self.assertIn("a refusal asking for a name means ask the user for one", FORK)
        self.assertNotIn("One simulation too", FORK)

    def test_one_simulation_skips_the_walkthrough_but_not_the_hand_off(self):
        one = SKILL[SKILL.find("**One simulation skips it**"):SKILL.find("Quote it with `deploy.py plan <id>`")]
        self.assertIn("unless a `Source strategy id` hand-off came: its walkthrough.md section applies, name "
                      "included", one)

    def test_a_local_package_plan_takes_the_hand_off_id(self):
        local = SKILL[SKILL.find("**A local package** (no catalog id)"):]
        local = local[:local.find("never call `senpi.deploy.startSimulation` yourself")]
        self.assertIn("`--forked-from <id>` for a `Source strategy id`", local)

    def test_the_hand_off_rules_cover_one_simulation_and_the_name_refusal(self):
        self.assertIn("**One simulation** takes the hand-off's id too", HAND_OFF)
        self.assertIn("`deploy.py create <dir> --simulation --user-said-yes --budget <their budget> --fork-of <id> "
                      "--name \"<their words>\"`", HAND_OFF)
        self.assertIn("never re-run it without `--fork-of`", HAND_OFF)

    def test_settings_only_variations_of_a_local_package_carry_the_hand_off_id(self):
        local = HAND_OFF[HAND_OFF.find("**Settings-only variations of a local package**"):]
        self.assertIn("**Settings-only variations of a local package**", HAND_OFF)
        self.assertIn("`deploy.py plan <dir> --budget <their budget> --design", local)
        self.assertIn("--forked-from <Source strategy id>`", local)
        self.assertIn("`deploy.py fork <dir> --name <label> --fork-of <Source strategy id>`", local)
        self.assertIn("`start-plan` and `startBatch` take no directory", local)
        self.assertIn("`deploy.py create <dir> --simulation --user-said-yes --budget <their budget> --fork-of "
                      "<Source strategy id> --name \"<their words>\"`", local)
        self.assertIn("`create <fork dir> --simulation --user-said-yes --budget <its budget> --variant-of", local)
        self.assertIn("never a raw `startBatch` by catalog id", local)
        self.assertIn("these are refused without that `--fork-of`: `fork <dir> --name <label>` for one of its "
                      "variation labels, and `create <dir> --simulation` (several wallets too)", local)
        self.assertIn("A fork under another name and `create <fork dir> --variant-of` are not checked", local)
        self.assertNotIn("A `create <dir> --simulation` without `--fork-of` is refused once", local)

    def test_a_name_held_by_a_copy_with_another_parent_is_never_deployed_by_directory(self):
        held = HAND_OFF[HAND_OFF.find("**A name an earlier copy holds.**"):]
        held = held[:held.find("- **One simulation**")]
        self.assertIn("records another parent is refused, and the refusal names that parent", held)
        self.assertIn("never deploy that folder by directory", held)
        self.assertIn("Only a folder that records `<id>` itself may be deployed by directory", held)

    def test_a_raw_start_batch_for_budgets_that_differ_is_for_a_catalog_id_only(self):
        self.assertIn("budgets that differ on a catalog id", HAND_OFF)

    def test_a_raw_start_batch_by_catalog_id_carries_forked_from(self):
        self.assertIn("itself: always by catalog id (`\"strategy\":\"<catalog id>\"`)", HAND_OFF)


if __name__ == "__main__":
    unittest.main()
