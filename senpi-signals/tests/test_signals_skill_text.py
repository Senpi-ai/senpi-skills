"""What the agent reads in senpi-signals/SKILL.md for 2.0: one reading, on demand, nothing to schedule or fund.

2.0 is a one-time job with no compare. The skill runs it when asked and never on a schedule (a cron is a
model call per firing), never deploys or funds anything for it, and never describes a detector that
needs an earlier reading as part of the feed."""
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"


def _flat(path):
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def _frontmatter(path):
    return re.sub(r"\s+", " ", re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S).group(1))


def test_the_run_is_one_on_demand_reading():
    skill = _flat(SKILL)
    assert "python3 scripts/sweep.py --print-feed" in skill
    assert "One reading, no compare." in skill
    assert "two runs a minute apart give the same feed" in skill


def test_a_request_for_a_schedule_gets_a_no():
    skill = _flat(SKILL)
    assert "On demand only: say no to a cron." in skill
    assert "any schedule" in skill and "say no" in skill
    assert "Never create an `openclaw cron` or any other scheduled agent turn for it" in skill
    assert "if asked to put it on a cron or any schedule, say no" in _frontmatter(SKILL)


def test_nothing_to_deploy_fund_or_keep():
    skill = _flat(SKILL)
    for gone in ("strategies/signals", "deploy.py create signals", "--budget 10", "--consumer social",
                 "--snapshot-only", "state.json", "state ring"):
        assert gone not in skill, gone
    assert "never deploy a strategy or fund a wallet to run it" in skill
    assert not (SKILL_DIR / "references" / "scheduling.md").exists()


def test_compare_detectors_are_v2_and_never_claimed():
    skill = _flat(SKILL)
    assert "## v2 — compare over periods (not in 2.0)" in skill
    assert "Now, never \"since\"." in skill
    assert "Never describe the feed as if it carried them." in skill
