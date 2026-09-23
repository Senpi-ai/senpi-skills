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


class DiagnoseFromTheRuntimesOwnNumbers(unittest.TestCase):
    """Scanner fields are read per scheduleMode, stops are said as ROE and as price, an edit's exposure is named,
    and the runtime's
    state is read before a top-up is advised. Every needle is absent from the pre-change skill, so each one
    fails if its rule is removed."""

    def test_the_runtime_first_rules_are_in_the_skill(self):
        text = SKILL.read_text()
        for needle in ("climbs **every tick**", "only on a tick that emits", "never a reason to close and recreate",
                       "Say every stop twice", "ROE ÷ leverage", "max_entries_per_day × marginPct",
                       "Read the runtime's own state first", "interval under 60 s"):
            self.assertIn(needle, text, needle)

    def test_an_external_scanners_empty_schedule_fields_are_not_a_dead_strategy(self):
        # The runtime arms a timer only for `interval` mode, so an `external` scanner reads
        # intervalSeconds 0 / nextRunAt null by construction. Read as "the runtime never wired it",
        # that tells a funded user their money is in a broken thing while it is scanning.
        text = SKILL.read_text()
        for needle in ('`scheduleMode`', "`intervalSeconds: 0` / `nextRunAt: null` are the correct values",
                       "`initialized` stays `false` until the first", "`lastAliveAt` is the tick",
                       "is the scaffold's", "never call a strategy dead without checking its ticks first"):
            self.assertIn(needle, text, needle)
        # The claim this PR started from was itself wrong: runCount counts ticks on `interval`
        # (complete, skip and error each increment it) and only emits on `external`. Teaching the
        # one-line version re-creates the misdiagnosis from the other side.
        self.assertNotIn("counts signals EMITTED, not ticks", text)
        liveness = (OPS / "references" / "liveness-verification.md").read_text()
        for needle in ("Read the schedule fields against `scheduleMode` first",
                       "is **not** an unwired scanner", "Never call a strategy dead without a tick check"):
            self.assertIn(needle, liveness, needle)


if __name__ == "__main__":
    unittest.main()
