"""The honest answer to "which templates are proven?" is resident in SKILL.md. A user who asked that
and was answered with a mirror-strategy ranking read it as "you only promote mirror trading" and left.
The line has to be in the skill the agent loads, not in a reference it may skip.

    python3 -m pytest senpi-strategy-discover/tests/test_skill_surface_discover.py -q
"""
import os
import re

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")


def test_proven_templates_answer_is_resident():
    text = open(SKILL, encoding="utf-8").read()
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    body = text[m.end():] if m else text
    for needle in ("has no backtest", "mirror-only"):
        assert needle in body, needle
