"""The page-copy fields a strategy page reads from catalog.json keep to their rules.

senpi-web's strategy pages read `descriptor` (the line under the name), `summary` (the hero's
one-liner) and `risks` (where it can go wrong) from the catalog, and `shown_on` to fold a version
onto its root's page. All of them are optional to the page: a missing one falls back to what it
showed before. So for now this checks only the fields that are present; once every package carries
descriptor, summary and risks, it should also fail on a missing one.

Run: python3 -m pytest strategies/tests/test_catalog_page_fields.py -q
"""
import glob
import json
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "senpi-trading-runtime", "scripts"))
import gen_catalog as G  # noqa: E402

MANIFESTS = sorted(glob.glob(os.path.join(ROOT, "strategies", "*", "strategy.yaml")))


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: os.path.basename(os.path.dirname(p)))
def test_page_copy_keeps_its_rules(path):
    catalog = (yaml.safe_load(open(path)) or {}).get("catalog") or {}
    assert G.page_copy_problems(catalog) == []


@pytest.mark.parametrize("catalog, problem", [
    ({"descriptor": "x" * 46}, "`descriptor` is 46 characters (max 45)"),
    ({"descriptor": "Rides BTC trends."}, "`descriptor` ends with a full stop"),
    ({"summary": "x" * 141}, "`summary` is 141 characters (max 140)"),
    ({"risks": "  "}, "`risks` must be a non-empty string"),
    ({"categories": "conservative"}, "`categories` must be a list of slugs"),
])
def test_page_copy_rules_catch(catalog, problem):
    assert problem in G.page_copy_problems(catalog)


def test_page_copy_absent_is_fine():
    assert G.page_copy_problems({}) == []
    assert G.page_copy_problems({"descriptor": "Rides BTC while it trends"}) == []


def _shown_on(records):
    skills = [{"id": i, "varies": v} for i, v in records]
    G.stamp_shown_on(skills)
    return {s["id"]: s["shown_on"] for s in skills}


def test_shown_on_is_the_root_of_the_varies_chain():
    assert _shown_on([("penguin", None), ("penguin-5x", "penguin"), ("penguin-5x-tight", "penguin-5x")]) == {
        "penguin": "penguin", "penguin-5x": "penguin", "penguin-5x-tight": "penguin",
    }


def test_shown_on_is_null_when_the_root_is_not_in_the_catalog():
    # e.g. the parent package is blocked and skipped by the generator
    assert _shown_on([("crane-lite", "crane")]) == {"crane-lite": None}


def test_shown_on_survives_a_varies_loop():
    assert _shown_on([("a", "b"), ("b", "a")]) == {"a": None, "b": None}


def test_committed_catalog_carries_the_fields():
    skills = json.load(open(os.path.join(ROOT, "strategies", "catalog.json")))["skills"]
    ids = {s["id"] for s in skills}
    for s in skills:
        for key in ("descriptor", "summary", "risks", "categories", "shown_on"):
            assert key in s, f"{s['id']} lacks {key}: re-run gen_catalog.py"
        assert s["shown_on"] is None or s["shown_on"] in ids
