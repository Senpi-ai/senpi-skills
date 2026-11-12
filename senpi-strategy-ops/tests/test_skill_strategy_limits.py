"""A backend `SERR183` (the user's plan is at its limit of running strategies) or `SERR184` (the user has
used every new strategy their plan allows this billing cycle, or in the last 30 days without a plan) is
the user's plan, not a defect the agent can fix. The outcome table's generic "read the quoted cause, fix
it, re-run" would turn either one into a retry loop, or into the agent closing a strategy or deploying a
smaller package to squeeze under the limit.
The skill names both as exceptions: nothing was created, tell the user the limit and the ways forward
(close a strategy themselves for SERR183, wait until the stated time for SERR184, or upgrade), and never
retry, never close a strategy for them, never split the request into smaller creates."""

import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = OPS / "SKILL.md"
PLAYBOOK = OPS / "references" / "refusal-playbook.md"
TAXONOMY = OPS.parent / "docs" / "error-code-taxonomy.md"


def _playbook_section(code):
    text = PLAYBOOK.read_text()
    backend = text.find("## Backend rejections the box cannot fix")
    assert backend >= 0, "the playbook lost its backend-rejections section"
    start = text.find(f"### `{code}`")
    assert start >= 0, f"the playbook has no {code} section"
    assert start > backend, f"{code} must sit under 'Backend rejections the box cannot fix'"
    rest = text[start:]
    ends = [i for i in (rest.find("\n## "), rest.find("\n### ", 1)) if i >= 0]
    section = rest[: min(ends)] if ends else rest
    # Markdown rewraps freely; a needle must not fail on where a line happens to break.
    return " ".join(section.split())


def _failed_row():
    row = next((l for l in SKILL.read_text().splitlines() if l.startswith("| `3` |")), None)
    assert row is not None, "the outcome table lost its exit-3 row"
    return row


class StrategyLimitIsThePlanNotARetry(unittest.TestCase):
    def test_the_failed_row_names_both_limit_codes_as_exceptions(self):
        row = _failed_row()
        for needle in (
            "`SERR183`",
            "`SERR184`",
            "the user's plan limit",
            "new strategies per billing cycle (the last 30 days without a plan)",
            "nothing was created",
            "never retry",
            "never close a strategy for them",
            "never split the deploy into smaller creates",
        ):
            self.assertIn(needle, row, needle)
        # SERR124 keeps its own exception beside the new one.
        self.assertIn("`SERR124`", row)

    def test_both_sections_forbid_the_three_workarounds(self):
        for code in ("SERR183", "SERR184"):
            section = _playbook_section(code)
            for needle in (
                "**Nothing was created**",
                "Quote the backend's sentence",
                "upgrade",
                "**Never**",
                "re-run the same `deploy.py create`",
                "on a timer",
                "close a strategy on the user's behalf",
                "smaller creates",
                "strategy_create_custom_strategy",
                "status.py <id>",
            ):
                self.assertIn(needle, section, f"{code}: {needle}")

    def test_serr183_offers_closing_one_as_the_users_choice(self):
        section = _playbook_section("SERR183")
        for needle in (
            "running strategies",
            "the user picks which one",
            "`close.py <id>`",
            "only after the user has closed one or upgraded",
        ):
            self.assertIn(needle, section, needle)

    def test_serr184_says_wait_and_that_closing_frees_nothing(self):
        section = _playbook_section("SERR184")
        for needle in (
            # Both strategy-manager texts: per billing cycle for a plan or trial, the last 30
            # days without one.
            "new strategies per billing cycle. Your count resets at",
            "new strategies per 30 days. Your next one frees up at",
            "on a plan or trial",
            "without one",
            "the time the backend names",
            "Closing a strategy does not free a start",
            "only after that time has passed or the user has upgraded",
        ):
            self.assertIn(needle, section, needle)
        # A closed strategy keeps counting in either window, so the section must not promise
        # it frees up after its own 30 days on a plan with a cycle.
        self.assertIn("a closed strategy still counts until the count resets or its 30 days pass", section)
        # Closing is the SERR183 way forward, not this one.
        self.assertNotIn("`close.py <id>`", section)

    def test_a_zero_limit_leaves_upgrade_as_the_only_way_forward(self):
        # strategy-manager words a limit of 0 as "allows 0 running strategies and you have 0. Close one
        # or upgrade" and, for starts, "allows 0 new strategies per billing cycle. Upgrade your plan to
        # start one." (or "per 30 days") with no time. Offering a close or a wait there points at
        # something that does not exist.
        self.assertIn("When the limit is 0, upgrading is the only way forward", _playbook_section("SERR183"))
        section = _playbook_section("SERR184")
        self.assertIn("When the sentence names no time (a limit of 0), upgrading is the only way forward",
                      section)

    def test_a_lowered_limit_means_closing_enough_to_get_under_it(self):
        self.assertIn("they need to close enough to get under it", _playbook_section("SERR183"))

    def test_the_taxonomy_carries_both_rows(self):
        if not TAXONOMY.is_file():
            self.skipTest("taxonomy not present (installed skill layout)")
        text = TAXONOMY.read_text()
        self.assertIn("| `SERR183` (backend) |", text)
        row = next(l for l in text.splitlines() if l.startswith("| `SERR184` (backend) |"))
        for needle in ("current billing cycle", "the last 30 days without a plan", "when the count resets",
                       "when the next start frees up", "closing a strategy does not free a start"):
            self.assertIn(needle, row, needle)


if __name__ == "__main__":
    unittest.main()
