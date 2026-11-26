"""A copy started from a running strategy names that strategy: the id comes from the client's hand-off
line `Source strategy id: <id>`, and goes to `--fork-of` on the copy, `forkOf` on a batch, and
`--from-simulation` on a take-live."""

import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = " ".join((OPS / "SKILL.md").read_text().split())
TAKE_LIVE = SKILL[SKILL.find("**Taking a simulation live**"):SKILL.find("**Running variations** (")]
RUNNING = SKILL[SKILL.find("**Running variations** ("):SKILL.find("**Simulations: running, ranked by return.**")]
PUBLIC = SKILL[SKILL.find("**Public simulations and forks.**"):SKILL.find("**Public simulations and forks.**") + 1200]
FORK = SKILL[SKILL.find("**The fork is the verb's job:**"):SKILL.find("A user ID is never a name;")]


class ForkOfNamesTheSourceStrategy(unittest.TestCase):
    def test_the_code_change_copy_passes_fork_of(self):
        self.assertTrue(re.search(r"`[^`]*deploy\.py fork [^`]*--fork-of [^`]*`", RUNNING),
                        "the code-change `deploy.py fork` command carries no --fork-of")

    def test_the_id_is_the_hand_off_source_strategy_id(self):
        self.assertTrue("Source strategy id" in RUNNING, "Running variations never names the hand-off line")

    def test_a_batch_of_variations_of_a_running_strategy_passes_fork_of(self):
        self.assertTrue("forkOf" in RUNNING, "the batch call never passes forkOf")

    def test_the_hand_off_copy_is_deploy_py_and_not_the_public_simulation_fork(self):
        self.assertTrue(re.search(r"Source strategy id: <id>.*--fork-of <id>.*deploy\.py fork.*"
                                  r"never `senpi deploy --fork-of`", FORK),
                        "the Fork paragraph does not tie the hand-off id to deploy.py's --fork-of")
        public = PUBLIC[:PUBLIC.find("**", 40)]
        self.assertIn("`senpi deploy --fork-of <id>", public)
        self.assertIn("`Source strategy id` hand-off is never this", public)

    def test_take_live_reads_the_same_hand_off_line(self):
        self.assertIn("`Source strategy id` is that `simulation-<id>`", TAKE_LIVE)
        self.assertTrue("--from-simulation" in TAKE_LIVE)


if __name__ == "__main__":
    unittest.main()
