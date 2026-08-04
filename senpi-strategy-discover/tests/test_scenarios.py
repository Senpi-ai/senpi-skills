#!/usr/bin/env python3
"""End-to-end scenario tests: diverse user interactions + null-catalog robustness.
Run: python3 tests/test_scenarios.py

New contract: the script filters on concrete constraints only and returns ALL survivors (asset-match
desc, then name). Soft ranking (risk/belief/horizon) is the LLM's job, so those are NOT flags here.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

FIXTURE = discover.load_catalog(os.path.join(HERE, "fixtures", "catalog_fixture.json"))
# the real catalog resolved like production would (skill-local copy, then repo strategies/).
# NEVER a path that doesn't exist — load_catalog would fetch the remote catalog and cache it there
# (this once pointed two levels up, silently dropping a catalog.json at the repo root on every run).
REAL = discover.default_catalog()

_P = _F = 0


def ck(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
    else:
        _F += 1
        print(f"  FAIL: {name}  {detail}")


def intent(**kw):
    a = SimpleNamespace(**{k: None for k in ("assets", "direction", "budget", "exclude")})
    for k, v in kw.items():
        setattr(a, k, v)
    return discover.normalize_intent(a)


def m(catalog=FIXTURE, limit=None, **kw):
    return discover.match(intent(**kw), catalog, limit=limit)


def ids(r):
    return [c["id"] for c in r["candidates"]]


def top(r):
    return r["candidates"][0]["id"] if r["candidates"] else None


# ---- diverse user interactions (eyeball + assert) ----
def scenarios():
    print("\n== diverse user interactions (concrete flags; LLM ranks the rest) ==")
    cases = [
        ("only SOL", dict(assets="SOL")),
        ("trade stocks not crypto", dict(assets="xyz_equities", exclude="crypto")),
        ("long only, no shorting", dict(direction="long_only")),
        ("no copy-trading", dict(exclude="copy_trading")),
        ("wider alts", dict(assets="major_alts")),
        ("pre-IPO names", dict(assets="pre_ipo")),
        ("I don't know / empty", dict()),
    ]
    for label, kw in cases:
        r = m(**kw)
        cands = ", ".join(c["id"] for c in r["candidates"][:4])
        print(f"  · {label:30s} -> [{r['meta']['eligible_count']}] {cands or 'BUILD-CUSTOM'}")

    # critical assertions
    ck("only SOL -> kodiak present", "kodiak" in ids(m(assets="SOL")))
    ck("only SOL -> beaver(BTC) absent", "beaver" not in ids(m(assets="SOL")))
    ck("stocks-not-crypto -> bobcat top (asset-match order)", top(m(assets="xyz_equities", exclude="crypto")) == "bobcat")
    ck("stocks -> no crypto-only beaver", "beaver" not in ids(m(assets="xyz_equities")))
    by_id = {x["id"]: x for x in FIXTURE}
    ck("long_only -> tortoise present", "tortoise" in ids(m(direction="long_only")))
    ck("long_only -> no short_only surfaced",
       all(by_id[c]["direction"] != "short_only" for c in ids(m(direction="long_only"))))
    ck("no copy -> albatross gone", "albatross" not in ids(m(exclude="copy_trading")))
    ck("pre_ipo -> lemur present", "lemur" in ids(m(assets="pre_ipo")))
    ck("empty -> ALL 11 (no cut)", m()["meta"]["eligible_count"] == 11)
    ck("empty -> returned == eligible", m()["meta"]["returned_n"] == 11)


def below_floor():
    print("\n== below-floor budget (surface, never block) ==")
    r = m(assets="btc_eth", budget="$50")
    sp = next((c for c in r["candidates"] if c["id"] == "spider"), None)
    ck("below-floor surfaces a caveat (not blocked)",
       sp is not None and any("degraded" in cv for cv in sp["caveats"]), sp["caveats"] if sp else None)
    ck("below-floor still returns the strategy", "spider" in ids(r))
    if sp:
        print(f"  · $50 budget -> spider caveat: {sp['caveats']}")


def multi_instance():
    print("\n== multi-instance (spider) ==")
    r = m(assets="major_alts")
    sp = next((c for c in r["candidates"] if c["id"] == "spider"), None)
    ck("spider present for alts", sp is not None)
    if sp:
        ck("spider shows funding_split", sp.get("funding_split") == [0.6, 0.4])
        ck("spider has leg caveat", any("wallet" in cv.lower() for cv in sp["caveats"]))
        print(f"  · spider split={sp.get('funding_split')} budget=${sp['min_budget']} caveats={sp['caveats']}")


def null_catalog_robustness():
    print("\n== robustness against the REAL (pre-migration, null-bearing) catalog ==")
    try:
        real = discover.load_catalog(REAL)
    except Exception as e:  # noqa
        ck("real catalog loads", False, str(e))
        return
    nulls = [s["id"] for s in real if s.get("archetype") is None]
    print(f"  · real catalog: {len(real)} strategies, {len(nulls)} with null archetype: {nulls}")
    for kw in (dict(), dict(assets="btc_eth"), dict(assets="SOL"), dict(direction="long_only"),
               dict(exclude="copy_trading"), dict(assets="ZZZ_NOPE")):
        try:
            r = discover.match(intent(**kw), real)
            ck(f"real-catalog match {kw or 'empty'} no crash", isinstance(r["candidates"], list))
        except Exception as e:  # noqa
            ck(f"real-catalog match {kw or 'empty'} no crash", False, f"{type(e).__name__}: {e}")
    ck("real empty intent returns >=1", discover.match(intent(), real)["meta"]["returned_n"] >= 1)


def json_serializable():
    print("\n== output is JSON-serializable for every scenario ==")
    for kw in (dict(), dict(assets="btc_eth,SOL", budget="$300"),
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
