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


if __name__ == "__main__":
    unittest.main()
