"""improve-trades vs quant-desk: "find leaks" has one home.

2026-09-23, a user with FIFTEEN live strategies sent `Find leaks on <replace with your hyperliquid
wallet address>` — the in-product chip, placeholder still in it. Their agent read
`senpi-improve-trades/SKILL.md`, ran `review.py`, and hand-rolled a leak analysis across all 15
wallets. `desk.py` was never invoked.

The cause is on the selection surface, which is all an agent reads when CHOOSING: both skills
claimed "where am I leaking". quant-desk owns it — it is the one with the priced-counterfactual
engine, and it reads any Hyperliquid wallet. improve-trades keeps the same ground for ONE trade or
ONE strategy's exits, which is a different question.

This is a cross-reference, not a carve-out: judging a strategy's exits against its mandate is still
improve-trades' job.
"""
import os
from pathlib import Path as _P

HERE = _P(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE.parent.parent


def _desc(skill):
    head = (ROOT / skill / "SKILL.md").read_text().split("license:")[0]
    return " ".join(head.lower().split())


def test_find_leaks_is_not_claimed_by_both_skills():
    """Deletion, not redirection. The description is the SELECTION surface and every char of it is
    rendered on every turn against a hard catalog budget — improve-trades is the second-largest
    description we ship. Adding a hand-off there costs +77 escaped chars to say what removing the
    trigger says for -32. quant-desk already claims the phrase; this skill simply stops."""
    mine, desk = _desc("senpi-improve-trades"), _desc("quant-desk")
    assert "where am i leaking money" in desk, "quant-desk no longer claims the leak question"
    assert "where am i leaking" not in mine, \
        "improve-trades still claims the leak question on the selection surface"
    assert "find leaks" not in mine


def test_the_hand_off_lives_in_the_body_where_it_is_free():
    """The body is read only AFTER the skill is chosen, so it costs nothing at selection time — the
    right home for anything that is not a trigger phrase."""
    body = (_P(HERE).parent / "SKILL.md").read_text().split("license:")[1]
    assert "quant-desk" in body, "an agent that lands here anyway is told nothing about the desk"
    assert "--book" in body, "a senpi user's whole book is the case that produced this"


def test_the_command_we_route_to_actually_exists():
    """Routing to a flag that is not on main sends every "find leaks" into an argparse error, which
    is worse than the collision it replaces. `--book` arrives with #755; this goes red until it
    lands, so the two cannot merge out of order."""
    desk = (ROOT / "quant-desk/scripts/desk.py").read_text()
    assert 'add_argument("--book"' in desk, \
        "senpi-improve-trades routes 'find leaks' to `desk.py --book`, which desk.py does not accept"


def test_improve_trades_keeps_the_single_trade_and_single_strategy_questions():
    """The correction that matters: this is a cross-reference, not a surrender. 'did I sell too
    early' and 'why is [strategy] losing' are still this skill's, and a carve-out would lose them."""
    mine = _desc("senpi-improve-trades")
    for keep in ("did i sell", "why is [strategy] losing", "what did my own limits block"):
        assert keep in mine, f"improve-trades gave away ground it owns: {keep!r}"
