"""Every script this skill tells the agent to run must actually exist.

senpi-portfolio named `diagnose.py` as the confirmation step for a degraded runtime across five
versions. No such script has ever existed in senpi-strategy-ops. So the agent sent to confirm a
scary verdict found nothing, and fell back to repeating the unconfirmed verdict to the user as fact
— on strategies that were visibly trading.

A prescribed remedy that does not exist is worse than no remedy: it converts "I am not sure" into
"I checked."
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SKILL = (_ROOT / "senpi-portfolio" / "SKILL.md").read_text(encoding="utf-8")
_ENGINE = (_ROOT / "senpi-portfolio" / "scripts" / "portfolio.py").read_text(encoding="utf-8")

# `<skill>/scripts/<name>.py` or a bare `<name>.py` next to a skill name.
_REFS = sorted(set(
    re.findall(r"`(senpi-[a-z-]+)`[^`\n]{0,40}`(?:scripts/)?([a-z_]+\.py)", _SKILL + _ENGINE)
    + re.findall(r"(senpi-[a-z-]+)/scripts/([a-z_]+\.py)", _SKILL + _ENGINE)
))


def test_the_scan_found_the_references():
    """A regex that matched nothing would make the parametrized test below vacuous."""
    assert _REFS, "no cross-skill script references found — did the phrasing change?"


@pytest.mark.parametrize("skill,script", _REFS, ids=lambda v: v)
def test_prescribed_script_exists(skill, script):
    path = _ROOT / skill / "scripts" / script
    assert path.is_file(), (
        f"{skill}/scripts/{script} is prescribed by senpi-portfolio but does not exist. "
        f"Available: {sorted(p.name for p in (_ROOT / skill / 'scripts').glob('*.py'))}"
    )
