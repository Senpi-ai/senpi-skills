"""The page-copy fields a strategy page reads from catalog.json keep to their rules.

senpi-web's strategy pages read `descriptor` (the line under the name), `summary` (the hero's
one-liner) and `risks` (where it can go wrong) from the catalog, and `shown_on` to fold a version
onto its root's page; `instances[].facts` gives every rule and the profit-lock chart. The copy
fields are optional to the page: a missing one falls back to what it showed before. So for now
this checks only the copy fields that are present; once every package carries descriptor, summary
and risks, it should also fail on a missing one.

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


# ---- instances[].facts: the rules and the profit-lock chart, without reading the package ----

def test_every_instance_has_complete_facts():
    skills = json.load(open(os.path.join(ROOT, "strategies", "catalog.json")))["skills"]
    for s in skills:
        assert s["instances"], f"{s['id']}: no instances"
        assert len(s["instances"]) == s["instance_count"]
        for inst in s["instances"]:
            facts = inst["facts"]
            assert facts and facts["v"] == G.FACTS_VERSION, f"{s['id']}/{inst['name']}: facts missing"
            ex = facts["exit"]
            assert ex["lock_steps"] == len(ex["lock_tiers"]), f"{s['id']}/{inst['name']}"
            assert ex["first_lock"] == (ex["lock_tiers"][0] if ex["lock_tiers"] else None)


def test_barracuda_facts():
    # The worked example in the catalog handoff: Barracuda's real values.
    skills = json.load(open(os.path.join(ROOT, "strategies", "catalog.json")))["skills"]
    (inst,) = next(s for s in skills if s["id"] == "barracuda")["instances"]
    assert inst["name"] == "main" and inst["funding_share"] == 1
    f = inst["facts"]
    assert (f["leverage_default"], f["leverage_max"], f["margin_pct"], f["slots"], f["scan_interval_s"]) == (5, 6, 30, 4, 60)
    assert f["exit"]["lock_tiers"] == [[15, 20], [20, 40], [25, 55], [30, 65], [40, 75], [50, 82], [60, 88], [75, 92], [100, 95]]
    assert f["exit"]["weak_peak_cut"] == {"after_min": 120, "under_pct": 5}
    assert f["exit"]["dead_weight_cut"] == {"after_min": 360}
    assert f["limits"] == {"max_entries_per_day": 20, "daily_loss_pct": 10,
                           "loss_streak": {"count": 5, "pause_s": 1200}, "drawdown_halt_pct": 20}


TIERS = [{"trigger_pct": 10, "lock_hw_pct": 30}, {"trigger_pct": 20, "lock_hw_pct": 60}]


def _exit(dsl_preset):
    return G.instance_facts({"exit": {"dsl_preset": dsl_preset}})["exit"]


def test_phase2_is_on_unless_disabled():
    # runtime-yaml.md: phase2 `enabled` defaults to true, so a file that omits it still locks.
    assert _exit({"phase2": {"tiers": TIERS}})["lock_tiers"] == [[10, 30], [20, 60]]
    assert _exit({"phase2": {"enabled": False, "tiers": TIERS}})["lock_steps"] == 0


def test_time_cuts_run_only_when_enabled():
    cut = {"interval_in_minutes": 60, "min_value": 3}
    assert _exit({"weak_peak_cut": cut})["weak_peak_cut"] is None
    assert _exit({"weak_peak_cut": {**cut, "enabled": True}})["weak_peak_cut"] == {"after_min": 60, "under_pct": 3}
    assert _exit({"dead_weight_cut": {"enabled": False, "interval_in_minutes": 60}})["dead_weight_cut"] is None


def test_scanner_margin_wins_over_the_strategy_default():
    rt = {"strategy": {"margin_pct": 20},
          "scanners": [{"type": "external_scanner", "inputs": {"marginPct": 35}}]}
    assert G.instance_facts(rt)["margin_pct"] == 35
    assert G.instance_facts({"strategy": {"margin_pct": 20}})["margin_pct"] == 20


def test_a_silent_file_gives_nulls():
    f = G.instance_facts({})
    assert f["margin_pct"] is f["slots"] is f["scan_interval_s"] is None
    assert f["exit"]["lock_steps"] == 0 and f["limits"]["loss_streak"] is None
