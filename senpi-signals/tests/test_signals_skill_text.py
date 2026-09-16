"""What the agent reads in senpi-signals/SKILL.md: a user gets the on-demand run and never the $10 host.

The host package (strategies/signals) keeps a feed on the runtime's clock for Senpi's content automation.
On a user's account it would park $10 plus a wallet fee to produce what the on-demand run already gives
them, so the skill must never deploy or offer it there, and must not claim its ring warms other boxes."""
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
SCHEDULING = SKILL_DIR / "references" / "scheduling.md"


def _flat(path):
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def test_the_ad_hoc_run_prints_only_the_feed():
    assert "python3 scripts/sweep.py --print-feed" in _flat(SKILL)


def test_the_host_is_never_deployed_or_offered_on_a_users_account():
    skill = _flat(SKILL)
    assert "never deployed or offered on a user's account" in skill
    assert "one team box, never a user's account" in skill
    assert "give them the on-demand run" in skill
    assert "the answer is the package" not in skill
    assert "never a user's account" in _flat(SCHEDULING)


def test_the_ring_is_per_box():
    skill = _flat(SKILL)
    assert "warm for everyone" not in skill
    assert "does not warm anyone else's ad-hoc runs" in skill
