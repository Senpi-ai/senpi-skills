"""A backend `SERR124` (this account's pool of approved strategy wallets is empty — pools are per
account, and the code is seen on some migrated older accounts) is not a cause the agent can fix. The
outcome table's generic "read the quoted cause, fix it, re-run" is exactly the sentence that turned it
into a 30-second retry loop, so the skill names it as the exception it is: tell the user, route it to
Senpi support, one later attempt after they confirm, never a loop, never a platform alert."""

import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = OPS / "SKILL.md"
PLAYBOOK = OPS / "references" / "refusal-playbook.md"
TAXONOMY = OPS.parent / "docs" / "error-code-taxonomy.md"


def _playbook_section():
    text = PLAYBOOK.read_text()
    start = text.find("### `SERR124`")
    assert start >= 0, "the playbook lost its SERR124 section"
    rest = text[start:]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


class Serr124IsThisAccountsPoolNotARetry(unittest.TestCase):
    def test_the_failed_row_names_the_empty_pool_as_an_exception_to_fix_and_rerun(self):
        row = next((l for l in SKILL.read_text().splitlines() if l.startswith("| `3` |")), None)
        assert row is not None, "the outcome table lost its exit-3 row"
        self.assertIn("`SERR124`", row)
        self.assertIn("nothing was created or debited", row)
        self.assertIn("route it to Senpi support", row)
        self.assertIn("never a retry loop", row)

    def test_the_playbook_says_per_account_and_forbids_the_loop(self):
        section = _playbook_section()
        for needle in (
            "Nothing was created or\ndebited",
            "per account",
            "not a platform outage",
            "Try ONCE more only after support confirms",
            "re-run every 30 seconds",
            "lower the budget",
            "strategy_create_custom_strategy",
            "close another strategy",
        ):
            self.assertIn(needle, section)

    def test_the_taxonomy_carries_the_row(self):
        if not TAXONOMY.is_file():
            self.skipTest("taxonomy not present (installed skill layout)")
        self.assertIn("| `SERR124` (backend) |", TAXONOMY.read_text())


if __name__ == "__main__":
    unittest.main()


def test_the_runtime_first_rules_are_in_the_skill():
    import os
    text = open(os.path.join(os.path.dirname(__file__), "..", "SKILL.md"), encoding="utf-8").read()
    for needle in ("`runCount` counts signals EMITTED, not ticks", "never a reason to close and recreate",
                   "Say every stop twice", "ROE ÷ leverage", "max_entries_per_day × marginPct",
                   "Read the runtime's own state first", "Max Entries/Day", "interval under 60 s"):
        assert needle in text, needle
