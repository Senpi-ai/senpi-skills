"""A copy started from a running strategy names that strategy: the id comes from the client's hand-off
line `Source strategy id: <id>` and goes to `--fork-of` on the copy and `--from-simulation` on a
take-live, and to `forkedFrom` on a variations batch, which never sends `forkOf` for it. The rules live
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

    def test_a_variations_batch_names_the_hand_off_strategy_as_forked_from(self):
        self.assertIn("`Source strategy id` other than that simulation adds "
                      "`\"forkedFrom\":\"<Source strategy id>\"`", HAND_OFF)

    def test_the_hand_off_copy_is_deploy_py_and_not_the_public_simulation_fork(self):
        self.assertTrue(re.search(r"Source strategy id: <id>.*--fork-of <id>.*deploy\.py fork.*"
                                  r"Never `senpi deploy --fork-of`", HAND_OFF),
                        "the hand-off rules do not tie the hand-off id to deploy.py's --fork-of")
        self.assertIn("`Source strategy id` hand-off is never that", HAND_OFF)
        public = SKILL[SKILL.find("**Public simulations and forks.**"):]
        self.assertIn("`senpi deploy --fork-of <id>", public[:public.find("**", 40)])

    def test_take_live_reads_the_same_hand_off_line(self):
        self.assertIn("`Source strategy id` is that `simulation-<id>`", HAND_OFF)


if __name__ == "__main__":
    unittest.main()
