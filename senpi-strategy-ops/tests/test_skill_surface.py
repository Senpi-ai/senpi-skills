#!/usr/bin/env python3
"""Guards on the agent-facing skill surfaces themselves.

Run:  python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OPS = REPO / "senpi-strategy-ops" / "SKILL.md"
AUTHOR = REPO / "senpi-strategy-author" / "SKILL.md"
TAXONOMY = REPO / "docs" / "error-code-taxonomy.md"


def _skill_body(path):
    """SKILL.md with its YAML frontmatter stripped — the part loaded on invoke."""
    text = path.read_text()
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


class TaxonomyCoversWhatTheSkillsTeach(unittest.TestCase):
    """The taxonomy header claims to cover every refusal an agent can hit, and did not carry the
    one code the deploy verb reaches for most: `[INVALID_REQUEST]` renders the no-DSL-exit refusal
    (runtime src/deploy/orchestrator.ts:1974), the scanner-`enabled` refusal (:2067) and the
    uppercase-package-id refusal, and senpi-strategy-ops/SKILL.md lists it as a refusal code."""

    def test_every_code_the_ops_skill_names_has_a_taxonomy_row(self):
        taxonomy = TAXONOMY.read_text()
        # The pattern deliberately excludes the glob shorthands the refused-table row uses
        # (`[E_FUNDS_*]`, `[E_VALIDATE_*]`): `*` is outside the class, so they never match.
        named = set(re.findall(r"\[([A-Z][A-Z0-9_]*)\]", _skill_body(OPS)))
        missing = sorted(c for c in named if f"`{c}`" not in taxonomy)
        self.assertEqual(missing, [], f"codes taught with no taxonomy row: {missing}")


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
    whichever branch actually fired, and the failure mode is an agent closing a funded wallet."""

    def test_relay_section_hardcodes_no_teardown_command(self):
        section = _section(_skill_body(OPS), RELAY_HEADING)
        for forbidden in ("close.py", "strategy_close"):
            self.assertNotIn(forbidden, section,
                             f"the relay contract names {forbidden!r}; the runtime computes it")


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
