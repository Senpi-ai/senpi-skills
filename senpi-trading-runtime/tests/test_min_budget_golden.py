#!/usr/bin/env python3
"""Golden-file lock on every template's computed minimum budget.

The minimum is machine-computed from each package's marginPct / leverage / funding_share
(min_budget.py). This test recomputes all of them and asserts they equal the committed golden.
A marginPct/leverage/funding retune therefore MOVES a minimum -> this test fails -> the author
updates the golden in the SAME PR, so the budget change is visible in the diff (never silent)."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "senpi-trading-runtime", "scripts"))
import yaml  # noqa: E402
import min_budget as MB  # noqa: E402

GOLDEN = json.load(open(os.path.join(HERE, "fixtures", "min_budget_golden.json")))


def _compute(pkg_dir):
    man = yaml.safe_load(open(os.path.join(pkg_dir, "strategy.yaml"))) or {}
    rts = {}
    for inst in man.get("instances", []) or []:
        rts[inst["name"]] = yaml.safe_load(open(os.path.join(pkg_dir, inst["runtime"]))) or {}
    return man, MB.strategy_min_budget(man, rts)["min_budget"]


def _deployable_packages():
    import glob
    for man_path in sorted(glob.glob(os.path.join(ROOT, "strategies", "*", "strategy.yaml"))):
        man = yaml.safe_load(open(man_path)) or {}
        cat = man.get("catalog", {}) or {}
        if cat.get("status") == "blocked" or cat.get("deployable") is False:
            continue
        yield man.get("id", os.path.basename(os.path.dirname(man_path))), os.path.dirname(man_path)


def test_min_budget_matches_golden():
    computed = {}
    for sid, pkg in _deployable_packages():
        _, mb = _compute(pkg)
        computed[sid] = mb
    drift = {sid: (GOLDEN.get(sid), v) for sid, v in computed.items() if GOLDEN.get(sid) != v}
    missing = sorted(set(GOLDEN) - set(computed))
    extra = sorted(set(computed) - set(GOLDEN))
    assert not drift, f"min_budget drifted (golden, computed): {drift} — update fixtures/min_budget_golden.json"
    assert not missing, f"golden has templates not in the catalog: {missing}"
    assert not extra, f"new templates missing from golden: {extra} — regenerate the golden"


def test_no_authored_min_budget_remains():
    import glob, re
    bad = [p for p in glob.glob(os.path.join(ROOT, "strategies", "*", "strategy.yaml"))
           if any(re.match(r"^\s{2}min_budget:\s", ln) for ln in open(p))]
    assert not bad, f"authored min_budget: is now computed — delete it from: {bad}"


if __name__ == "__main__":
    test_min_budget_matches_golden()
    test_no_authored_min_budget_remains()
    print(f"GOLDEN OK — {len(GOLDEN)} templates, all computed minimums match")
