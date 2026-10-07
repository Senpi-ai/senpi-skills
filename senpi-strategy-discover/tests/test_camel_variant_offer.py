#!/usr/bin/env python3
"""A user who says "run camel" must be offered BOTH camels — and the cap must not hide one.

Two separate things had to be true for this to work, and neither was:

1. **The ranking has to survive `--limit`.** `match()` truncated its survivors BEFORE anything
   scored them, ordering them neutrally (asset match, then NAME). So
   `--theme "camel funding carry" --limit 6` returned `asia-ai` at theme_score 0 and dropped both
   camels at 21, purely because `a` sorts before `c`. The cap is a safety valve on output size; it
   is not allowed to be a relevance filter.

2. **The agent has to be TOLD to name both.** `variant-families.md` otherwise says "never offer two
   members of the same family" and sends the user to the parent. Camel is now the second declared
   exception (after the chase caps), because camel-concentrated was posted about publicly on
   2026-10-07 and "run camel" became ambiguous.

The third assertion here is honesty rather than discoverability: camel-concentrated's whole pitch
is "at 10x", and the venue clamps to `min(requested, the name's cap)` on more than half the
universe. A user choosing between the two on a 10x premise is choosing on a number that does not
hold, so both docs have to disclose the clamp where the choice is made.

Runs offline against the bundled catalog — no MCP, no auth. Imports nothing: the CLI is driven by
subprocess (that is the surface the agent actually calls) and the docs are read as text, so this
file cannot leak a module onto another suite's import path.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DISCOVER = os.path.join(REPO, "senpi-strategy-discover", "scripts", "discover.py")
CATALOG = os.path.join(REPO, "strategies", "catalog.json")
FAMILIES = os.path.join(REPO, "senpi-strategy-discover", "references", "variant-families.md")
WALKTHROUGH = os.path.join(REPO, "senpi-strategy-ops", "references", "walkthrough.md")

_WORDS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}


def _run(*args):
    cmd = [sys.executable, DISCOVER, "--no-market"] + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"discover.py failed: {proc.stderr[-800:]}"
    return json.loads(proc.stdout)


def _ranked(res):
    return [(c["id"], c.get("theme_score", 0)) for c in res.get("candidates", [])]


def _catalog():
    return json.load(open(CATALOG, encoding="utf-8"))["skills"]


# ------------------------------------------------------------------ discoverability

def test_a_camel_query_surfaces_both_camels():
    ids = [i for i, _ in _ranked(_run("--theme", "camel funding carry"))]
    assert ids[:2] == ["camel", "camel-concentrated"], f"got {ids[:4]}"


def test_a_tight_limit_still_surfaces_both_camels():
    """The regression: --limit used to drop the top-scored candidates entirely."""
    for cap in (2, 3, 6):
        ids = [i for i, _ in _ranked(_run("--theme", "camel funding carry", "--limit", str(cap)))]
        assert len(ids) == cap, f"--limit {cap} returned {len(ids)}"
        assert ids[0] == "camel", f"--limit {cap}: expected camel first, got {ids}"
        assert "camel-concentrated" in ids, f"--limit {cap} hid camel-concentrated: {ids}"


def test_the_limit_is_a_prefix_of_the_full_ranking():
    """Stated generally, so this cannot regress for some other theme: whatever a capped run
    returns must be the first N of the uncapped ranking, not a differently-chosen subset."""
    for theme in ("camel funding carry", "market-neutral funding carry", "follow smart money"):
        full = [i for i, _ in _ranked(_run("--theme", theme))]
        for cap in (1, 3, 5):
            got = [i for i, _ in _ranked(_run("--theme", theme, "--limit", str(cap)))]
            assert got == full[:cap], f"theme {theme!r} --limit {cap}: {got} != {full[:cap]}"


def test_a_capped_run_never_returns_a_zero_scoring_candidate_over_a_scoring_one():
    res = _run("--theme", "camel funding carry", "--limit", "6")
    scores = [s for _, s in _ranked(res)]
    assert scores == sorted(scores, reverse=True), f"not ranked: {_ranked(res)}"
    full = _ranked(_run("--theme", "camel funding carry"))
    scoring = [i for i, s in full if s > 0]
    returned = [i for i, _ in _ranked(res)]
    dropped = [i for i in scoring[:6] if i not in returned]
    assert not dropped, f"dropped scoring candidates in favour of others: {dropped}"


def test_camel_concentrated_declares_camel_as_its_parent():
    """`varies` is what makes the agent able to SAY "this is camel with one change"."""
    rec = next((s for s in _catalog() if s["id"] == "camel-concentrated"), None)
    assert rec is not None, "camel-concentrated is not in the catalog"
    assert rec.get("varies") == "camel", f"varies = {rec.get('varies')!r}"


# ------------------------------------------------------------------ what the agent is told

def test_both_docs_tell_the_agent_to_offer_both_camels():
    fam = open(FAMILIES, encoding="utf-8").read()
    walk = open(WALKTHROUGH, encoding="utf-8").read()
    assert "two exceptions" in fam, (
        "variant-families.md must declare camel as the SECOND exception to 'never offer two "
        "members of the same family' — otherwise the agent sends every camel user to the parent")
    assert "camel-concentrated" in fam and "camel-concentrated" in walk
    assert re.search(r"camel", walk, re.I), "the ops walkthrough never mentions camel"
    assert "Camel Concentrated" in walk, (
        "the walkthrough must name Camel Concentrated in the block the agent reads at deploy time")


def test_the_leverage_clamp_is_disclosed_where_the_choice_is_made():
    """camel-concentrated is sold "at 10x" and gets it on under half the universe. A user picking
    between the two on that premise is picking on a number that does not hold."""
    for path in (FAMILIES, WALKTHROUGH):
        text = open(path, encoding="utf-8").read()
        assert "53.6%" in text, f"{os.path.basename(path)} does not state how often 10x is clamped"
        assert "clamp" in text.lower(), f"{os.path.basename(path)} does not name the clamp"


def test_the_documented_variant_count_matches_the_catalog():
    """Both docs open by counting the variants. camel-concentrated shipped and neither was updated,
    so both said fifteen when there were sixteen. Read the number rather than trusting it."""
    actual = len([s for s in _catalog() if s.get("varies")])
    for path, pattern in ((FAMILIES, r"(\w+) listed strategies are \*\*variants"),
                          (WALKTHROUGH, r"(\w+) listed templates are variants")):
        text = open(path, encoding="utf-8").read()
        m = re.search(pattern, text)
        assert m, f"{os.path.basename(path)}: could not find the variant-count sentence"
        stated = _WORDS.get(m.group(1).lower())
        assert stated is not None, f"{os.path.basename(path)}: unparsed count {m.group(1)!r}"
        assert stated == actual, (
            f"{os.path.basename(path)} says {m.group(1)} ({stated}) variants, the catalog has "
            f"{actual} — update the prose when a variant ships")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"CAMEL VARIANT OFFER OK — {len(fns)} checks")
