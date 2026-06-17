#!/usr/bin/env python3
"""Unit tests for discover.py (matcher + normalizer). Run: python3 tests/test_discover.py"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

CATALOG = discover.load_catalog(os.path.join(HERE, "fixtures", "catalog_fixture.json"))

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
    args = SimpleNamespace(**{k: None for k in (
        "risk", "assets", "belief", "horizon", "direction", "market_scope",
        "goal", "budget", "exclude", "experience")})
    for k, v in kw.items():
        setattr(args, k, v)
    return discover.normalize_intent(args)


def ids(result):
    return [c["id"] for c in result["candidates"]]


def run(**kw):
    return discover.match(intent(**kw), CATALOG, limit=20)


# ---- normalizer ----
def test_normalizer():
    i = intent(risk="safe", belief="ride", horizon="quick", direction="no shorting",
               budget="around $300", assets="btc,eth,SOL", exclude="copy_trading")
    check("risk safe->conservative", i["risk"] == "conservative", i["risk"])
    check("belief ride->trend", i["belief"] == "trend", i["belief"])
    check("horizon quick->scalp", i["horizon"] == "scalp", i["horizon"])
    check("direction 'no shorting'->long_only", i["direction"] == "long_only", i["direction"])
    check("budget around $300->300", i["budget"] == 300, i["budget"])
    check("assets dedup btc/eth + named SOL",
          i["assets"] == [("class", "btc_eth"), ("named", "SOL")], i["assets"])
    check("exclude copy_trading", ("archetype", "copy_trading") in i["exclude"], i["exclude"])

    check("risk medium->moderate", intent(risk="medium")["risk"] == "moderate")
    check("risk yolo->aggressive", intent(risk="yolo")["risk"] == "aggressive")
    check("budget 300k->300000", intent(budget="300k")["budget"] == 300000)
    check("budget range 500-2000->1250", intent(budget="500-2000")["budget"] == 1250)
    check("budget 'lots'->None", intent(budget="lots")["budget"] is None)
    check("assets 'stocks'->xyz_equities", intent(assets="stocks")["assets"] == [("class", "xyz_equities")])
    check("assets 'NVDA'->named", intent(assets="NVDA")["assets"] == [("named", "NVDA")])
    bad = intent(risk="purple")
    check("unknown risk dropped + warned", bad["risk"] is None and any("purple" in w for w in bad["_warnings"]))


# ---- hard rejects ----
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
    # tortoise (long_only) should have a direction reason; a long_short one carries the short caveat
    tort = next(c for c in r["candidates"] if c["id"] == "tortoise")
    check("tortoise direction +1 reason", any(rr["dim"] == "direction" for rr in tort["match_reasons"]))
    beav = next(c for c in r["candidates"] if c["id"] == "beaver")
    check("beaver long_short short-caveat", any("short" in cv.lower() for cv in beav["caveats"]), beav["caveats"])


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


# ---- relevance ----
def test_relevance():
    r = run(belief="copy")
    alb = next(c for c in r["candidates"] if c["id"] == "albatross")
    check("copy belief: albatross +1", alb["relevance"] == 1, alb["relevance"])

    r2 = run(belief="trend")
    egret = next(c for c in r2["candidates"] if c["id"] == "egret")
    check("trend belief: egret(contrarian) opposite -1", egret["relevance"] == -1, egret["relevance"])
    check("trend belief: egret still present (not rejected)", "egret" in ids(r2))
    beav = next(c for c in r2["candidates"] if c["id"] == "beaver")
    check("trend belief: beaver(trend) +1", beav["relevance"] == 1)

    r3 = run(risk="conservative")
    tort = next(c for c in r3["candidates"] if c["id"] == "tortoise")
    check("conservative: tortoise exact +1, no caveat", tort["relevance"] == 1 and not any("notch" in c for c in tort["caveats"]))
    beav3 = next(c for c in r3["candidates"] if c["id"] == "beaver")
    check("conservative: beaver(moderate) adjacent +1 + caveat",
          beav3["relevance"] == 1 and any("notch" in c for c in beav3["caveats"]), beav3["caveats"])

    # within-crypto specificity caveat
    rk = run(assets="btc_eth")
    kod = next((c for c in rk["candidates"] if c["id"] == "kodiak"), None)
    check("btc_eth user: kodiak gets alts caveat", kod and any("alts" in c.lower() for c in kod["caveats"]), kod["caveats"] if kod else None)


def test_ranking_and_topn():
    r = discover.match(intent(), CATALOG, limit=8)
    check("empty intent eligible_count=11", r["meta"]["eligible_count"] == 11, r["meta"]["eligible_count"])
    check("empty intent returned_n=8 (top-N)", r["meta"]["returned_n"] == 8, r["meta"]["returned_n"])
    check("empty intent: starters before advanced",
          all(c["tier"] == "starter" for c in r["candidates"][:6]), [c["tier"] for c in r["candidates"]])
    # paging
    p2 = discover.match(intent(), CATALOG, limit=8, offset=8)
    check("page 2 returns the remaining 3", p2["meta"]["returned_n"] == 3, p2["meta"]["returned_n"])
    check("no overlap between pages", not (set(ids(r)) & set(ids(p2))))

    # multi-instance funding split surfaced
    rs = run(risk="aggressive", assets="btc_eth")
    sp = next((c for c in rs["candidates"] if c["id"] == "spider"), None)
    check("spider funding_split present", sp and sp.get("funding_split") == [0.6, 0.4], sp.get("funding_split") if sp else None)
    check("spider multi-leg caveat", sp and any("wallet" in c.lower() for c in sp["caveats"]))
    check("spider suggested_budget>=200", sp and sp["suggested_budget"] >= 200)


def test_degrade():
    # named asset nobody trades -> broaden to class
    r = run(assets="DOGE")
    check("DOGE broadened (widened named_asset)", "named_asset" in r["meta"]["widened"], r["meta"]["widened"])
    check("DOGE broaden -> alt strategies survive", "kodiak" in ids(r), ids(r))

    # genuinely impossible (crypto user, exclude crypto AND copy) -> build-custom only
    r2 = run(assets="btc_eth", exclude="crypto,copy_trading")
    check("impossible -> no candidates", r2["candidates"] == [], ids(r2))
    check("impossible -> build_custom present", r2["build_custom"]["route"] == "senpi-strategy-author")
    check("impossible -> unmet non-empty", len(r2["meta"]["unmet"]) > 0, r2["meta"]["unmet"])

    # 'no stocks' only excludes equities, NOT commodities/pre-IPO/copy (regression for the above)
    r3 = run(assets="xyz_equities", exclude="stocks")
    check("'no stocks' keeps commodities (dire)", "dire" in ids(r3), ids(r3))
    check("'no stocks' drops equities (bobcat, spider)", "bobcat" not in ids(r3) and "spider" not in ids(r3))


def test_failopen():
    r = run(risk="purple", belief="vibes", horizon="whenever")
    check("garbage intent still returns candidates", r["meta"]["returned_n"] > 0, r["meta"]["returned_n"])
    check("garbage intent logged warnings", len(r["meta"]["warnings"]) >= 3, r["meta"]["warnings"])


def test_intent_echo():
    r = run(risk="safe", assets="btc_eth,SOL", budget="$500")
    e = r["meta"]["intent_echo"]
    check("echo risk", e.get("risk") == "conservative", e)
    check("echo assets", e.get("assets") == ["btc_eth", "SOL"], e)
    check("echo budget", e.get("budget") == 500, e)


if __name__ == "__main__":
    for fn in [test_normalizer, test_asset_class_crossdomain, test_named_asset, test_direction,
               test_exclude, test_relevance, test_ranking_and_topn, test_degrade, test_failopen,
               test_intent_echo]:
        try:
            fn()
        except Exception as e:  # noqa
            _FAIL += 1
            print(f"  ERROR in {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
