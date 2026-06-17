#!/usr/bin/env python3
"""Aggressive stress test of the matcher over the full ~54-strategy fleet catalog.
Sweeps thousands of intent combinations; asserts no crashes, full reachability, correctness, latency.
Run: python3 tests/test_fullfleet.py  (regenerate fixture first if main changed — see gen_fullfleet.py)
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
    a = SimpleNamespace(**{k: None for k in (
        "risk", "assets", "belief", "horizon", "direction", "market_scope", "goal", "budget",
        "exclude", "experience")})
    for k, v in kw.items():
        setattr(a, k, v)
    return discover.normalize_intent(a)


def ids(r, limit=60):
    return [c["id"] for c in r["candidates"]]


RISKS = [None, "conservative", "moderate", "aggressive"]
ASSETS = [None, "btc_eth", "major_alts", "universe_crypto", "xyz_equities", "commodities",
          "indices", "pre_ipo", "SOL", "NVDA", "DOGE"]
BELIEFS = [None, "trend", "contrarian", "copy", "breakout", "structural", "single_market"]
DIRS = [None, "long_only", "short_only"]
EXCL = [None, "copy_trading", "stocks", "crypto"]


def stress_sweep():
    print("\n== aggressive sweep ==")
    seen, crashes, empty_only, total = set(), 0, 0, 0
    t0 = time.time()
    for risk, asset, belief, direction, exc in itertools.product(RISKS, ASSETS, BELIEFS, DIRS, EXCL):
        total += 1
        try:
            r = discover.match(intent(risk=risk, assets=asset, belief=belief, direction=direction,
                                      exclude=exc), CAT, limit=60)
        except Exception as e:  # noqa
            crashes += 1
            if crashes <= 3:
                print(f"   CRASH risk={risk} asset={asset} belief={belief} dir={direction} exc={exc}: {e}")
            continue
        # invariants on every result
        if not isinstance(r.get("candidates"), list) or r.get("build_custom", {}).get("route") != "senpi-strategy-author":
            crashes += 1
        if not r["candidates"]:
            empty_only += 1
        for c in r["candidates"]:
            seen.add(c["id"])
    dt = time.time() - t0
    print(f"   ran {total} intents in {dt:.2f}s ({1000*dt/total:.2f} ms/intent)")
    print(f"   crashes: {crashes} | build-custom-only results: {empty_only} ({100*empty_only/total:.1f}%)")
    ck("no crashes across full sweep", crashes == 0, f"{crashes} crashes")
    missing = ALL_IDS - seen
    ck("every strategy reachable by some intent", not missing, f"unreachable: {sorted(missing)}")
    print(f"   reachable strategies: {len(seen)}/{len(ALL_IDS)}")
    ck("sweep is fast (<2ms/intent avg)", (1000 * dt / total) < 2.0, f"{1000*dt/total:.2f} ms")


def correctness():
    print("\n== correctness on the full fleet ==")
    def run(**kw):
        return discover.match(intent(**kw), CAT, limit=60)

    # asset-class routing
    stocks = ids(run(assets="xyz_equities"))
    ck("stocks -> bobcat present", "bobcat" in stocks, stocks[:6])
    ck("stocks -> no pure-crypto (beaver) ", "beaver" not in stocks)
    ck("oil -> dire present", "dire" in ids(run(assets="commodities")))
    ck("indices -> iguana present", "iguana" in ids(run(assets="indices")))
    ck("pre_ipo -> lemur present", "lemur" in ids(run(assets="pre_ipo")))
    ck("btc_eth -> no xyz specialists", not ({"bobcat", "dire", "lemur", "iguana"} & set(ids(run(assets="btc_eth")))))

    # belief routing
    copies = ids(run(belief="copy"))
    ck("copy -> >=4 copy strategies surface", sum(1 for c in CAT if c["archetype"] == "copy_trading"
        and c["id"] in copies) >= 4, copies[:6])
    ck("contrarian -> a contrarian leads",
       next(x for x in CAT if x["id"] == ids(run(belief="contrarian"))[0])["archetype"] == "contrarian_fade")

    # direction
    lo = ids(run(direction="long_only"))
    ck("long_only -> tortoise & sheep present", "tortoise" in lo and "sheep" in lo)
    ck("long_only -> no short_only surfaced",
       all(next(x for x in CAT if x["id"] == cid)["direction"] != "short_only" for cid in lo))

    # exclude
    ck("exclude stocks -> bobcat gone", "bobcat" not in ids(run(exclude="stocks")))
    ck("exclude copy -> no copy_trading surfaced",
       not any(next(x for x in CAT if x["id"] == cid)["archetype"] == "copy_trading"
               for cid in ids(run(exclude="copy_trading"))))

    # named asset + degrade
    ns = run(assets="SOL")
    ck("named SOL -> candidates trade SOL or broadened", ns["candidates"] != [])
    dg = run(assets="WIFFLEBALL")
    ck("unknown named -> broaden or build-custom (no crash)", isinstance(dg["candidates"], list))

    # combined realistic query
    q = run(risk="aggressive", assets="xyz_equities", belief="single_market")
    ck("aggressive+stocks+single_market -> bobcat near top",
       "bobcat" in ids(q)[:5], ids(q)[:5])


if __name__ == "__main__":
    stress_sweep()
    correctness()
    print(f"\n{_P} passed, {_F} failed")
    sys.exit(1 if _F else 0)
