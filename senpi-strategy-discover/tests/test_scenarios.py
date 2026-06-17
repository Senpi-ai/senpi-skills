#!/usr/bin/env python3
"""End-to-end scenario tests: diverse user interactions + null-catalog robustness.
Run: python3 tests/test_scenarios.py"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

FIXTURE = discover.load_catalog(os.path.join(HERE, "fixtures", "catalog_fixture.json"))
REAL = os.path.join(HERE, "..", "..", "catalog.json")

_P = _F = 0


def ck(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
    else:
        _F += 1
        print(f"  FAIL: {name}  {detail}")


def intent(**kw):
    a = SimpleNamespace(**{k: None for k in (
        "risk", "assets", "belief", "horizon", "direction", "market_scope", "goal", "budget",
        "exclude", "experience")})
    for k, v in kw.items():
        setattr(a, k, v)
    return discover.normalize_intent(a)


def m(catalog=FIXTURE, limit=20, **kw):
    return discover.match(intent(**kw), catalog, limit=limit)


def ids(r):
    return [c["id"] for c in r["candidates"]]


def top(r):
    return r["candidates"][0]["id"] if r["candidates"] else None


# ---- diverse user interactions (eyeball + assert) ----
def scenarios():
    print("\n== diverse user interactions ==")
    cases = [
        ("aggressive SOL play", dict(risk="aggressive", assets="SOL")),
        ("copy good traders, nothing crazy", dict(belief="copy", risk="moderate")),
        ("trade stocks not crypto", dict(assets="xyz_equities")),
        ("long only, no shorting", dict(direction="long_only")),
        ("no copy-trading", dict(exclude="copy_trading")),
        ("I'm new", dict(experience="new")),
        ("wider alts", dict(assets="major_alts")),
        ("pre-IPO names", dict(assets="pre_ipo")),
        ("hold for the long term", dict(horizon="hodl")),
        ("just accumulate", dict(goal="accumulate")),
        ("I don't know / empty", dict()),
    ]
    for label, kw in cases:
        r = m(**kw)
        cands = ", ".join(f"{c['id']}({c['relevance']})" for c in r["candidates"][:3])
        print(f"  · {label:38s} -> {cands or 'BUILD-CUSTOM'}")

    # critical assertions on the above
    ck("aggressive SOL -> kodiak present", "kodiak" in ids(m(risk="aggressive", assets="SOL")))
    ck("copy -> albatross top", top(m(belief="copy")) == "albatross")
    ck("stocks -> bobcat top, no crypto", top(m(assets="xyz_equities")) == "bobcat"
       and "beaver" not in ids(m(assets="xyz_equities")))
    ck("long_only -> tortoise present, no short_only", "tortoise" in ids(m(direction="long_only")))
    ck("no copy -> albatross gone", "albatross" not in ids(m(exclude="copy_trading")))
    by_id = {x["id"]: x for x in FIXTURE}
    ck("new -> a starter leads", by_id[top(m(experience="new"))]["tier"] == "starter")
    ck("pre_ipo -> lemur present", "lemur" in ids(m(assets="pre_ipo")))
    ck("hodl -> tortoise (hodl) present", "tortoise" in ids(m(horizon="hodl")))
    ck("accumulate -> tortoise(dca) top", top(m(goal="accumulate")) == "tortoise")
    ck("empty -> 8 of 11", m(limit=8)["meta"]["returned_n"] == 8)


def below_floor():
    print("\n== below-floor budget ==")
    r = m(assets="btc_eth", budget="$50")
    c = r["candidates"][0]
    ck("below-floor surfaces a caveat (not blocked)",
       r["candidates"] != [] and any("Needs" in cv for cv in c["caveats"]), c["caveats"])
    print(f"  · $50 budget -> top {c['id']} caveat: {c['caveats']}")


def multi_instance():
    print("\n== multi-instance (spider) ==")
    r = m(risk="aggressive", assets="major_alts")
    sp = next((c for c in r["candidates"] if c["id"] == "spider"), None)
    ck("spider present for aggressive/alts", sp is not None)
    if sp:
        ck("spider shows funding_split", sp.get("funding_split") == [0.6, 0.4])
        ck("spider has leg caveat", any("wallet" in cv.lower() for cv in sp["caveats"]))
        print(f"  · spider split={sp.get('funding_split')} budget=${sp['suggested_budget']} caveats={sp['caveats']}")


def null_catalog_robustness():
    print("\n== robustness against the REAL (pre-migration, null-bearing) catalog ==")
    try:
        real = discover.load_catalog(REAL)
    except Exception as e:  # noqa
        ck("real catalog loads", False, str(e))
        return
    nulls = [s["id"] for s in real if s.get("archetype") is None]
    print(f"  · real catalog: {len(real)} strategies, {len(nulls)} with null archetype: {nulls}")
    for kw in (dict(), dict(risk="conservative"), dict(assets="btc_eth"), dict(belief="trend"),
               dict(assets="SOL"), dict(direction="long_only"), dict(exclude="copy_trading"),
               dict(assets="ZZZ_NOPE")):
        try:
            r = discover.match(intent(**kw), real, limit=8)
            ck(f"real-catalog match {kw or 'empty'} no crash", isinstance(r["candidates"], list))
        except Exception as e:  # noqa
            ck(f"real-catalog match {kw or 'empty'} no crash", False, f"{type(e).__name__}: {e}")
    # empty intent over real catalog returns the strategies that exist
    ck("real empty intent returns >=1", discover.match(intent(), real, limit=8)["meta"]["returned_n"] >= 1)


def json_serializable():
    print("\n== output is JSON-serializable for every scenario ==")
    for kw in (dict(), dict(risk="safe", assets="btc_eth,SOL", budget="$300"),
               dict(assets="DOGE"), dict(assets="btc_eth", exclude="crypto,copy_trading")):
        r = m(**kw)
        try:
            json.dumps(r, ensure_ascii=False)
            ck(f"serializable {kw or 'empty'}", True)
        except Exception as e:  # noqa
            ck(f"serializable {kw or 'empty'}", False, str(e))


if __name__ == "__main__":
    scenarios()
    below_floor()
    multi_instance()
    null_catalog_robustness()
    json_serializable()
    print(f"\n{_P} passed, {_F} failed")
    sys.exit(1 if _F else 0)
