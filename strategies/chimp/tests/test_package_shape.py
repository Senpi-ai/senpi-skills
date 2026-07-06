"""Regime-Allocator package-shape invariants — encode the corrected architecture.

The design guarantees, asserted structurally:
  * exactly ONE external scanner (not the cuttlefish/gorilla-v1 two-scanner mess),
  * NO CLOSE_POSITION action — DSL is the sole exit (rotate-by-attrition),
  * a slow recalibration clock (the market check is not nonstop),
  * a DERIVED universe (no hardcoded asset list),
  * marginPct tiers a PERCENT in (0,100].

Run: python3 strategies/chimp/tests/test_package_shape.py
"""
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_ID = os.path.basename(ROOT)
PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name}")


spec = yaml.safe_load(open(os.path.join(ROOT, "strategy.yaml")))
check("asset_scope universe (derived)", spec["catalog"]["asset_scope"] == "universe")
check("assets [] (nothing hardcoded)", spec["catalog"].get("assets") == [])

for inst in spec["instances"]:
    book = inst["name"]
    ry = yaml.safe_load(open(os.path.join(ROOT, book, inst["runtime"].split("/")[-1])))
    ext = [s for s in ry["scanners"] if s.get("type") == "external_scanner"]
    check(f"{book}: exactly ONE external scanner", len(ext) == 1)

    actions = {a["action_type"] for a in ry["actions"]}
    check(f"{book}: has OPEN_POSITION", "OPEN_POSITION" in actions)
    check(f"{book}: has POSITION_TRACKER", "POSITION_TRACKER" in actions)
    check(f"{book}: NO CLOSE_POSITION (DSL is the only exit)", "CLOSE_POSITION" not in actions)

    check(f"{book}: exit engine is dsl", ry["exit"]["engine"] == "dsl")
    tiers = ry["exit"]["dsl_preset"]["phase2"]["tiers"]
    trg = [t["trigger_pct"] for t in tiers]
    check(f"{book}: DSL tiers ascending, <=100", trg == sorted(trg) and all(0 < t <= 100 for t in trg))
    check(f"{book}: DSL phase1 stop present", ry["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] > 0)

    inp = ext[0]["inputs"]
    check(f"{book}: recalibration clock set (slow, not nonstop)", float(inp.get("recalibrationHours", 0)) >= 24)
    check(f"{book}: no hardcoded universe", "universe" not in inp and "assets" not in inp)
    check(f"{book}: both-dex vol floors set", float(inp.get("universeVolFloorUsd", 0)) > 0
          and float(inp.get("xyzVolFloorUsd", 0)) > 0)
    for band, pct in (inp.get("marginPctTiers") or {}).items():
        check(f"{book}: marginPct[{band}] in (0,100]", 0 < float(pct) <= 100)
    # scanner wakes must be far slower than the old 15m churn
    check(f"{book}: scanner cadence is a longer-play cadence (>=1h)",
          int(ext[0].get("interval_seconds", 0)) >= 3600)

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
