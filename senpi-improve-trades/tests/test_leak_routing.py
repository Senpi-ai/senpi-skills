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
    """The phrase may still APPEAR here — it has to, to redirect — but never as a trigger this
    skill claims. Every occurrence must sit next to the hand-off, which is the difference between
    "ask me this" and "ask the desk this"."""
    mine, desk = _desc("senpi-improve-trades"), _desc("quant-desk")
    assert "where am i leaking money" in desk, "quant-desk no longer claims the leak question"
    i = 0
    seen = 0
    while (i := mine.find("where am i leaking", i)) != -1:
        seen += 1
        assert "quant-desk" in mine[i:i + 120], \
            "improve-trades claims the leak question without handing it off"
        i += 1
    assert seen, "the redirect is gone — an agent reading this skill learns nothing about the desk"


def test_improve_trades_points_at_the_desk_by_name_and_command():
    mine = _desc("senpi-improve-trades")
    assert "quant-desk" in mine, "the agent is told the question is not ours but not where it goes"
    assert "--book" in mine, "a senpi user's whole book is the case that produced this"


def test_improve_trades_keeps_the_single_trade_and_single_strategy_questions():
    """The correction that matters: this is a cross-reference, not a surrender. 'did I sell too
    early' and 'why is [strategy] losing' are still this skill's, and a carve-out would lose them."""
    mine = _desc("senpi-improve-trades")
    for keep in ("did i sell", "why is [strategy] losing", "what did my own limits block"):
        assert keep in mine, f"improve-trades gave away ground it owns: {keep!r}"
