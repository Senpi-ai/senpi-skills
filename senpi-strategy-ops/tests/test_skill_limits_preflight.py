"""Strategy limits are read BEFORE a deploy, not learned from a refusal halfway through one.

A package with N instances is N creates, and strategy-manager checks each one on its own: a user with 1
running slot left gets sleeve 1 created and funded and sleeve 2 refused (SERR183). The skill reads MCP
`strategy_get_limits` once the package is on disk, counts the wallets the deploy will create, and tells
the user before anything is created when the package needs more than `runningLeft` or `startsLeft`.
The runtime refuses the same package itself (`[STRATEGY_LIMIT_PREFLIGHT]`, a `refused` report, exit 2)
when the skill did not catch it, and the playbook says what to do with that refusal. The running-status
list matches strategy-manager: every status except CLOSED, FAILED, CLOSING_POSITIONS and MANUAL_REVIEW
counts, PAUSED included."""

import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = OPS / "SKILL.md"
PLAYBOOK = OPS / "references" / "refusal-playbook.md"
TAXONOMY = OPS.parent / "docs" / "error-code-taxonomy.md"


def _flat(text):
    """Markdown rewraps freely; a needle must not fail on where a line happens to break."""
    return " ".join(text.split())


def _between(text, start, end_marker):
    i = text.find(start)
    assert i >= 0, f"lost {start!r}"
    j = text.find(end_marker, i + len(start))
    return _flat(text[i: j if j >= 0 else len(text)])


class LimitsAreReadBeforeTheDeploy(unittest.TestCase):
    def setUp(self):
        self.step = _between(SKILL.read_text(), "**Step 0.6 — strategy limits", "\n**Step 0.75")

    def test_the_step_sits_after_the_package_is_on_disk_and_before_the_walkthrough(self):
        text = SKILL.read_text()
        self.assertLess(text.find("**Step 0.5 — preflight"), text.find("**Step 0.6 — strategy limits"))
        self.assertLess(text.find("**Step 0.6 — strategy limits"), text.find("**Step 0.75 — the walkthrough"))

    def test_it_reads_the_tool_and_counts_the_new_wallets(self):
        for needle in (
            "Call MCP `strategy_get_limits`",
            "N = one wallet per `strategy.yaml` instance",
            "a resume counts only the instances with no wallet yet",
        ):
            self.assertIn(needle, self.step, needle)

    def test_a_short_package_is_told_before_anything_is_created(self):
        for needle in (
            "When `enforced` is true and N is above `runningLeft` or `startsLeft`",
            "a `null` left is no limit",
            "tell the user before creating anything",
            "quote `summary`",
            "say the package needs N",
            "give the ways forward of [`[STRATEGY_LIMIT_PREFLIGHT]`](references/refusal-playbook.md)",
            "the verb's own exit-2 refusal of it",
            "Never fewer instances to fit",
        ):
            self.assertIn(needle, self.step, needle)

    def test_a_failed_read_goes_on(self):
        for needle in (
            "A failed read (`SERR185`): retry once at most",
            "say the limits couldn't be read and go on",
            "never block the deploy on it",
        ):
            self.assertIn(needle, self.step, needle)

    def test_numbers_come_from_the_tool_never_from_memory(self):
        self.assertIn("never limit numbers from memory", self.step)
        # Plan limits change; no plan's figure may sit in the step.
        self.assertIsNone(re.search(r"\b\d+ (running|new) strateg", self.step))


class TheRuntimeRefusalIsAnExit2Refusal(unittest.TestCase):
    def test_the_refused_row_lists_the_code(self):
        row = next(l for l in SKILL.read_text().splitlines() if l.startswith("| `2` | `refused` |"))
        self.assertIn("`[STRATEGY_LIMIT_PREFLIGHT]`", row)

    def test_the_playbook_section(self):
        text = PLAYBOOK.read_text()
        start = text.find("### `[STRATEGY_LIMIT_PREFLIGHT]`")
        self.assertGreaterEqual(start, 0, "the playbook has no STRATEGY_LIMIT_PREFLIGHT section")
        # Next to the backend limit codes it pre-empts.
        self.assertLess(text.find("### `SERR184`"), start)
        self.assertLess(start, text.find("## Budget warnings"))
        section = _between(text, "### `[STRATEGY_LIMIT_PREFLIGHT]`", "\n## ")
        for needle in (
            "**`refused` report (exit 2)**",
            "before the first create",
            "**Nothing was created** and no money moved",
            "every wallet is one strategy",
            "Quote its lines",
            "close a running strategy themselves",
            "wait until the reset time it names, or upgrade",
            "closing never gives a start back",
            "only after the user has done one and asks",
            "**Never**",
            "on a timer",
            "close a strategy on the user's behalf",
            "deploy fewer instances",
            "strategy_create_custom_strategy",
        ):
            self.assertIn(needle, section, needle)

    def test_the_running_status_list_matches_strategy_manager(self):
        section = _between(PLAYBOOK.read_text(), "### `SERR183`", "\n### ")
        self.assertIn(
            "the limit counts every strategy except CLOSED, FAILED, CLOSING_POSITIONS and MANUAL_REVIEW ones, "
            "so a PAUSED one counts",
            section,
        )
        self.assertNotIn("not closed, closing or failed", section)


class TheTaxonomyCarriesTheNewCodes(unittest.TestCase):
    def setUp(self):
        if not TAXONOMY.is_file():
            self.skipTest("taxonomy not present (installed skill layout)")
        self.lines = TAXONOMY.read_text().splitlines()

    def _row(self, prefix):
        row = next((l for l in self.lines if l.startswith(prefix)), None)
        self.assertIsNotNone(row, prefix)
        return row

    def test_serr185_is_a_read_failure_never_a_create_failure(self):
        row = self._row("| `SERR185` (backend) |")
        for needle in (
            "`strategy_get_limits`",
            "not a create failure",
            "Retry the read once at most",
            "continue without them",
            "never block or refuse a deploy on it",
        ):
            self.assertIn(needle, row, needle)

    def test_the_runtime_code_has_its_row(self):
        row = self._row("| `STRATEGY_LIMIT_PREFLIGHT` |")
        for needle in (
            "`senpi-trading-runtime` (`senpi deploy`, preflight)",
            "`refused`",
            "before the first create",
            "`runningLeft` or `startsLeft`",
            "Nothing was created",
            "never split the deploy into smaller creates",
        ):
            self.assertIn(needle, row, needle)

    def test_the_rows_sit_after_serr184(self):
        index = {l.split("|")[1].strip(): i for i, l in enumerate(self.lines) if l.startswith("| `")}
        self.assertLess(index["`SERR184` (backend)"], index["`SERR185` (backend)"])
        self.assertLess(index["`SERR185` (backend)"], index["`STRATEGY_LIMIT_PREFLIGHT`"])


if __name__ == "__main__":
    unittest.main()
