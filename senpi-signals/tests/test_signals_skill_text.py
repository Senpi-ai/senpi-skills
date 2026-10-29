"""What the agent reads in senpi-signals/SKILL.md: Senpi Signals from the backend, on demand, nothing to
schedule or fund.

The backend computes Senpi Signals; the skill fetches and presents it, with no local fallback. The skill
runs it when asked and never on a schedule (a cron is a model call per firing), never deploys or funds
anything for it, and never claims a change read the feed does not carry."""
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


def test_change_reads_are_never_claimed_beyond_the_read():
    skill = _flat(SKILL)
    assert "Now, never \"since\"." in skill
    assert "never add a change, a baseline or a time it did not give" in skill
    assert "Never describe the feed as if it carried more than it does." in skill


def test_an_outage_is_never_a_quiet_market_and_the_agent_never_rebuilds_the_feed():
    """One implementation: the feed the app and the extension show. A rebuild from the agent's own
    tool calls would show the user numbers the app does not."""
    skill = _flat(SKILL)
    assert "Never build Senpi Signals yourself." in skill
    assert "Never present it as a quiet market, never rebuild the feed from your own tool calls" in skill
    assert "Requires the agent's Senpi sign-in." in _frontmatter(SKILL)
    for gone in ("scripts/score.py", "smartmoney.py", "read budget", "Requires a Senpi MCP token"):
        assert gone not in skill, gone


def test_the_skill_text_is_instructions_not_implementation():
    """SKILL.md is what the agent needs to act. How the backend works lives in the backend's docs."""
    skill = _flat(SKILL)
    for gone in ("/v1/signals", "insight=none", "agents.senpi.ai", "SENPI_AUTH_TOKEN", "signals-service",
                 "net_bias", "share_kind", "contribution_pct_change_15m", "striker_scoring.py",
                 "credibility multiplier", "Jason's call"):
        assert gone not in skill, gone
    assert len(skill.split()) < 2600, len(skill.split())


def test_every_run_ends_with_one_question_to_trade_or_build():
    """The feed is observation; acting is a separate, consented step. One question closes every run,
    it routes to senpi-trade or to building the user's own strategy, and nothing is placed or funded
    without a yes."""
    skill = _flat(SKILL)
    assert "## How every run ends — one question" in skill
    for fragment in ("Want to act on any of these?",
                     "I can set up a **single trade** on one read",
                     "**build a strategy on these signals**"):
        assert fragment in skill, fragment
    assert "Place nothing until the user says yes to that exact order." in skill
    assert "The stop must sit before liquidation." in skill
    assert "It is a new strategy with no track record, and you say so." in skill
    assert "Deploy only on the user's yes." in skill
    assert "then the closing question (below) — nothing after it" in skill
    assert "Follow-up: \"how could I play it?\"" not in skill


def _closing(skill):
    return skill[skill.index("## How every run ends — one question"):skill.index("## Where it lives")]


def test_the_offer_builds_the_users_own_and_names_no_template():
    """Building is the default; a pre-built template is opt-in. The closing names no template: a named
    menu at the end of every run is a pitch, and the user owns what they build."""
    closing = _closing(_flat(SKILL))
    assert "**A strategy → senpi-strategy-author.**" in closing
    assert "**They ask for something pre-built → senpi-strategy-discover**" in closing
    assert "never improvise a shortlist from memory" in closing
    assert "Name no template in it." in closing
    ids = {s["id"] for s in json.loads(
        (SKILL_DIR.parent / "strategies" / "catalog.json").read_text(encoding="utf-8"))["skills"]}
    named = {n for n in re.findall(r"`([a-z0-9-]+)`", closing) if n in ids}
    assert not named, named
    words = set(re.findall(r"[a-z0-9-]+", closing.lower()))
    assert not ({"penguin", "pelican", "puffin", "athena", "signals-hunter"} & words)


def test_the_closing_question_is_treated_as_a_major_entry_point():
    """For most users this question is where a read first becomes a position. An agent that treats it
    as a footer — abbreviating it, asking it only sometimes — is where the funnel leaks, so the skill
    says so in as many words."""
    closing = _closing(_flat(SKILL))
    assert "**This is a major entry point.**" in closing
    assert "Ask it every time, in full." in closing


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


def test_a_whale_open_is_dated_but_never_called_an_add_or_a_flip():
    """Rule 5 bans "since". A whale OPEN dates itself, so the rule separates it, or the agent keeps
    suppressing a read the feed carries."""
    skill = _flat(SKILL)
    assert "A whale OPEN is the exception, because it dates itself" in skill
    assert "Say *opened*, never *added* or *flipped*" in skill
