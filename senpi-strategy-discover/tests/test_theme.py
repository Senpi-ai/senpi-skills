#!/usr/bin/env python3
"""--theme SOFT worldview search — the ENGINE's scoring contract.

Division of labor (see SKILL.md): the LLM expands a worldview into its structural synonyms and passes
them in --theme; this engine does DETERMINISTIC weighted keyword-overlap over the real catalog fields.
So these tests feed the EXPANDED query (the terms the LLM would pass) and assert the engine ranks the
right shortlist — guarding the fix for "the agent eyeballed 78 theses and missed the K-shape strategies".
The engine holds NO synonym map of its own.

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

# the expanded queries the LLM is instructed to pass (SKILL.md few-shots), verbatim
K_SHAPE = "k-shape two-speed long-short divergence dispersion winners laggards"
RISK_OFF = "risk-off defensive recession bearish hedge downturn crisis"


def _broad():
    a = SimpleNamespace(assets=None, direction=None, budget=None, exclude=None)
    return discover.normalize_intent(a)


def _themed(query):
    res = discover.match(_broad(), CAT)
    return discover.apply_theme(res, query)


def test_expanded_kshape_query_surfaces_the_long_short_books():
    """Expanded terms in → correct shortlist out. The engine (no synonyms of its own) must score the
    literal + structural K-shape books from their OWN thesis words and float them to the top."""
    res = _themed(K_SHAPE)
    match_ids = [m["id"] for m in res["meta"]["theme_matches"]]
    for i in ("lion", "cub", "cougar", "octopus"):
        assert i in ALL_IDS, f"{i} not in fixture"
        assert i in match_ids, f"{i} missing from theme_matches"
    top5 = match_ids[:5]
    assert "cougar" in top5 and "lion" in top5, f"K-shape L/S books not floated to top: {top5}"
    scores = [m["theme_score"] for m in res["meta"]["theme_matches"]]
    assert scores == sorted(scores, reverse=True), "theme_matches not score-sorted"


def test_engine_matches_non_literal_from_the_expanded_terms():
    """cub self-describes as 'Two-Speed-Market Long/Short' — it scores because the LLM-expanded query
    carries 'two-speed' / 'long-short', NOT because the engine expanded a bare 'k-shape'."""
    res = _themed(K_SHAPE)
    cub = next(c for c in res["candidates"] if c["id"] == "cub")
    assert cub["theme_score"] > 0
    assert any(h in ("two speed", "long short", "winners", "dispersion") for h in cub["theme_hits"])
    # the echoed expansion is exactly the deterministic tokenization of the input — no added vocabulary
    assert "long short" in res["meta"]["theme_expanded"]
    assert "two speed" in res["meta"]["theme_expanded"]


def test_bare_query_is_NOT_expanded_by_the_engine():
    """The engine no longer carries a synonym map: a bare 'k-shape' tokenizes to just {'k shape'} and
    does NOT pull in 'two-speed'/'long-short' on its own (that's the LLM's job now)."""
    res = _themed("k-shape")
    expanded = res["meta"]["theme_expanded"]
    assert "two speed" not in expanded and "long short" not in expanded, \
        f"engine should not invent synonyms: {expanded}"


def test_theme_never_drops_a_candidate():
    base = discover.match(_broad(), CAT)
    n_before = len(base["candidates"])
    res = _themed(K_SHAPE)
    assert len(res["candidates"]) == n_before == len(ALL_IDS)
    assert any(c.get("theme_score", 0) == 0 for c in res["candidates"])


def test_a_different_theme_surfaces_a_different_set():
    """Generality: an expanded 'risk-off' worldview floats the defensive/tail-risk books."""
    res = _themed(RISK_OFF)
    match_ids = [m["id"] for m in res["meta"]["theme_matches"]]
    assert match_ids, "risk-off surfaced nothing"
    if "rhino" in ALL_IDS:
        assert "rhino" in match_ids


def test_no_theme_leaves_output_untouched():
    res = discover.match(_broad(), CAT)
    assert "theme" not in res["meta"]
    assert all("theme_score" not in c for c in res["candidates"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
