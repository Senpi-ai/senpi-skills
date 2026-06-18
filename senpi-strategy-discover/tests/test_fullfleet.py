#!/usr/bin/env python3
"""Aggressive stress test of the matcher over the full origin/main fleet (75 strategies).
Sweeps concrete-intent combinations; asserts no crashes, full reachability, correctness, latency.
Run: python3 tests/test_fullfleet.py  (regenerate fixture first if main changed — see gen_fullfleet.py)

New contract: the script filters on concrete constraints (assets / direction / exclude) and returns
ALL survivors. Soft ranking is the LLM's job, so the sweep only varies concrete dims.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import itertools
import os
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

CAT = discover.load_catalog(os.path.join(HERE, "fixtures", "catalog_fullfleet.json"))
ALL_IDS = {s["id"] for s in CAT}
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


def ids(r):
    return [c["id"] for c in r["candidates"]]


def arch(cid):
    return next(x for x in CAT if x["id"] == cid).get("archetype")


ASSETS = [None, "btc_eth", "major_alts", "universe_crypto", "xyz_equities", "commodities",
          "indices", "pre_ipo", "SOL", "NVDA", "DOGE", "btc_eth,SOL", "commodities,indices"]
DIRS = [None, "long_only", "short_only"]
EXCL = [None, "copy_trading", "stocks", "crypto", "commodities", "dca"]
BUDGETS = [None, "50", "500"]


def stress_sweep():
    print("\n== aggressive sweep (concrete dims) ==")
    seen, crashes, empty_only, total = set(), 0, 0, 0
    t0 = time.time()
    for asset, direction, exc, budget in itertools.product(ASSETS, DIRS, EXCL, BUDGETS):
        total += 1
        try:
            r = discover.match(intent(assets=asset, direction=direction, exclude=exc, budget=budget), CAT)
        except Exception as e:  # noqa
            crashes += 1
            if crashes <= 3:
                print(f"   CRASH asset={asset} dir={direction} exc={exc} budget={budget}: {e}")
            continue
        if not isinstance(r.get("candidates"), list) or r.get("build_custom", {}).get("route") != "senpi-strategy-author":
            crashes += 1
        if not r["candidates"]:
            empty_only += 1
        # invariant: returned == eligible (no top-N cut without a --limit)
        if r["meta"]["returned_n"] != r["meta"]["eligible_count"]:
            crashes += 1
        for c in r["candidates"]:
            seen.add(c["id"])
    dt = time.time() - t0
    print(f"   ran {total} intents in {dt:.2f}s ({1000*dt/total:.2f} ms/intent)")
    print(f"   crashes: {crashes} | build-custom-only results: {empty_only} ({100*empty_only/total:.1f}%)")
    ck("no crashes / invariant violations across full sweep", crashes == 0, f"{crashes}")
    missing = ALL_IDS - seen
    ck("every strategy reachable by some intent", not missing, f"unreachable: {sorted(missing)}")
    print(f"   reachable strategies: {len(seen)}/{len(ALL_IDS)}")
    ck("sweep is fast (<2ms/intent avg)", (1000 * dt / total) < 2.0, f"{1000*dt/total:.2f} ms")


def correctness():
    print("\n== correctness on the full fleet ==")
    def run(**kw):
        return discover.match(intent(**kw), CAT)

    # asset-class routing
    stocks = ids(run(assets="xyz_equities"))
    ck("stocks -> bobcat present", "bobcat" in stocks, stocks[:6])
    ck("stocks -> no pure-crypto (beaver)", "beaver" not in stocks)
    ck("commodities -> dire present", "dire" in ids(run(assets="commodities")))
    ck("indices -> iguana present", "iguana" in ids(run(assets="indices")))
    ck("pre_ipo -> lemur present", "lemur" in ids(run(assets="pre_ipo")))
    ck("btc_eth -> no xyz specialists",
       not ({"bobcat", "dire", "lemur", "iguana", "bald-eagle"} & set(ids(run(assets="btc_eth")))))

    # direction
    lo = ids(run(direction="long_only"))
    ck("long_only -> tortoise & sheep present", "tortoise" in lo and "sheep" in lo)
    ck("long_only -> no short_only surfaced", all(arch(c) is not None for c in lo) and
       all(next(x for x in CAT if x["id"] == cid)["direction"] != "short_only" for cid in lo))

    # exclude
    ck("exclude stocks -> bobcat gone", "bobcat" not in ids(run(exclude="stocks")))
    ck("exclude copy -> no copy_trading surfaced",
       not any(arch(cid) == "copy_trading" for cid in ids(run(exclude="copy_trading"))))
    ck("exclude crypto -> no crypto-class strategy surfaced", not any(
        set(next(x for x in CAT if x["id"] == cid)["asset_classes"]) & discover.CRYPTO_CLASSES
        for cid in ids(run(exclude="crypto"))))

    # named asset + degrade
    ck("named SOL -> non-empty", run(assets="SOL")["candidates"] != [])
    ck("unknown named -> broaden or build-custom (no crash)", isinstance(run(assets="WIFFLEBALL")["candidates"], list))

    # return-ALL invariant
    ck("empty intent returns all 75", run()["meta"]["eligible_count"] == len(ALL_IDS))
    ck("empty returned == eligible", run()["meta"]["returned_n"] == len(ALL_IDS))

    # WORLDVIEW / hedge-fund reachability (the whole point of thesis+tags)
    comm = ids(run(assets="commodities"))
    ck("commodities -> rhino (tail-risk) present", "rhino" in comm, comm[:8])
    ck("commodities -> thesis-war-escalation present", "thesis-war-escalation" in comm)
    allc = ids(run())
    ck("fleet has hedge funds (spider, rhino, ox)", {"spider", "rhino", "ox"} <= set(allc))
    ck("fleet has worldview thesis funds", {"thesis-war-escalation", "thesis-risk-off"} <= set(allc))
    # every candidate carries the thesis + tags surface
    ck("all candidates carry thesis", all(c.get("thesis") for c in run()["candidates"]))
    ck("all candidates carry tags", all(isinstance(c.get("tags"), list) and c["tags"] for c in run()["candidates"]))


if __name__ == "__main__":
    stress_sweep()
    correctness()
    print(f"\n{_P} passed, {_F} failed")
    sys.exit(1 if _F else 0)
