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
import strategy_facts as SF  # noqa: E402

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
    ({"categories": ["golds"]}, "`categories` has unknown slug(s) ['golds']"),
])
def test_page_copy_rules_catch(catalog, problem):
    assert any(p.startswith(problem) for p in G.page_copy_problems(catalog))


def test_page_copy_absent_is_fine():
    assert G.page_copy_problems({}) == []
    assert G.page_copy_problems({"descriptor": "Rides BTC while it trends"}) == []


def _shown_on(records):
    skills = [{"id": i, "varies": v} for i, v in records]
    G.stamp_shown_on(skills)
    return {s["id"]: s["shown_on"] for s in skills}


def test_shown_on_is_the_root_of_the_varies_chain_on_versions_only():
    assert _shown_on([("penguin", None), ("penguin-5x", "penguin"), ("penguin-5x-tight", "penguin-5x")]) == {
        "penguin": None, "penguin-5x": "penguin", "penguin-5x-tight": "penguin",
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
        for key in ("descriptor", "summary", "risks", "categories", "shown_on",
                    "added", "updated", "updates", "instances"):
            assert key in s, f"{s['id']} lacks {key}: re-run gen_catalog.py"
        assert s["shown_on"] is None or (s["varies"] and s["shown_on"] in ids)


# ---- categories ----

def _categories(records):
    skills = [{"id": i, "varies": v, "risk_level": r, "categories": c} for i, v, r, c in records]
    G.stamp_shown_on(skills)
    G.stamp_categories(skills)
    return {s["id"]: s["categories"] for s in skills}


def test_conservative_follows_risk_level_and_versions_inherit():
    assert _categories([
        ("dog", None, "conservative", []),
        ("dog-gold", "dog", "moderate", ["gold"]),
        ("owl", None, "aggressive", ["ict-smc"]),
    ]) == {"dog": ["conservative"], "dog-gold": ["gold", "conservative"], "owl": ["ict-smc"]}


def test_spec_category_members():
    # Strategy Pages handoff, section 4: who is on each category page today.
    skills = {s["id"]: s for s in json.load(open(os.path.join(ROOT, "strategies", "catalog.json")))["skills"]}
    members = {
        "single-stocks": ["magpie", "hen", "hornet", "cub", "asia-ai", "cougar", "lion", "mongoose", "bobcat", "osprey"],
        "stock-index-scalps": ["oryx"],
        "conservative": ["dog", "pangolin", "tortoise", "hedgehog", "sheep", "armadillo", "mandate", "ox", "koala"],
        "gold": ["boar", "ibex", "ram"],
        "ict-smc": ["viper", "kite", "manta"],
        "dca": ["tortoise", "koala"],
        "fx": ["manta"],
        "memecoins-new-listings": ["magpie", "vulture"],
    }
    for slug, ids in members.items():
        for sid in ids:
            if sid in skills:
                assert slug in skills[sid]["categories"], f"{sid} should be on {slug}"


# ---- added, updated, updates ----

def _dates(prev, version, today="2026-12-01"):
    skills = [{"id": "x", "version": version}]
    G.stamp_dates(skills, {"x": prev}, today)
    return {k: skills[0][k] for k in ("added", "updated", "updates")}


def test_dates_carry_forward_while_the_version_holds():
    prev = {"version": "1.2.0", "added": "2026-03-16", "updated": "2026-09-17", "updates": 5}
    assert _dates(prev, "1.2.0") == {"added": "2026-03-16", "updated": "2026-09-17", "updates": 5}


def test_a_version_change_stamps_the_run_date_and_counts_one():
    prev = {"version": "1.2.0", "added": "2026-03-16", "updated": "2026-09-17", "updates": 5}
    assert _dates(prev, "1.3.0") == {"added": "2026-03-16", "updated": "2026-12-01", "updates": 6}


def test_barracuda_dates():
    # Seeded from git through the old top-level barracuda/ folder; matches the spec's fixture.
    skills = json.load(open(os.path.join(ROOT, "strategies", "catalog.json")))["skills"]
    b = next(s for s in skills if s["id"] == "barracuda")
    if b["version"] == "1.2.0":
        assert (b["added"], b["updated"], b["updates"]) == ("2026-03-16", "2026-09-17", 5)


# ---- instances[].facts: StrategyFacts v1, as senpi-trading-runtime computes it ----

def test_every_instance_has_facts():
    skills = json.load(open(os.path.join(ROOT, "strategies", "catalog.json")))["skills"]
    for s in skills:
        assert len(s["instances"]) == s["instance_count"], s["id"]
        for inst in s["instances"]:
            facts = inst["facts"]
            assert facts and facts["v"] == SF.FACTS_VERSION, f"{s['id']}/{inst['name']}: no facts"
            ex = facts["exit"]
            assert ex is not None, f"{s['id']}/{inst['name']}: the engine would refuse its exit"
            assert ex["lock_steps"] == len(ex["lock_tiers"]), f"{s['id']}/{inst['name']}"
            assert ex["first_lock"] == (ex["lock_tiers"][0] if ex["lock_tiers"] else None)


def test_barracuda_facts_match_the_runtime():
    # What @senpi-ai/runtime 3.0.212 strategyFacts() returns for barracuda/main, plus lock_tiers.
    skills = json.load(open(os.path.join(ROOT, "strategies", "catalog.json")))["skills"]
    (inst,) = next(s for s in skills if s["id"] == "barracuda")["instances"]
    assert inst["name"] == "main" and inst["funding_share"] == 1
    assert inst["facts"] == {
        "v": 1, "assets": None, "universe": None, "direction": "long_short",
        "leverage_default": 5, "leverage_max": None, "margin_pct": 30, "margin_pct_max": None,
        "slots": 4, "scan_interval_s": 180,
        "exit": {"loss_cap_pct": 7, "time_cap_min": None, "first_lock": [15, 20], "lock_steps": 9,
                 "lock_tiers": [[15, 20], [20, 40], [25, 55], [30, 65], [40, 75], [50, 82],
                                [60, 88], [75, 92], [100, 95]],
                 "weak_peak_cut": {"after_min": 120, "under_pct": 5},
                 "dead_weight_cut": {"after_min": 360}},
        "limits": {"max_entries_per_day": 20, "daily_loss_pct": 10,
                   "loss_streak": {"count": 5, "pause_s": 1200}, "drawdown_halt_pct": 20,
                   "same_market_wait_s": 7200},
    }


TIERS = [{"trigger_pct": 10, "lock_hw_pct": 30}, {"trigger_pct": 20, "lock_hw_pct": 60}]
P1 = {"enabled": True, "max_loss_pct": 8, "retrace_threshold": 5, "consecutive_breaches_required": 1}


def _exit(**preset):
    return SF.strategy_facts({"exit": {"dsl_preset": {"phase1": P1, **preset}}})["exit"]


def test_phase2_is_on_unless_disabled():
    assert _exit(phase2={"tiers": TIERS})["lock_tiers"] == [[10, 30], [20, 60]]
    assert _exit(phase2={"enabled": False, "tiers": TIERS})["lock_steps"] == 0


def test_time_cuts_run_only_when_enabled_and_clamp_to_the_exit_poll():
    cut = {"interval_in_minutes": 60, "min_value": 3}
    assert _exit(phase2={"tiers": TIERS}, weak_peak_cut=cut)["weak_peak_cut"] is None
    on = _exit(phase2={"tiers": TIERS}, weak_peak_cut={**cut, "enabled": True})
    assert on["weak_peak_cut"] == {"after_min": 60, "under_pct": 3}
    fast = SF.strategy_facts({"exit": {"interval_seconds": 600, "dsl_preset": {
        "phase1": P1, "phase2": {"tiers": TIERS},
        "hard_timeout": {"enabled": True, "interval_in_minutes": 5}}}})
    assert fast["exit"]["time_cap_min"] == 10


def test_loss_cap_resolution():
    # map-exit-config.ts: preset max_loss_pct -> phase1.max_loss_pct -> 1 when phase1 is off.
    off = {"enabled": False}
    assert SF.strategy_facts({"exit": {"dsl_preset": {"phase1": off, "phase2": {"tiers": TIERS}}}})["exit"]["loss_cap_pct"] == 1
    assert _exit(phase2={"tiers": TIERS})["loss_cap_pct"] == 8


def test_a_preset_the_engine_refuses_gives_no_exit():
    unsorted = [TIERS[1], TIERS[0]]
    assert _exit(phase2={"tiers": unsorted}) is None
    assert SF.strategy_facts({"exit": {"dsl_preset": {"phase2": {"tiers": TIERS}}}})["exit"] is None


def test_strategy_margin_wins_over_the_scanner():
    # facts.ts marginFacts: strategy.margin_pct first, the entry scanner's inputs only without it.
    rt = {"strategy": {"margin_pct": 20},
          "scanners": [{"type": "external_scanner", "inputs": {"marginPct": 35}}]}
    assert SF.strategy_facts(rt)["margin_pct"] == 20
    rt["strategy"] = {}
    assert SF.strategy_facts(rt)["margin_pct"] == 35


def test_numbers_are_written_as_the_runtime_writes_them():
    rt = {"risk": {"guard_rails": {"daily_loss_limit_pct": 10.0}}}
    assert repr(SF.strategy_facts(rt)["limits"]["daily_loss_pct"]) == "10"


def test_a_silent_file_gives_nulls():
    f = SF.strategy_facts({})
    assert f["margin_pct"] is f["slots"] is f["scan_interval_s"] is f["exit"] is f["limits"] is None


# ---- writing page copy needs no version bump ----

def test_page_copy_is_outside_the_behaviour_hash(tmp_path):
    import behaviour_hash as BH
    (tmp_path / "strategy.yaml").write_text('version: "1.0.0"\ncatalog:\n  name: "X"\n')
    before = BH.package_hash(str(tmp_path))[1]
    (tmp_path / "strategy.yaml").write_text(
        'version: "1.0.0"\ncatalog:\n  name: "X"\n  descriptor: "Rides trends"\n'
        '  summary: "One line."\n  risks: "Chop."\n  categories: [gold]\n')
    assert BH.package_hash(str(tmp_path))[1] == before
    (tmp_path / "strategy.yaml").write_text('version: "1.0.0"\ncatalog:\n  name: "Y"\n')
    assert BH.package_hash(str(tmp_path))[1] != before  # the rest of catalog: still counts
