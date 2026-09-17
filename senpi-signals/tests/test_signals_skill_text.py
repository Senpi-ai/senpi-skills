"""What the agent reads in senpi-signals/SKILL.md for 2.0: one reading, on demand, nothing to schedule or fund.

2.0 is a one-time job with no compare. The skill runs it when asked and never on a schedule (a cron is a
model call per firing), never deploys or funds anything for it, and never describes a detector that
needs an earlier reading as part of the feed."""
import json
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


def test_every_run_ends_with_one_question_to_trade_or_build():
    """The feed is observation; acting is a separate, consented step. One question closes every run,
    it routes to senpi-trade or to a strategy, and nothing is placed or funded without a yes."""
    skill = _flat(SKILL)
    assert "## How every run ends — one question" in skill
    assert ("Want to act on any of these? I can set up a trade on one of them — you see the size and the stop "
            "before anything is placed — or start a strategy: your own Athena, the smart-money hedge fund, "
            "or one built around these reads.") in skill
    assert "Place nothing until the user says yes to that exact order." in skill
    assert "The stop must sit before liquidation." in skill
    assert "It is a new strategy with no track record, and you say so." in skill
    assert "Deploy only on the user's yes." in skill
    assert "then the closing question (next section) — nothing after it" in skill
    assert "Follow-up: \"how could I play it?\"" not in skill


def _closing(skill):
    return skill[skill.index("## How every run ends — one question"):skill.index("## Where it lives")]


def test_the_strategy_offer_leads_with_athena_and_every_template_it_names_is_real():
    """Athena is the lead strategy offer, and each read type names the template built on that kind of
    read. A name that isn't in the catalog would send the user to a template ops can't deploy, so every
    id named here must exist in strategies/catalog.json."""
    closing = _closing(_flat(SKILL))
    named = re.findall(r"\*\*[A-Z][a-z]+\*\* \(`([a-z0-9-]+)`\)", closing)
    assert named[0] == "athena"
    assert set(named) == {"athena", "phalanx", "pangolin", "camel", "meerkat", "mantis"}
    catalog = json.loads((SKILL_DIR.parent / "strategies" / "catalog.json").read_text(encoding="utf-8"))
    ids = {s["id"] for s in catalog["skills"]}
    assert set(named) <= ids, set(named) - ids
    assert "**Athena → senpi-strategy-ops.**" in closing
    assert "read the minimum budget from the catalog, never from memory" in closing


def test_a_template_is_a_starting_point_never_a_promise():
    closing = _closing(_flat(SKILL))
    assert "Every template is a starting point the user makes their own" in closing
    assert "Never promise or imply results, and never call a template proven." in closing
    for claim in ("proven template", "proven strategy", "guaranteed", "best-performing", "profitable"):
        assert claim not in closing.lower(), claim


def test_the_sweep_stays_read_only_and_the_question_stays_private():
    skill = _flat(SKILL)
    assert "3. **The sweep is read-only.**" in skill
    assert "Acting on a read happens only in the closing step (below), on the user's explicit yes" in skill
    assert "**Never in public copy.**" in skill


def test_the_brief_is_documented_and_the_chip_wording_loads_the_skill():
    skill = _flat(SKILL)
    front = _frontmatter(SKILL)
    assert "python3 scripts/sweep.py --brief 3" in skill
    assert "## The short version (`--brief N`)" in skill
    for trigger in ("scan Senpi Signals", "scan for market anomalies"):
        assert trigger in front, trigger


def test_the_mispricing_vocabulary_routes_here_and_stays_an_observation():
    """"Find what's mispriced" is how a user asks for this feed in their own words, so it has to
    reach the skill at all — and then be pinned to the reads, because "mispriced" is a valuation
    word and this sweep carries no model of fair value. It compares two things it read; saying a
    thing is worth more or less than its price would be advice the feed cannot support."""
    front = _frontmatter(SKILL)
    for trigger in ("find what's mispriced", "where is the market wrong", "find dislocations",
                    "what's out of line"):
        assert trigger in front, trigger
    body = SKILL.read_text(encoding="utf-8")
    skill = _flat(SKILL)
    assert "never a fair-value judgment" in skill
    assert 'never "HYPE is overpriced"' in skill
    # ONCE, and in golden rule 2 — a rebase across a section deletion re-attached a copy of this
    # paragraph to the previous section while a second copy lived in the rule, and every assertion
    # above passed against both. "Present somewhere" is not the claim; "stated once, where the rule
    # that forbids the crossing lives" is.
    assert body.count("gap between two things the sweep actually read") == 1, "the fence is duplicated"
    rule2 = body[body.index("2. **Observation, not advice.**"):body.index("3. **")]
    assert "gap between two things the sweep actually read" in rule2, "the fence drifted out of rule 2"
    assert "Every user has it." in skill
