"""The budget an agent quotes is the CALCULATED minimum, never the $10 platform wallet floor.

Below the calculated minimum a wallet's smallest slot cannot reach the engine's bumped notional and the
sleeve no-trades — it funds, installs, scans and never opens. The walkthrough has to say that, and the
worked figure it uses has to stay true to `min_budget.py`, which is the arithmetic of record."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WALKTHROUGH = os.path.join(HERE, "..", "references", "walkthrough.md")
LIFECYCLE = os.path.join(HERE, "..", "references", "lifecycle.md")
AUTHOR = os.path.join(HERE, "..", "..", "senpi-strategy-author", "SKILL.md")
MIN_BUDGET = os.path.join(HERE, "..", "scripts", "min_budget.py")


def _min_budget_module():
    spec = importlib.util.spec_from_file_location("min_budget_under_test", MIN_BUDGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_walkthrough_quotes_the_calculated_minimum_not_the_floor():
    text = open(WALKTHROUGH, encoding="utf-8").read()
    for needle in ("never the $10 wallet floor",
                   "no-trades",
                   "min_budget.py",
                   "Never describe a below-minimum deploy"):
        assert needle in text, needle


def test_lifecycle_calls_a_shortfall_a_no_trade_not_a_degrade():
    text = open(LIFECYCLE, encoding="utf-8").read()
    assert "no-trades" in text
    assert "a warn is not permission to call the deploy fine" in text


def test_the_worked_example_matches_min_budget_py():
    """The walkthrough's "15%-margin, 3x sleeve needs $30 (... rounds 28.17 up)" is the real formula's answer."""
    mod = _min_budget_module()
    assert mod._per_wallet_min(15.0, 3.0) == 12.0 / (0.15 * 3.0) + 1.5
    assert round(mod._per_wallet_min(15.0, 3.0), 2) == 28.17
    assert mod._round_step(mod._per_wallet_min(15.0, 3.0)) == 30
    assert "$30 (the card rounds 28.17 up" in open(WALKTHROUGH, encoding="utf-8").read()
    # and the floor the agent must stop quoting really is the $10 + fee it looks like
    assert mod.WALLET_FLOOR == 10.0 and mod.FEE_BUFFER == 1.5


def test_author_checks_the_floor_before_a_design_and_quotes_the_minimum_at_handoff():
    """Before Decision 1 no design exists, so there is no minimum to compare against: the heads-up
    checks the ~$11.50 floor. The design's minimum is quoted where it first exists — Handoff step 1."""
    text = open(AUTHOR, encoding="utf-8").read()
    heads_up = text[text.index("### Funding heads-up"):text.index("- One heads-up total")]
    assert "- **Balance < ~$11.50/wallet**" in heads_up
    assert "not what the strategy needs to trade" in heads_up
    assert "$<min_budget>" not in heads_up and "that minimum" not in heads_up
    handoff = text[text.index("1. **Confirm with the user**"):text.index("2. **Preflight**")]
    assert "Quote the design's minimum before asking" in handoff
    assert "never opens a position" in handoff
