"""Discover lists other users' public simulations, and its Fork button opens a chat with "Fork simulation
<id> for me". Simulations are private until their owner asks, a fork runs only after the forker's yes to
its quote, and a simulation that cannot be forked says why. The box reads only the first 8,000 characters
of a SKILL.md on its first read, so these rules must sit inside them."""

import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
RAW = (OPS / "SKILL.md").read_text()
FIRST_READ = 8_000
HEAD = " ".join(RAW[: RAW.find("\n---\n", 4)].split())
START = "**Public simulations and forks.**"
RULE_AT = RAW.find(START)
RULE_RAW = RAW[RULE_AT:RAW.find("\n", RULE_AT)]
RULE = " ".join(RULE_RAW.split())


class TheRulesAreInTheFirstRead(unittest.TestCase):
    def test_the_rule_ends_inside_the_first_8000_characters(self):
        self.assertGreaterEqual(RULE_AT, 0, "the public-simulations rule is gone")
        self.assertLessEqual(RULE_AT + len(RULE_RAW), FIRST_READ)

    def test_the_description_routes_both_asks_here(self):
        self.assertIn('"make my simulation public"; "Fork simulation <id>".', HEAD)


class Publish(unittest.TestCase):
    def test_simulations_are_private_until_the_owner_asks(self):
        self.assertIn("Private until the owner asks.", RULE)

    def test_publish_and_unpublish_call_the_tool_with_ids_or_the_group(self):
        self.assertIn('"Make <sim / group> public" or "private" → `simulation_set_public` (`public` true/false; '
                      "`ids`, or `groupId` = `identity.group.id` from `strategy_list {simulation: true}`)", RULE)

    def test_publishing_is_free_so_it_takes_no_quote(self):
        self.assertIn("free, no quote.", RULE)

    def test_the_answer_says_what_is_shown_and_what_never_is(self):
        self.assertIn("Say others see its name, settings, results and chart under an anonymous label, never its "
                      "wallet, positions or fills.", RULE)


class Fork(unittest.TestCase):
    def test_the_source_is_read_first_and_an_unforkable_one_stops(self):
        self.assertIn('"Fork simulation <id>": `simulation_public_get {id}`; `forkable` false → quote '
                      "`forkBlockedReason` and stop.", RULE)

    def test_the_fork_is_quoted_and_the_request_is_never_the_yes(self):
        self.assertIn("`senpi deploy --fork-of <id> --dry-run`", RULE)
        self.assertIn("show the quote, end the turn: the request is never the yes.", RULE)

    def test_only_the_yes_starts_it_and_its_status_is_followed(self):
        self.assertIn("After their yes, the same with `--user-said-yes` for `--dry-run`, then "
                      "`senpi deploy fork-status <requestId>`.", RULE)
        self.assertLess(RULE.find("--dry-run"), RULE.find("--user-said-yes"))

    def test_a_change_asked_for_on_a_fork_is_said_not_applied(self):
        self.assertIn('"… and change X" is not applied: say so.', RULE)


class VersionBumped(unittest.TestCase):
    def test_skill_and_readme_versions_agree_and_are_above_dev(self):
        version = re.search(r'^  version: "([0-9.]+)"', RAW, re.M).group(1)
        readme = (OPS.parent / "README.md").read_text()
        self.assertIn(f"| [`senpi-strategy-ops`](senpi-strategy-ops/) | {version} |", readme)
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (3, 37, 1))


if __name__ == "__main__":
    unittest.main()
