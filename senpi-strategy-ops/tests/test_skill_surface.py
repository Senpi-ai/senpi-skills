#!/usr/bin/env python3
"""Guards on the agent-facing skill surfaces themselves.

Run:  python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
OPS = REPO / "senpi-strategy-ops" / "SKILL.md"
AUTHOR = REPO / "senpi-strategy-author" / "SKILL.md"
TAXONOMY = REPO / "docs" / "error-code-taxonomy.md"

# Budgets, not aspirations: the convergence took ops from 278 to 626 lines by restating rendered
# refusal text in prose. This is the post-reduction ceiling from
# docs/specs/2026-08-12-skills-context-reduction-design.md §2 (~260 lines), with headroom.
#
# One key per skill, and a key lands here only when that skill is under its ceiling — a dict entry
# whose subTest is red on the day it is written is a guard nobody can distinguish from a regression.
#
# `senpi-strategy-author` is set at its post-cut line count with NO slack, and its number is much
# higher than ops' on purpose: that skill is a conversation script, not a relay surface. Its bulk is
# the 7-decision interview, the staged build and the handoff gate — rules about what to ASK and what
# to CLAIM, which no rendered message can own, so there is nothing to move them to. Only seven of its
# 51 blocks were bucket-1/rationale (docs/specs/2026-08-12-classification-table.md:176-232) and all
# seven are gone. Cutting further means deleting a conversation rule, which this budget exists to
# make visible, not to force.
BODY_BUDGET = {"senpi-strategy-ops": 300, "senpi-strategy-author": 368}


def _skill_body(path):
    """SKILL.md with its YAML frontmatter stripped — the part loaded on invoke."""
    text = path.read_text()
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


def _ops_teaching_corpus():
    """Everything the ops skill teaches about codes: the resident body PLUS its references.

    The references are in the corpus because the reduction MOVES per-code teaching there. Scanning
    `SKILL.md` alone made this guard shrink every time a task relocated a block — Task 4 took the
    four `W_BUDGET_*` codes out of its view and it kept passing, which is a guard that stops
    guarding without ever going red. Coverage has to follow the teaching, not the file.
    """
    parts = [_skill_body(OPS)]
    parts += [p.read_text() for p in sorted((REPO / "senpi-strategy-ops" / "references").glob("*.md"))]
    return "\n".join(parts)


class TaxonomyCoversWhatTheSkillsTeach(unittest.TestCase):
    """The taxonomy header claims to cover every refusal an agent can hit, and did not carry the
    one code the deploy verb reaches for most: `[INVALID_REQUEST]` renders the no-DSL-exit refusal
    (runtime src/deploy/orchestrator.ts:1974), the scanner-`enabled` refusal (:2067) and the
    uppercase-package-id refusal, and senpi-strategy-ops/SKILL.md lists it as a refusal code."""

    def test_every_code_the_ops_skill_names_has_a_taxonomy_row(self):
        # Skills install as bare dirs on a box; the repo-root docs/ tree is not installed. Skip
        # rather than error, so "run the suite on the box" stays a usable release gate.
        if not TAXONOMY.is_file():
            self.skipTest(f"taxonomy not present at {TAXONOMY} (installed skill layout)")
        taxonomy = TAXONOMY.read_text()
        # The pattern deliberately excludes the glob shorthands the refused-table row uses
        # (`[E_FUNDS_*]`, `[E_VALIDATE_*]`): `*` is outside the class, so they never match.
        named = set(re.findall(r"\[([A-Z][A-Z0-9_]*)\]", _ops_teaching_corpus()))
        missing = sorted(c for c in named if f"`{c}`" not in taxonomy)
        self.assertEqual(missing, [], f"codes taught with no taxonomy row: {missing}")


class TaxonomyGateOnABox(unittest.TestCase):
    def test_taxonomy_test_skips_when_the_repo_docs_tree_is_absent(self):
        """Skills install as bare dirs; the repo-root docs/ tree is not installed. A release gate
        that raises FileNotFoundError instead of skipping is not a usable gate."""
        case = TaxonomyCoversWhatTheSkillsTeach("test_every_code_the_ops_skill_names_has_a_taxonomy_row")
        with mock.patch.object(
            sys.modules[TaxonomyCoversWhatTheSkillsTeach.__module__], "TAXONOMY", Path("/nonexistent/taxonomy.md")
        ):
            result = case.run()
        self.assertEqual(len(result.errors), 0, f"raised instead of skipping: {result.errors}")
        self.assertEqual(len(result.skipped), 1, "expected exactly one skip")


RELAY_HEADING = "### Refusals and warns — the relay contract"


_FENCE = re.compile(r"\s*(```+|~~~+)")


def _section(body, heading):
    """The lines under `heading`, up to the next heading at the same or shallower depth.

    FENCE-AWARE, and that is load-bearing for the guards below rather than cosmetic: a `#` opening a
    line inside a fenced block is a bash/yaml/jsonc comment, not a heading. Terminating on one would
    silently hand a guard a TRUNCATED slice — it would still pass, just over less text than it
    claims to cover, which is a guard that has stopped guarding without ever going red.
    """
    lines = body.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == heading)
    depth = len(heading) - len(heading.lstrip("#"))
    fence = None
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        opener = _FENCE.match(ln)
        if opener:
            # Normalised so ``` never closes ~~~ and vice versa.
            token = opener.group(1)[0] * 3
            fence = token if fence is None else (None if token == fence else fence)
            continue
        if fence is None and ln.startswith("#") and (len(ln) - len(ln.lstrip("#"))) <= depth:
            return "\n".join(lines[start:j])
    return "\n".join(lines[start:])


class SectionSliceIsFenceAware(unittest.TestCase):
    """Guards the guard's own reader. Tasks 5-7 extend the relay-contract section, and the first
    fenced bash/yaml block with a `#` comment in it would otherwise shorten every slice taken after
    that point — weakening `RelayContractNamesNoComputedCommand` invisibly."""

    def test_a_comment_inside_a_fence_does_not_end_the_section(self):
        body = "\n".join([
            "### H", "before", "```bash", "# not a heading", "close.py 'x'", "```", "after",
            "### Next", "outside",
        ])
        section = _section(body, "### H")
        self.assertIn("close.py 'x'", section, "the fenced body was truncated away")
        self.assertIn("after", section, "the section ended at a comment inside a fence")
        self.assertNotIn("outside", section, "the section ran past the next heading")


class RelayContractNamesNoComputedCommand(unittest.TestCase):
    """`buildBudgetEscape` (runtime orchestrator.ts:810) decides AT RUNTIME whether to emit a scoped
    `close.py --instance`, a read-only `status.py` pointer, or nothing at all — the stranded-wallet
    and zero-share branches deliberately emit no teardown. A static copy in the skill contradicts
    whichever branch actually fired, and the failure mode is an agent closing a funded wallet.

    The slice is the contract and its money rules, and stops at `### Report from the structured
    output` — which used to sit inside it only because it had no heading of its own. That block is
    about quoting the report's numbers, never about what to run at a refusal, so it was never what
    this guard was written to cover."""

    def test_relay_section_hardcodes_no_teardown_command(self):
        section = _section(_skill_body(OPS), RELAY_HEADING)
        for forbidden in ("close.py", "strategy_close"):
            self.assertNotIn(forbidden, section,
                             f"the relay contract names {forbidden!r}; the runtime computes it")


class CodesAreNamedNotExplained(unittest.TestCase):
    """A code may be NAMED in the skill (routing: which branch am I on) but not EXPLAINED (the
    runtime renders the explanation, computed against terminal state). Two mentions is the ceiling:
    the refused-table row, plus at most one routing line. Each occurrence past that is a second
    copy of a message that decides its own content at runtime — and the copy is what contradicts
    whichever branch actually fired.

    The brackets are OPTIONAL in the pattern on purpose. Counting only `[CODE]` left a trivial way
    past the cap — write the third mention as a backticked bare `E_FUNDS_SHORT` and the guard never
    sees it, while a reader sees the same second copy. The `(?!\\*)` keeps the glob shorthands the
    outcome table uses (`[E_FUNDS_*]`, `[E_VALIDATE_*]`, `[W_BUDGET_*]`) out of the tally: they name
    a family, not a code, and without it the prefix before the `*` counts as a code of its own.

    Both resident bodies are in the corpus. Scanning ops alone was the same silent weakening
    `_ops_teaching_corpus` documents, one file over: `senpi-strategy-author` reached the deploy
    refusals too (it embedded `[E_FUNDS_SHORT]`/`[E_FUNDS_BELOW_FLOOR]` prose in its handoff loop, and
    that copy said "confirm a lower amount" for the one code where **no** budget is valid), and the
    guard could not see it. Author is at 1 mention per code after Task 7, so it joins green."""

    def test_no_code_is_mentioned_more_than_twice(self):
        for path in (OPS, AUTHOR):
            with self.subTest(skill=path.parent.name):
                counts = {}
                for code in re.findall(r"\[?\b([EW]_[A-Z0-9_]+)\b(?!\*)\]?", _skill_body(path)):
                    counts[code] = counts.get(code, 0) + 1
                over = {c: n for c, n in counts.items() if n > 2}
                self.assertEqual(over, {}, f"codes explained rather than named: {over}")


class SkillBodyWithinBudget(unittest.TestCase):
    """The skill body is loaded on every invoke; references are pay-per-read. Depth belongs in
    references/, and a budget is the only thing that keeps that true under editing pressure — every
    task in this workstream had a reason to add "just three more lines" resident."""

    def test_bodies_are_within_budget(self):
        for skill, budget in BODY_BUDGET.items():
            with self.subTest(skill=skill):
                n = len(_skill_body(REPO / skill / "SKILL.md").splitlines())
                self.assertLessEqual(n, budget, f"{skill}/SKILL.md body is {n} lines (budget {budget})")


class ReferencePointersResolve(unittest.TestCase):
    """A bucket-4 move is only safe if the pointer lands. A dead relative link silently turns
    'depth is one read away' into 'depth is gone'."""

    def test_every_relative_md_link_exists(self):
        for skill in ("senpi-strategy-ops", "senpi-strategy-author"):
            path = REPO / skill / "SKILL.md"
            for target in re.findall(r"\]\((?!https?:)([^)#]+\.md)\)", _skill_body(path)):
                with self.subTest(skill=skill, target=target):
                    self.assertTrue((path.parent / target).resolve().is_file(),
                                    f"{skill}/SKILL.md links to missing {target}")


if __name__ == "__main__":
    unittest.main()
