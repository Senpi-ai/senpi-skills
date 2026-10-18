"""The honest answer to "which templates are proven?" is resident in SKILL.md — in the skill the agent
loads, not in a reference it may skip.

    python3 -m pytest senpi-strategy-discover/tests/test_skill_surface_discover.py -q
"""
import os
import re

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")


def _body():
    text = open(SKILL, encoding="utf-8").read()
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


def _golden_rules():
    """Just the Golden rules section — the part an agent is most likely to actually apply.
    Whitespace-collapsed, so a needle never fails merely because the prose wrapped."""
    b = _body()
    i = b.index("## Golden rules")
    j = b.index("\n## ", i + 1)
    return " ".join(b[i:j].split())


def test_proven_templates_answer_is_resident():
    body = " ".join(_body().split())
    for needle in ("has no live track record", "every live copy of it is a user's own fork",
                   "a replay over its stated window, not a result",
                   "No tool reports how other users' forks did"):
        assert needle in body, needle
    # 16 catalog cards cite a replay, so "has no backtest" was false; and nothing reports other users'
    # forks, so "give the spread across live forks" asked for a number the agent can only invent.
    for banned in ("has no backtest", "spread across live forks"):
        assert banned not in body.lower(), f"re-introduced: {banned!r}"


def test_the_mirror_ranking_rule_is_a_GOLDEN_rule_not_a_buried_edge_case():
    """Measured 2026-10-18: over three days, 38 conversations asked a best-strategy question and were
    answered with a mirror ranking — and senpi-strategy-discover had RUN in 14 of them. The rule
    existed the whole time, 350+ lines deep in a Special-paths list. Placement was the defect, so
    placement is what this pins: it has to sit where the agent reads it, not where we can point at it
    afterwards."""
    g = _golden_rules()
    assert "discovery_get_top_strategies" in g, "the mirror-only ranker must be named in Golden rules"
    for needle in ("COPY-TRADING wallets only", "bait-and-switch"):
        assert needle in g, f"{needle!r} must be in Golden rules, not buried further down"


def test_no_per_template_pnl_is_explained_by_forking_in_golden_rules():
    """The reason is the product argument: a template has no track record BECAUSE every deployment is
    the user's own fork. Stating the conclusion without the cause is what let agents reach for a mirror
    leaderboard to fill the gap."""
    g = _golden_rules()
    for needle in ("no per-template PnL", "their fork"):
        assert needle in g, f"{needle!r} missing from Golden rules"
    assert "Never put a single live-performance number beside a template" in g


def test_copy_trading_is_not_sold_as_the_one_thing_with_a_track_record():
    """Removed 2026-10-18. That sentence licensed exactly the answer we do not want: it invited the
    agent to present mirroring as the evidence-backed option next to an 'unproven' template."""
    b = " ".join(_body().split()).lower()
    for banned in ("the one thing on senpi with a track record",
                   "copy-trading is the one thing"):
        assert banned not in b, f"re-introduced: {banned!r}"


def test_skill_says_the_fee_math_on_micro_specs_and_links_the_public_source():
    text = open(SKILL, encoding="utf-8").read()
    for needle in ("do the fee math out loud",
                   "the only fee that tool carries",
                   "Never save an impossible spec",
                   "https://github.com/Senpi-ai/senpi-skills/tree/main/strategies/",
                   "Never hand-roll a tarball"):
        assert needle in text, needle


def test_skill_forbids_recommending_from_the_unthemed_neutral_order_shortlist():
    """discover.py returns 8 of the eligible set by default; without --theme those 8 are alphabetical,
    so the skill must route soft preferences into --theme (or ask) instead of picking from them."""
    text = " ".join(open(SKILL, encoding="utf-8").read().split())
    for needle in ("Never recommend from a neutral-order shortlist",
                   "`meta.families_count > meta.returned_n`",
                   "Re-run with `--theme` built from what the user has told you",
                   "say how many fit (`eligible_count`) and ask one short question",
                   "Soft preferences go in `--theme`, never in a filter",
                   "`--limit N` is the browse path only"):
        assert needle in text, needle
    assert "Keep risk, belief, horizon, and worldview in your head" not in text
