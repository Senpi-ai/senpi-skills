"""Every market-pulse run closes with the Senpi Signals brief and one question offering the full sweep.

The brief is another skill's script, so the prescribed command must exist and accept the flag this skill
tells the agent to pass; a closing step that names a missing script turns "here are the top reads" into
an error at the end of every pulse. The question is pinned because it is the handoff into senpi-signals.

Run: python3 -m pytest senpi-market-pulse/tests -q
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = re.sub(r"\s+", " ", (ROOT / "senpi-market-pulse" / "SKILL.md").read_text(encoding="utf-8"))
SWEEP = ROOT / "senpi-signals" / "scripts" / "sweep.py"


def test_the_brief_command_exists_and_takes_the_flag_we_pass():
    assert "From the **senpi-signals** skill folder (`cd ../senpi-signals` from this one), run `python3 scripts/sweep.py --brief 3`" in SKILL
    assert SWEEP.is_file(), "senpi-signals/scripts/sweep.py is prescribed by senpi-market-pulse but does not exist"
    assert '"--brief"' in SWEEP.read_text(encoding="utf-8"), "sweep.py no longer accepts --brief"


def test_every_run_ends_with_the_brief_and_one_question():
    assert "## Mandatory closing: Senpi Signals in brief, then one question" in SKILL
    assert "Every market-pulse run — a full read or a narrow ask — ends the same way" in SKILL
    assert ("**Want the full Senpi Signals sweep? I can also check how your positions sit in this market, "
            "or build a strategy for it.**") in SKILL
    assert "Run `python3 scripts/sweep.py --print-feed` from the senpi-signals folder" in SKILL


def test_the_4h_leader_layer_is_never_called_smart_money():
    """The overlay is leaderboard_get_markets — who is WINNING in the last 4h, survivorship included.
    senpi-signals' smart money is the >= $1M lifetime-realized cohort, and the two are regularly on
    opposite sides of the same name in the same answer (live, Sep 17: the overlay had ZEC and HYPE long,
    the signals brief had the cohort short both). Whatever the JSON key is called, the words the reader
    sees must say which population it is."""
    assert "**4h leaders**" in SKILL
    assert "Never call it smart money" in SKILL
    assert ">= $1M lifetime-realized cohort" in SKILL
    assert "the >$1M cohort is X% concentrated short HYPE" not in SKILL     # the old, wrong example


def test_a_missing_move_is_not_printed_as_flat():
    assert "A missing change is `—`, never `0.00%`" in SKILL


def test_the_question_does_not_offer_to_mirror_a_4h_winner():
    q = SKILL[SKILL.index("**Want the full Senpi Signals sweep?"):]
    assert "traders to mirror" not in q[:400]


def test_the_strategy_route_offers_athena_first():
    route = SKILL[SKILL.index("**Strategy → Athena first, or one built for this market.**"):]
    assert route.index("**Athena**") < route.index("**senpi-strategy-author**")
    assert "**senpi-strategy-ops** runs its walkthrough and deploys it under their name" in route
    assert "Never promise or imply results." in route


def test_a_box_without_senpi_signals_skips_the_step_silently():
    assert ("If the senpi-signals folder isn't there, skip this step and the signals clause of the question, "
            "and say nothing about it.") in SKILL


def test_the_old_verbatim_cta_list_is_gone():
    for gone in ("## Mandatory closing (verbatim)", "The two CTAs", "Want me to check how our strategies"):
        assert gone not in SKILL, gone
