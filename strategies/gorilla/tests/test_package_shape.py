"""gorilla package-shape regression — margin units, DSL ladders, action wiring.

Guards the fleet-first CLOSE_POSITION wiring: each book must pair its rebalance
scanner with a rule-mode CLOSE_POSITION action, entries with OPEN_POSITION, and
every marginPct tier must be a PERCENT in (0,100] (never a v2 fraction).

Run: python3 strategies/gorilla/tests/test_package_shape.py
"""
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS = ["long", "short"]

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name}")


for book in BOOKS:
    with open(os.path.join(ROOT, book, "runtime.yaml")) as f:
        spec = yaml.safe_load(f)

    externals = [s for s in spec["scanners"] if s.get("type") == "external_scanner"]
    check(f"{book}: two external scanners", len(externals) == 2)
    by_entry = {s["entrypoint"]: s for s in externals}
    check(f"{book}: scan.py + rebalance.py", set(by_entry) == {"scan.py", "rebalance.py"})

    for s in externals:
        check(f"{book}/{s['name']}: validity set", s.get("default_signal_validity_seconds", 0) > 0)
        inputs = s.get("inputs") or {}
        for band, pct in (inputs.get("marginPctTiers") or {}).items():
            check(f"{book}/{s['name']} marginPct[{band}] percent-range", 1 <= float(pct) <= 100)
        side = inputs.get("side")
        check(f"{book}/{s['name']}: side matches book", side == ("LONG" if book == "long" else "SHORT"))

    actions = {a["action_type"]: a for a in spec["actions"]}
    check(f"{book}: has OPEN_POSITION", "OPEN_POSITION" in actions)
    check(f"{book}: has CLOSE_POSITION", "CLOSE_POSITION" in actions)
    check(f"{book}: has POSITION_TRACKER", "POSITION_TRACKER" in actions)
    close = actions.get("CLOSE_POSITION") or {}
    check(f"{book}: close is rule-mode", close.get("decision_mode") == "rule")
    check(f"{book}: close subscribed to rebalance",
          any("rebalance" in sc for sc in close.get("scanners", [])))
    open_a = actions.get("OPEN_POSITION") or {}
    check(f"{book}: open subscribed to entries",
          any("entries" in sc for sc in open_a.get("scanners", [])))

    # derived universe — the fund reads the market, not a preset list
    for s in externals:
        inp = s.get("inputs") or {}
        check(f"{book}/{s['name']}: no hardcoded universe", "universe" not in inp)
        check(f"{book}/{s['name']}: volume floor set", float(inp.get("universeVolFloorUsd", 0)) >= 1_000_000)
        check(f"{book}/{s['name']}: universe cap set", int(inp.get("universeMaxNames", 0)) >= 6)
        check(f"{book}/{s['name']}: override empty by default", (inp.get("universeOverride") or []) == [])

    # gorilla cadences — the ask: rethink 48h, rebalance 7d
    for s in externals:
        inp = s.get("inputs") or {}
        check(f"{book}/{s['name']}: thesisRefreshHours=48", float(inp.get("thesisRefreshHours", 0)) == 48)
        check(f"{book}/{s['name']}: rebalanceDays=7", float(inp.get("rebalanceDays", 0)) == 7)

    tiers = spec["exit"]["dsl_preset"]["phase2"]["tiers"]
    triggers = [t["trigger_pct"] for t in tiers]
    check(f"{book}: DSL tiers ascending", triggers == sorted(triggers))
    check(f"{book}: DSL triggers <= 100", all(0 < t <= 100 for t in triggers))
    check(f"{book}: DSL locks 0-100",
          all(0 <= t["lock_hw_pct"] <= 100 for t in tiers))

    # scanner code identical across books (shared-verbatim contract)
    for fname in ("scan.py", "rebalance.py", "scoring.py"):
        with open(os.path.join(ROOT, "long", "scanners", fname)) as a, \
             open(os.path.join(ROOT, book, "scanners", fname)) as b:
            check(f"{book}: {fname} identical to long book", a.read() == b.read())

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
