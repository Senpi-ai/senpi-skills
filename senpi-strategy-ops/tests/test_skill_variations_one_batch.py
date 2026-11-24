"""Variations that change only settings start as one runtime batch after the yes, not one fork,
validate and deploy each: a user asking for 10 variations waited minutes per variation."""

import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = " ".join((OPS / "SKILL.md").read_text().split())
VARIATIONS = SKILL[SKILL.find("**Running variations**"):SKILL.find("**A variation refused or failed")]


class SettingsOnlyVariationsStartAsOneBatch(unittest.TestCase):
    def test_settings_only_variations_take_one_start_batch_call(self):
        self.assertIn("variations that change only settings start as one batch", VARIATIONS)
        self.assertIn('`"dryRun":false`', VARIATIONS)
        self.assertIn("no fork, no edit, no `create`", VARIATIONS)

    def test_each_variant_carries_its_label_and_settings(self):
        self.assertIn('{"label":"<name>","settings":{"<key>":<value>}}', VARIATIONS)

    def test_progress_is_read_from_batch_status(self):
        params = re.search(r"senpi\.simulations\.batchStatus --json --params '(\{.*?\})'", VARIATIONS).group(1)
        self.assertIn('"requestId":', params)

    def test_the_quote_dry_runs_the_same_variants(self):
        self.assertIn("as you will start them", VARIATIONS)

    def test_only_a_code_change_forks(self):
        self.assertIn("Only a variation that changes code", VARIATIONS)


if __name__ == "__main__":
    unittest.main()
