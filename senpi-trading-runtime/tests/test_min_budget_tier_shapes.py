"""`leverageTiers` rows come in two shapes, and reading the wrong element under-funds a wallet.

    [min_score, leverage]              — the original shape
    [min_score, leverage, marginPct]   — condor / cheetah / wolverine

`_lev_values` took `row[-1]`, which is the leverage in the first shape and the MARGIN PERCENT in
the second. Read as an 18-80x leverage instead of a 5-10x one, the notional check passed on a
wallet that could not actually open the position: cheetah computed a $10 minimum where its
smallest tier needs $15 (18% margin x 5x on $10 = $9 notional, under the $12 bumped minimum).
The other three packages happened to floor at WALLET_FLOOR, which masked it.

That is the "deployed but not working" failure class — the user funds the advertised minimum and
their first signal silently fails to open. The golden test covers this only for the exact configs
shipped today; these pin the resolver itself.

Run: python3 -m pytest senpi-trading-runtime/tests/test_min_budget_tier_shapes.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import min_budget as mb  # noqa: E402


def test_leverage_is_element_one_in_both_tier_shapes():
    assert mb._lev_values([[14, 8], [12, 7], [10, 3]]) == [8.0, 7.0, 3.0], "2-element shape"
    assert mb._lev_values([[14, 5, 48], [12, 5, 42], [10, 5, 18]]) == [5.0, 5.0, 5.0], (
        "3-element shape — element 2 is marginPct, NOT a leverage. Reading it as one inflates the "
        "assumed leverage and under-computes the minimum budget.")


def test_a_margin_percent_is_never_mistaken_for_a_leverage():
    """The regression in its own terms: a 5x book whose margin tiers reach 48% must not resolve a
    48x minimum leverage, which would claim a wallet 10x smaller than reality can trade."""
    tiers = [[14, 5, 48], [12, 5, 42], [11, 5, 30], [10, 5, 18]]
    assert min(mb._lev_values(tiers)) == 5.0
    inputs = [{"leverageTiers": tiers, "marginPct": 18}]
    assert mb._min_leverage({}, inputs) == 5.0
    per_wallet = mb._per_wallet_min(mb._margin_pct({}, inputs), mb._min_leverage({}, inputs))
    assert per_wallet > mb.WALLET_FLOOR, (
        "an 18%-margin 5x slot needs more than the bare wallet floor to clear the bumped notional; "
        "if this floors, the resolver is reading the margin as leverage again")


def test_dict_shaped_tiers_still_resolve():
    """wolverine-style {apex, good, base} maps must keep working — the fix touches lists only."""
    assert sorted(mb._lev_values({"apex": 7, "good": 5, "base": 3})) == [3.0, 5.0, 7.0]
