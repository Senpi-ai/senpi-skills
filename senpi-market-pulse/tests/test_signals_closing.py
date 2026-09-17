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


def test_every_run_ends_with_the_brief_and_three_numbered_next_steps():
    """A reader handed a market read plus a signals brief is deciding, not reading. Three numbered
    routes can be answered with a digit; one packed sentence has to be unpicked first. The three are
    pinned verbatim, in order, with the signals sweep first."""
    assert "## Mandatory closing: Senpi Signals in brief, then three numbered next steps" in SKILL
    assert "Every market-pulse run — a full read or a narrow ask — ends the same way" in SKILL
    for line in (
        "**What do you want to do next?**",
        "1. Want the full Senpi Signals sweep?",
        "2. Or I can check how your positions sit in this market.",
        "3. Or I can start planning a strategy with you to trade this market setup.",
    ):
        assert line in SKILL, line
    # order matters — signals leads
    assert (SKILL.index("1. Want the full Senpi Signals sweep?")
            < SKILL.index("2. Or I can check how your positions")
            < SKILL.index("3. Or I can start planning a strategy"))
    assert "Run `python3 scripts/sweep.py --print-feed` from the senpi-signals folder" in SKILL


def test_the_closing_stays_three_routes():
    """A fourth route turns a decision into a menu — and mirroring is the one that keeps trying to
    get in, because the 4h-leader layer sits right above it."""
    closing = SKILL[SKILL.index("**What do you want to do next?**"):]
    assert "4." not in closing[:300], "a fourth route crept into the closing"
    assert "Keep it to these three" in SKILL


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


def test_the_closing_does_not_offer_to_mirror_a_4h_winner():
    q = SKILL[SKILL.index("**What do you want to do next?**"):]
    assert "traders to mirror" not in q[:500]
    assert "mirror" not in q[:300].lower()


def test_the_strategy_route_offers_athena_first():
    route = SKILL[SKILL.index("**Strategy → Athena first, or one built for this market.**"):]
    assert route.index("**Athena**") < route.index("**senpi-strategy-author**")
    assert "**senpi-strategy-ops** runs its walkthrough and deploys it under their name" in route
    assert "Never promise or imply results." in route


def test_a_box_without_senpi_signals_skips_the_step_silently():
    assert ("If the senpi-signals folder isn't there, skip this step and the signals clause of the question, "
            "and say nothing about it.") in SKILL


def test_the_old_verbatim_cta_list_is_gone():
    for gone in ("## Mandatory closing (verbatim)", "The two CTAs", "Want me to check how our strategies",
                 "I can also check how your positions sit in this market, or build a strategy for it"):
        assert gone not in SKILL, gone
