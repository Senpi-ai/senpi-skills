#!/usr/bin/env python3
"""--theme SOFT worldview search: scores candidates on thesis/tag match (+ regime synonyms), floats the
matches to the top, and NEVER drops a candidate. Guards the fix for "the agent eyeballed 78 theses and
missed the K-shape strategies we have" — a 'k-shape' theme must surface lion/cub/cougar/octopus.

Run: python3 tests/test_theme.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

CAT = discover.load_catalog(os.path.join(HERE, "fixtures", "catalog_fullfleet.json"))
ALL_IDS = {s["id"] for s in CAT}


def _broad():
    a = SimpleNamespace(assets=None, direction=None, budget=None, exclude=None)
    return discover.normalize_intent(a)


def _themed(query):
    res = discover.match(_broad(), CAT)
    return discover.apply_theme(res, query)


def test_k_shape_surfaces_the_long_short_books():
    res = _themed("k-shape")
    matches = res["meta"]["theme_matches"]
    match_ids = [m["id"] for m in matches]
    # the literal + structural K-shape books must all score and appear in the shortlist
    for i in ("lion", "cub", "cougar", "octopus"):
        assert i in ALL_IDS, f"{i} not in fixture"
        assert i in match_ids, f"{i} missing from theme_matches"
    # and they must rank at the TOP — the whole point (agent missed cougar by eyeballing names)
    top5 = match_ids[:5]
    assert "cougar" in top5 and "lion" in top5, f"K-shape L/S books not floated to top: {top5}"
    # matches are ordered by descending score
    scores = [m["theme_score"] for m in matches]
    assert scores == sorted(scores, reverse=True), "theme_matches not score-sorted"


def test_synonym_expansion_catches_non_literal_matches():
    """cub self-describes as 'Two-Speed-Market Long/Short' — it must match 'k-shape' via the synonym
    expansion (two-speed / long-short / winners), not only a literal 'k-shaped' substring."""
    res = _themed("k-shape")
    cub = next(c for c in res["candidates"] if c["id"] == "cub")
    assert cub["theme_score"] > 0
    assert any(h in ("two speed", "long short", "winners", "dispersion") for h in cub["theme_hits"])
    # expansion is echoed for transparency
    assert "long short" in res["meta"]["theme_expanded"]


def test_theme_never_drops_a_candidate():
    """SOFT surface: every survivor the concrete filter returned is still present after theming."""
    base = discover.match(_broad(), CAT)
    n_before = len(base["candidates"])
    res = _themed("k-shape")
    assert len(res["candidates"]) == n_before == len(ALL_IDS)
    # non-matches simply carry theme_score 0 (not removed)
    assert any(c.get("theme_score", 0) == 0 for c in res["candidates"])


def test_a_different_theme_surfaces_a_different_set():
    """Generality: 'risk-off' floats the defensive/tail-risk books, not the K-shape L/S ones."""
    res = _themed("risk-off")
    match_ids = [m["id"] for m in res["meta"]["theme_matches"]]
    assert match_ids, "risk-off surfaced nothing"
    # rhino (tail-risk / crisis-alpha) should score on the risk-off vocabulary if present in the fixture
    if "rhino" in ALL_IDS:
        assert "rhino" in match_ids


def test_no_theme_leaves_output_untouched():
    """Without --theme the output carries no theme keys (apply_theme is opt-in)."""
    res = discover.match(_broad(), CAT)
    assert "theme" not in res["meta"]
    assert all("theme_score" not in c for c in res["candidates"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
