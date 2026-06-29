#!/usr/bin/env python3
"""Unit tests for discover.py — the CONCRETE filter (filter + return all). Run: python3 tests/test_discover.py

New contract (see docs/strategy-discover/discovery-architecture.md): the script filters ONLY on the
explicit concrete constraints (asset domain, named asset, strict-opposite direction, exclusions) and
returns ALL survivors, neutral-ordered (asset-match desc, name). No relevance score, no top-N, no
risk/belief/horizon flags — the LLM ranks the returned set on those soft dimensions itself.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

CATALOG = discover.load_catalog(os.path.join(HERE, "fixtures", "catalog_fixture.json"))
N_CATALOG = len(CATALOG)  # 11

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}  {detail}")


def intent(**kw):
    # Only the concrete flags exist now.
    args = SimpleNamespace(**{k: None for k in ("assets", "direction", "budget", "exclude")})
    for k, v in kw.items():
        setattr(args, k, v)
    return discover.normalize_intent(args)


def ids(result):
    return [c["id"] for c in result["candidates"]]


def run(**kw):
    return discover.match(intent(**kw), CATALOG)


# ---- normalizer (concrete inputs only) ----
def test_normalizer():
    i = intent(direction="no shorting", budget="around $300", assets="btc,eth,SOL", exclude="copy_trading")
    check("direction 'no shorting'->long_only", i["direction"] == "long_only", i["direction"])
    check("budget around $300->300", i["budget"] == 300, i["budget"])
    check("assets dedup btc/eth + named SOL",
          i["assets"] == [("class", "btc_eth"), ("named", "SOL")], i["assets"])
    check("exclude copy_trading", ("archetype", "copy_trading") in i["exclude"], i["exclude"])

    check("budget 300k->300000", intent(budget="300k")["budget"] == 300000)
    check("budget range 500-2000->1250", intent(budget="500-2000")["budget"] == 1250)
    check("budget 'lots'->None", intent(budget="lots")["budget"] is None)
    check("assets 'stocks'->xyz_equities", intent(assets="stocks")["assets"] == [("class", "xyz_equities")])
    check("assets 'NVDA'->named", intent(assets="NVDA")["assets"] == [("named", "NVDA")])
    check("direction 'both'->None (any)", intent(direction="both")["direction"] is None)
    bad = intent(direction="sideways")
    check("unknown direction dropped + warned",
          bad["direction"] is None and any("sideways" in w for w in bad["_warnings"]))


# ---- return ALL (no top-N) ----
def test_return_all():
    r = run()
    check("empty intent returns ALL", r["meta"]["eligible_count"] == N_CATALOG, r["meta"]["eligible_count"])
    check("empty intent returned_n == eligible", r["meta"]["returned_n"] == N_CATALOG)
    check("no relevance field on candidates", all("relevance" not in c for c in r["candidates"]))
    check("no match_reasons field on candidates", all("match_reasons" not in c for c in r["candidates"]))
    # limit is a SAFETY cap only, never the default
    rl = discover.match(intent(), CATALOG, limit=3)
    check("limit caps returned but reports full eligible",
          rl["meta"]["returned_n"] == 3 and rl["meta"]["eligible_count"] == N_CATALOG)


# ---- soft surface present for the LLM ----
def test_soft_surface():
    c = next(x for x in run()["candidates"] if x["id"] == "spider")
    for f in ("thesis", "tags", "risk_level", "belief_plain", "archetype_label", "time_horizon",
              "asset_scope", "direction", "asset_classes", "tier", "suggested_budget", "caveats",
              "market_facts", "id", "version", "name", "emoji", "tagline"):
        check(f"candidate carries '{f}'", f in c, list(c.keys()))
    check("spider thesis non-empty", bool(c["thesis"]))
    check("spider tags include hedge-fund", "hedge-fund" in c["tags"], c["tags"])


# ---- hard rejects (the only narrowing) ----
def test_asset_class_crossdomain():
    r = run(assets="btc_eth")
    check("crypto user: bobcat(xyz) rejected", "bobcat" not in ids(r), ids(r))
    check("crypto user: dire(xyz) rejected", "dire" not in ids(r))
    check("crypto user: lemur(pre_ipo) rejected", "lemur" not in ids(r))
    check("crypto user: beaver(btc) kept", "beaver" in ids(r))
    check("crypto user: kodiak(major_alts) kept (same domain)", "kodiak" in ids(r))
    check("crypto user: albatross(none) kept", "albatross" in ids(r))
    check("crypto user: spider(mixed) kept", "spider" in ids(r))

    r2 = run(assets="xyz_equities")
    check("xyz user: bobcat kept", "bobcat" in ids(r2))
    check("xyz user: dire(commodities, xyz domain) kept", "dire" in ids(r2))
    check("xyz user: beaver(crypto) rejected", "beaver" not in ids(r2), ids(r2))
    check("xyz user: spider(mixed) kept", "spider" in ids(r2))


def test_named_asset():
    r = run(assets="SOL")
    check("named SOL: kodiak kept", "kodiak" in ids(r))
    check("named SOL: hedgehog kept (basket incl SOL)", "hedgehog" in ids(r))
    check("named SOL: tortoise kept", "tortoise" in ids(r))
    check("named SOL: beaver(BTC only) rejected", "beaver" not in ids(r), ids(r))
    check("named SOL: bobcat rejected", "bobcat" not in ids(r))

    r2 = run(assets="NVDA")
    check("named NVDA: bobcat kept", "bobcat" in ids(r2))
    check("named NVDA: spider kept (trades xyz:NVDA)", "spider" in ids(r2))
    check("named NVDA: beaver rejected", "beaver" not in ids(r2))


def test_direction():
    r = run(direction="long_only")
    by_id = {x["id"]: x for x in CATALOG}
    check("long_only: tortoise kept", "tortoise" in ids(r))
    check("long_only: no short_only strategies surfaced",
          all(by_id[cid]["direction"] != "short_only" for cid in ids(r)))
    # a long_short strategy under a long_only ask carries the honest short caveat
    beav = next(c for c in r["candidates"] if c["id"] == "beaver")
    check("beaver long_short short-caveat", any("short" in cv.lower() for cv in beav["caveats"]), beav["caveats"])
    # tortoise IS long_only -> no short caveat
    tort = next(c for c in r["candidates"] if c["id"] == "tortoise")
    check("tortoise no short caveat", not any("short" in cv.lower() for cv in tort["caveats"]))


def test_exclude():
    check("exclude copy: albatross gone", "albatross" not in ids(run(exclude="copy_trading")))
    r = run(exclude="stocks")
    check("exclude stocks: bobcat gone", "bobcat" not in ids(r))
    check("exclude stocks: spider gone (partial xyz)", "spider" not in ids(r), ids(r))
    check("exclude stocks: beaver kept", "beaver" in ids(r))
    check("exclude shorting: long_short gone, long_only kept",
          "beaver" not in ids(run(exclude="shorting")) and "tortoise" in ids(run(exclude="shorting")))
    # 'stocks not crypto' = --assets xyz_equities --exclude crypto -> drops the mixed spider, keeps pure bobcat
    rc = run(assets="xyz_equities", exclude="crypto")
    check("stocks-not-crypto: bobcat kept", "bobcat" in ids(rc), ids(rc))
    check("stocks-not-crypto: mixed spider dropped", "spider" not in ids(rc), ids(rc))


# ---- neutral ordering (asset-match desc, then name) ----
def test_ordering():
    r = run(assets="btc_eth")
    cands = r["candidates"]
    by_id = {x["id"]: x for x in CATALOG}

    def matched(cid):
        return bool({"btc_eth"} & set(by_id[cid]["asset_classes"]))
    # all asset-matched come before all non-matched
    flags = [matched(c["id"]) for c in cands]
    first_false = flags.index(False) if False in flags else len(flags)
    check("asset-matched ordered before non-matched",
          all(flags[:first_false]) and not any(flags[first_false:]), flags)
    # ties broken by name
    matched_names = [c["name"] for c in cands[:first_false]]
    check("asset-matched group sorted by name", matched_names == sorted(matched_names), matched_names)


def test_caveats():
    # within-crypto specificity: btc_eth user, a broader-crypto strategy flags it trades alts
    rk = run(assets="btc_eth")
    kod = next((c for c in rk["candidates"] if c["id"] == "kodiak"), None)
    check("btc_eth user: kodiak gets alts caveat",
          kod and any("alts" in c.lower() for c in kod["caveats"]), kod["caveats"] if kod else None)
    # multi-instance funding split + caveat surfaced when the user stated assets
    rs = run(assets="btc_eth")
    sp = next((c for c in rs["candidates"] if c["id"] == "spider"), None)
    check("spider funding_split present", sp and sp.get("funding_split") == [0.6, 0.4], sp.get("funding_split") if sp else None)
    check("spider multi-leg caveat", sp and any("wallet" in c.lower() for c in sp["caveats"]))
    check("spider suggested_budget>=200", sp and sp["suggested_budget"] >= 200)
    # below-floor budget caveat
    rb = run(assets="btc_eth", budget="50")
    sp2 = next((c for c in rb["candidates"] if c["id"] == "spider"), None)
    check("below-floor budget caveat", sp2 and any("needs" in c.lower() for c in sp2["caveats"]), sp2["caveats"] if sp2 else None)


def test_degrade():
    # named asset nobody trades -> broaden to class
    r = run(assets="DOGE")
    check("DOGE broadened (widened named_asset)", "named_asset" in r["meta"].get("widened", []), r["meta"])
    check("DOGE broaden -> alt strategies survive", "kodiak" in ids(r), ids(r))

    # genuinely impossible (crypto user, exclude crypto AND copy) -> build-custom only
    r2 = run(assets="btc_eth", exclude="crypto,copy_trading")
    check("impossible -> no candidates", r2["candidates"] == [], ids(r2))
    check("impossible -> build_custom present", r2["build_custom"]["route"] == "senpi-strategy-author")
    check("impossible -> unmet non-empty", len(r2["meta"]["unmet"]) > 0, r2["meta"]["unmet"])

    # 'no stocks' only excludes equities, NOT commodities/pre-IPO/copy
    r3 = run(assets="xyz_equities", exclude="stocks")
    check("'no stocks' keeps commodities (dire)", "dire" in ids(r3), ids(r3))
    check("'no stocks' drops equities (bobcat, spider)", "bobcat" not in ids(r3) and "spider" not in ids(r3))


def test_failopen():
    # unparseable concrete values drop to unstated (widen) -> still returns everything
    r = run(direction="vibes", budget="lots")
    check("garbage concrete intent still returns all", r["meta"]["returned_n"] == N_CATALOG, r["meta"]["returned_n"])
    check("garbage intent logged warnings", len(r["meta"]["warnings"]) >= 1, r["meta"]["warnings"])


def test_intent_echo():
    r = run(assets="btc_eth,SOL", budget="$500", direction="long")
    e = r["meta"]["intent_echo"]
    check("echo assets", e.get("assets") == ["btc_eth", "SOL"], e)
    check("echo budget", e.get("budget") == 500, e)
    check("echo direction", e.get("direction") == "long_only", e)
    check("echo has no soft keys", not any(k in e for k in ("risk", "belief", "horizon")), e)


def test_infer_class():
    # the named-asset broaden fallback must not force a non-crypto ticker into the crypto bucket
    check("infer BTC -> btc_eth", discover.infer_class_for_named("BTC") == "btc_eth")
    check("infer SOL -> major_alts", discover.infer_class_for_named("SOL") == "major_alts")
    check("infer NVDA -> None (not mis-bucketed as crypto)", discover.infer_class_for_named("NVDA") is None)
    check("infer xyz:GOLD -> None (xyz, sub-class unknown)", discover.infer_class_for_named("xyz:GOLD") is None)


if __name__ == "__main__":
    for fn in [test_normalizer, test_return_all, test_soft_surface, test_asset_class_crossdomain,
               test_named_asset, test_direction, test_exclude, test_ordering, test_caveats,
               test_degrade, test_failopen, test_intent_echo, test_infer_class]:
        try:
            fn()
        except Exception as e:  # noqa
            _FAIL += 1
            print(f"  ERROR in {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
