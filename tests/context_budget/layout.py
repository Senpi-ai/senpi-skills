"""Shared helpers for the per-skill SKILL.md layout tests (senpi-portfolio, senpi-improve-trades).

openclaw_measure.py holds the ports of OpenClaw's own code; this file holds what the layout tests do with
them: compare text the way the model reads it, and assert that rules sit inside the 5.7 follow-up replay.
"""
import re

import openclaw_measure as om


def norm(text):
    """Whitespace runs and blockquote markers at line starts do not change what the model reads."""
    return " ".join(re.sub(r"(?m)^[ \t]*>[ \t]?", "", text).split())


def assert_replayed(text, needles):
    """Fail naming every needle that 5.7 no longer keeps of ``text`` on a follow-up turn."""
    replayed = norm(om.replay_57(text))
    lost = [n for n in needles if norm(n) not in replayed]
    mode, kept, _ = om.replay_57_layout(text)
    assert not lost, (
        f"5.7 keeps only the first {kept:,} chars of SKILL.md on a follow-up ({mode}); these rules are past "
        f"it: {lost}. Make room above them: move long-tail text that sits above them further down SKILL.md "
        f"(it stays in core) or into references/ if it holds no rule. Do not drop a rule to make room."
    )
