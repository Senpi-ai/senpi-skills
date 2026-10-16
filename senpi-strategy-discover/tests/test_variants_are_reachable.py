"""A folded variant must still be REACHABLE — the one discoverability case nothing covered.

`fold_variants` collapses a variant family to one card so a parent and its four tunings do
not eat five of the eight slots. That is right, but it has a consequence nothing was testing:
a variant is ABSENT from the ranked list by design, so the usual "ranks top-3 for its own
query" assertion in test_new_strategy_discoverability.py cannot be written for one, and all
18 variants in the catalog were therefore unguarded. A variant that stopped being eligible —
a tag typo, a class that no longer matches, a `varies` pointing at an id that was renamed —
would look exactly like one that was correctly folded, and would simply never be offered.

Two properties, asserted for EVERY `varies:` package rather than for one new package:

  1. It survives the filter. Being folded is not being rejected.
  2. Its parent's card carries it in `variants`, so the agent can offer the family's tunings.
     This is the only route by which a folded variant reaches a user at all.

And one that catches the rename: every `varies` target must exist in the catalog. A dangling
parent id silently orphans the variant — `fold_variants` has no card to fold it into.

NOT asserted here, deliberately: that a variant ranks BELOW its parent pre-fold. `varies`
does mean "never outrank the template you vary", but that is enforced by the FOLD, not by the
ordering — and on an unconstrained query the order is alphabetical, so `athena-concentrated`
precedes `athena` for reasons that have nothing to do with precedence. An assertion there
passes or fails on spelling. The fold is the real guarantee and it is covered above.

Run: python3 -m pytest senpi-strategy-discover/tests/test_variants_are_reachable.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

_RECORDS = json.load(open(os.path.join(HERE, "..", "catalog.json"), encoding="utf-8"))["skills"]
_BY_ID = {r["id"]: r for r in _RECORDS}
_VARIANTS = sorted((r["id"], r["varies"]) for r in _RECORDS if r.get("varies"))


def _intent():
    return {"direction": None, "assets": [], "exclude": [], "budget": None,
            "_broadened_classes": None}


def test_the_catalog_actually_has_variants():
    """Guards the guard: if fold_variants or the `varies` key is ever renamed away, every
    parametrized test below would silently collapse to zero cases and pass."""
    assert len(_VARIANTS) >= 10, f"only {len(_VARIANTS)} variants found — did `varies` move?"


@pytest.mark.parametrize("vid,parent", _VARIANTS)
def test_every_variants_parent_exists(vid, parent):
    assert parent in _BY_ID, (
        f"{vid} declares `varies: {parent}` but no such package is in the catalog — the "
        f"variant has no card to fold into, so it can never be offered")


@pytest.mark.parametrize("vid,parent", _VARIANTS)
def test_a_variant_survives_the_filter_even_though_it_is_folded(vid, parent):
    res = discover.match(_intent(), _RECORDS)
    ids = {c["id"] for c in res["candidates"]}
    assert vid in ids, (
        f"{vid} was REJECTED by the filter on an unconstrained query, which is not the same "
        f"as being folded — it cannot be offered as a tuning of {parent} from here")


@pytest.mark.parametrize("vid,parent", _VARIANTS)
def test_the_parent_card_carries_the_variant_after_folding(vid, parent):
    """The only route a folded variant reaches a user by."""
    res = discover.match(_intent(), _RECORDS)
    discover.fold_variants(res, _RECORDS)
    card = next((c for c in res["candidates"] if c["id"] == parent), None)
    if card is None:
        pytest.skip(f"{parent} is itself folded or not returned on an unconstrained query")
    assert vid in (card.get("variants") or []), (
        f"{parent}'s card does not list {vid} in `variants` after folding — the variant is "
        f"invisible: absent from the ranked list AND absent from its parent's card")
