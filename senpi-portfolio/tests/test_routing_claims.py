#!/usr/bin/env python3
"""Who owns "why did it open" and "why did it close": the claims live in the descriptions.

A description is what the agent reads to choose a skill, and it is the criterion the skill router
classifies on. No description claimed why a position was OPENED, so a question about the signal
behind an automated entry reached no skill. And senpi-portfolio's "use FIRST for ANY portfolio /
strategies / positions / balances / PnL / trade-history question" pulled why-close questions and
the stop on a hand-opened position away from the skills that own them.

The split (skill-router ticket 11):
- senpi-portfolio: holdings, balances, PnL and current state, plus why a position was opened, read
  from the runtime's decision record;
- senpi-improve-trades: why the user's strategy closed a position;
- senpi-trade: why a manual or mirrored position closed, and the stop on a position opened by hand;
- senpi-trading-runtime: the runtime contract, not the reason behind one entry.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _split(name):
    text = (REPO / name / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{name}/SKILL.md has no frontmatter"
    return m.group(1), text[m.end():]


def _desc(name):
    return " ".join(yaml.safe_load(_split(name)[0])["description"].split())


def _body(name):
    return " ".join(_split(name)[1].split())


def test_portfolio_claims_why_a_position_was_opened():
    desc = _desc("senpi-portfolio").lower()
    assert "why a position was opened" in desc
    assert "what signal or scanner decision triggered" in desc


def test_portfolio_reads_an_entry_from_the_runtimes_decision_layer():
    """The claim is only honest if the procedure can answer it. The engine's read cannot: a position
    carries no open time and no reason. The runtime keeps both, and these are its commands on
    senpi-trading-runtime main (src/cli/senpi-commands.ts): `audit` and `explain` require -r."""
    body = _body("senpi-portfolio")
    for cmd in ("openclaw senpi runtime list --json",
                "openclaw senpi explain <ASSET> -r <runtime_id> --json",
                "openclaw senpi action decisions -r <runtime_id> --json",
                "openclaw senpi audit -r <runtime_id> --json"):
        assert cmd in body, f"the entry-attribution procedure does not run `{cmd}`"
    assert "signal.outcome" in body and "position.opened" in body, \
        "the procedure must name the events that tie the signal to the open"
    assert "never reconstruct" in body.lower(), "a reason not in the record is invented, not read"


def test_portfolio_is_first_for_holdings_and_state_not_for_any_question():
    """"FIRST for ANY portfolio / strategies / positions / balances / PnL / trade-history question"
    out-claimed every owner beside it: 17 of the 22 wrong pointers in the 2026-10-01 smoke runs."""
    desc = _desc("senpi-portfolio")
    assert "FIRST for ANY" not in desc
    assert "trade-history question" not in desc
    assert "FIRST for holdings, balances, PnL and current state" in desc
    # the closed-position facts and the quant-desk hand-off stay (quant-desk/tests pins the latter)
    assert "OPEN and CLOSED" in desc and "stays here" in desc


def test_portfolio_hands_why_closed_and_a_hand_opened_stop_to_their_owners():
    """A cross-reference, not a carve-out: the owner is named so the reader goes there."""
    desc = _desc("senpi-portfolio")
    handoff = desc[desc.index("Why a position CLOSED"):]
    assert "senpi-improve-trades" in handoff and "senpi-trade" in handoff
    assert "opened by hand" in handoff
    assert "outside Senpi" in handoff and "quant-desk" in handoff


def test_improve_trades_claims_why_the_strategy_closed_a_position():
    """p12-shaped questions fired portfolio at 0.64-0.72 while improve-trades sat at 0.36-0.41, and
    the record that answers them (`senpi dsl closes`, section 6b of its body) is improve-trades'."""
    desc = _desc("senpi-improve-trades").lower()
    assert "why did my strategy close x" in desc


def test_trade_claims_a_manual_close_and_a_hand_opened_stop():
    """p14-shaped questions fired portfolio at 0.67-0.69 while senpi-trade sat at 0.11-0.12. Its body
    already rules "Why did it close?" is a record question; the description never said so."""
    desc = _desc("senpi-trade").lower()
    assert "why did my manual or mirrored position close" in desc
    assert "the stop on a position i opened by hand" in desc


def test_trading_runtime_disclaims_the_reason_behind_one_entry():
    """p03-shaped questions reached the runtime contract at 0.48-0.53 through "scanner" and "bot".
    It documents the decision-layer CLI; it does not own the question one entry raises."""
    desc = _desc("senpi-trading-runtime")
    tail = desc[desc.index("NOT"):]
    assert "why a position was opened" in tail and "senpi-portfolio" in tail


def test_every_description_is_a_folded_scalar_that_parses():
    """A description that fails to parse, or changes scalar style, changes what every agent reads."""
    skills = sorted(p.parent.name for p in REPO.glob("*/SKILL.md"))
    assert len(skills) >= 15
    for name in skills:
        raw, _ = _split(name)
        style = re.search(r"^description:[ \t]*(\S*)", raw, re.M).group(1)
        assert style in (">-", ">"), f"{name}: description is not a folded scalar ({style!r})"
        desc = yaml.safe_load(raw)["description"]
        assert isinstance(desc, str) and desc.strip(), f"{name}: description did not parse to text"


def test_closed_trades_rule_does_not_send_every_why_close_to_improve_trades():
    """The body's "What happened to my [asset] / my closed trades" rule handed every why-it-closed to
    senpi-improve-trades, which contradicts the split above for a manual or mirrored close."""
    body = _body("senpi-portfolio").replace(" > ", " ")  # the rule sits in a blockquote
    start = body.index("**\"What happened to my [asset] / my closed trades\"**")
    rule = body[start:body.index("Never narrate a closed-position story", start)]
    assert "`senpi-improve-trades` for why-it-closed" in rule
    assert "a manual or mirrored one is `senpi-trade`" in rule, \
        "the closed-trades rule must name senpi-trade for a manual or mirrored close"
