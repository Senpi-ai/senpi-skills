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


class ForkOfNamesTheSourceStrategy(unittest.TestCase):
    def test_the_sections_are_found(self):
        self.assertTrue(TAKE_LIVE and RUNNING)

    def test_the_code_change_copy_passes_fork_of(self):
        self.assertTrue(re.search(r"`[^`]*deploy\.py fork [^`]*--fork-of [^`]*`", RUNNING),
                        "the code-change `deploy.py fork` command carries no --fork-of")

    def test_the_id_is_the_hand_off_source_strategy_id(self):
        self.assertTrue("Source strategy id" in RUNNING, "Running variations never names the hand-off line")

    def test_a_batch_of_variations_of_a_running_strategy_passes_fork_of(self):
        self.assertTrue("forkOf" in RUNNING, "the batch call never passes forkOf")

    def test_take_live_reads_the_same_hand_off_line(self):
        self.assertTrue("Source strategy id" in TAKE_LIVE, "take-live never names the hand-off line")
        self.assertTrue("--from-simulation" in TAKE_LIVE)


if __name__ == "__main__":
    unittest.main()
