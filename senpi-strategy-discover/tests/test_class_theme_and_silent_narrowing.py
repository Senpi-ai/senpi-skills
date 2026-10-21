"""Two silent-result defects in discover.py, found 2026-10-12 while verifying that a new
single-asset package (ibex, a gold book) was reachable the ways an agent actually calls this.

Both share one shape, the one this repo keeps rediscovering: a narrowing that produces a
PLAUSIBLE-LOOKING answer instead of an error, so nothing downstream can tell the difference
between "few things match" and "the query never worked".

DEFECT 1 — a declared asset class was invisible to a theme query. `_THEME_FIELDS` scored
tags/tagline/name/archetype/thesis/belief_plain and NOT `asset_classes`, the one tag drawn
from a validated 7-token vocabulary that every record declares. Prose rarely repeats the
class name, so measured over the 133-package catalog a theme query missed:

    major_alts       61 of 61  (100%)      xyz_equities   29 of 39  (74%)
    universe_crypto  52 of 53  (98%)       commodities    22 of 37  (59%)
    btc_eth          37 of 47  (79%)       indices        16 of 31  (52%)

Ibex — which trades nothing but gold and declares `commodities` — scored ZERO on
`--theme commodities`, behind six broader packages that merely say the word in prose.
Patching tags package by package does not scale to that, and would rot; scoring the
declared field fixes every package at once.

DEFECT 2 — an unrecognized `--assets` token silently became a TICKER. `_norm_assets` is
handed a `warnings` list and never writes to it, by necessity: a token it does not know as a
class cannot be told apart from a legitimate ticker (`--assets NVDA`). So it becomes a named
constraint and `_hard_reject` drops every package that is not `_configurable`. The graceful
degrade in `match()` only fires when survivors reach ZERO, and a few configurable templates
always survive — so the caller gets a short, calm, wrong answer. `--assets class:commodities`
(a syntax that has never existed; the flag takes bare class tags) returned 2 of 133 with an
empty `warnings` list. Both sibling normalisers already warn: `_canon` on a bad direction,
`_norm_exclude` on a bad exclude. This was the one silent path, and the only one whose
failure mode is a near-empty shortlist.

Run: python3 -m pytest senpi-strategy-discover/tests/test_class_theme_and_silent_narrowing.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

_RECORDS = json.load(open(os.path.join(HERE, "..", "catalog.json"), encoding="utf-8"))["skills"]


def _intent(assets=None):
    return {"direction": None, "assets": assets or [], "exclude": [], "budget": None,
            "_broadened_classes": None}


def _themed(theme, assets=None):
    res = discover.match(_intent(assets), _RECORDS)
    discover.apply_theme(res, theme)
    return res


# ── DEFECT 1: the declared class is scored ────────────────────────────────────────────────

def test_asset_classes_is_a_scored_theme_field():
    """Pinned by name: dropping it back out is what reopens the 52-100%-per-class hole."""
    fields = dict((f, w) for f, w in discover._THEME_FIELDS)
    assert "asset_classes" in fields, (
        "asset_classes is no longer theme-scored — a class word a user types once again misses "
        "most of the catalog that declares it (measured: 100% of major_alts, 98% of "
        "universe_crypto, 79% of btc_eth)")
    assert fields["asset_classes"] == fields["tags"], (
        "asset_classes is the validated-vocabulary tag; it is scored at tag weight or the "
        "declared class loses to an incidental prose mention")


@pytest.mark.parametrize("cls,word", [("commodities", "commodities"), ("indices", "indices"),
                                      ("xyz_equities", "xyz equities"), ("major_alts", "major alts"),
                                      ("universe_crypto", "universe crypto"), ("btc_eth", "btc eth"),
                                      ("pre_ipo", "pre ipo")])
def test_every_package_declaring_a_class_scores_on_that_class_word(cls, word):
    """The generalized assertion — not one package's tags. EVERY record declaring the class must
    score, which is only true while the declared field itself is scored."""
    declared = {r["id"] for r in _RECORDS if cls in (r.get("asset_classes") or [])}
    if not declared:
        pytest.skip(f"no package declares {cls}")
    res = _themed(word)
    scored = {c["id"] for c in res["candidates"] if c.get("theme_score", 0) > 0}
    missed = sorted(declared - scored)
    assert not missed, (
        f"{len(missed)} of {len(declared)} packages declaring {cls} scored 0 on theme "
        f"{word!r} and are invisible to it: {missed[:10]}")


def test_a_single_asset_package_is_visible_on_its_class_word():
    """The case that surfaced this. A pure-gold package must at least be SCORED on
    'commodities' — it need not outrank the diversified commodity baskets, which is the right
    answer for a whole-class ask, but it must not be unscored."""
    ids = {r["id"] for r in _RECORDS}
    if "ibex" not in ids:
        pytest.skip("ibex not in this catalog")
    hit = next(c for c in _themed("commodities")["candidates"] if c["id"] == "ibex")
    assert hit.get("theme_score", 0) > 0, "the gold package scores 0 on 'commodities'"


# ── DEFECT 2: the narrowing says so ───────────────────────────────────────────────────────

def test_an_undeclared_named_asset_is_reported_not_silent():
    res = discover.match(_intent(assets=[("named", "CLASS:COMMODITIES")]), _RECORDS)
    warns = [w for w in res["meta"]["warnings"] if "no template declares" in w]
    assert warns, (
        "a named asset no template declares collapsed the catalog to configurable templates "
        f"only ({len(res['candidates'])} of {len(_RECORDS)}) and said nothing about it")
    assert "CLASS:COMMODITIES" in warns[0]
    assert "commodities" in warns[0], "the warning must name the real class-tag vocabulary"


def test_the_warning_names_the_narrowing_not_just_the_token():
    """A warning that does not say WHY the list is short is barely better than silence."""
    res = discover.match(_intent(assets=[("named", "NOTACOIN")]), _RECORDS)
    w = next(x for x in res["meta"]["warnings"] if "no template declares" in x)
    assert "TICKER" in w and "configurable" in w


@pytest.mark.parametrize("ticker", ["BTC", "ETH", "SOL", "HYPE", "NVDA", "xyz:GOLD"])
def test_a_real_declared_asset_does_not_warn(ticker):
    """The warning is worthless if it cries on every legitimate named ask."""
    res = discover.match(_intent(assets=[("named", ticker.upper())]), _RECORDS)
    assert not [w for w in res["meta"]["warnings"] if "no template declares" in w], (
        f"{ticker} is declared by a template but was reported as undeclared")


def test_an_undeclared_coin_still_returns_the_configurable_route():
    """The warning explains the short list; it must not change it. gecko still survives, which
    is what makes the advice in the warning ('can be pointed at it at deploy') true."""
    res = discover.match(_intent(assets=[("named", "VVV")]), _RECORDS)
    assert "gecko" in {c["id"] for c in res["candidates"]}
    assert [w for w in res["meta"]["warnings"] if "no template declares" in w]


def test_parse_warnings_are_preserved_alongside_the_new_one():
    """`warnings` is now a copy — a dropped-direction warning must still come through."""
    w = []
    discover._canon("sideways", discover.DIRECTION_SYN, w, "direction")
    assert w, "precondition: _canon warns on an unrecognized direction"
    itn = _intent(assets=[("named", "VVV")])
    itn["_warnings"] = list(w)
    res = discover.match(itn, _RECORDS)
    assert any("sideways" in x for x in res["meta"]["warnings"])
    assert any("no template declares" in x for x in res["meta"]["warnings"])
