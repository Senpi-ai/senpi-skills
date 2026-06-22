#!/usr/bin/env python3
"""CLI integration tests: run discover.py as a subprocess, assert exit 0 + valid JSON.
Run: python3 tests/test_cli.py

New contract: concrete flags only (--assets / --direction / --exclude / --budget / --limit). The script
returns ALL survivors; the LLM ranks them. No --risk/--belief/--horizon/--offset.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "discover.py")
FIXTURE = os.path.join(HERE, "fixtures", "catalog_fixture.json")
_P = _F = 0


def ck(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
    else:
        _F += 1
        print(f"  FAIL: {name}  {detail}")


def run(flags, live=False):
    env = dict(os.environ)
    env.pop("SENPI_AUTH_TOKEN", None)
    args = [sys.executable, SCRIPT, "--catalog", FIXTURE] + flags
    if not live:
        args.append("--no-market")
    p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=90)
    return p.returncode, p.stdout, p.stderr


def case(name, flags, expect_top=None, live=False):
    rc, out, err = run(flags, live=live)
    ck(f"{name}: exit 0", rc == 0, f"rc={rc} err={err[:200]}")
    try:
        r = json.loads(out)
    except Exception as e:  # noqa
        ck(f"{name}: valid JSON", False, f"{e}: {out[:120]}")
        return None
    ck(f"{name}: valid JSON", True)
    ck(f"{name}: has build_custom", r.get("build_custom", {}).get("route") == "senpi-strategy-author")
    ck(f"{name}: candidates is list", isinstance(r.get("candidates"), list))
    ck(f"{name}: no relevance leaked", all("relevance" not in c for c in r.get("candidates", [])))
    if expect_top is not None:
        got = r["candidates"][0]["id"] if r["candidates"] else None
        ck(f"{name}: top (by asset-match order) == {expect_top}", got == expect_top, f"got {got}")
    return r


if __name__ == "__main__":
    r = case("concrete-btc", ["--assets", "btc_eth", "--budget", "$300"])
    ck("concrete-btc: returns all btc-domain (no cut)", r and r["meta"]["returned_n"] == r["meta"]["eligible_count"])
    ck("concrete-btc: candidates carry thesis+tags",
       r and all(c.get("thesis") is not None and "tags" in c for c in r["candidates"]))

    case("exclude-copy", ["--exclude", "copy_trading"])
    case("stocks-not-crypto", ["--assets", "xyz_equities", "--exclude", "crypto"], expect_top="bobcat")
    re = case("empty", [])
    ck("empty: returns full fixture (11)", re and re["meta"]["eligible_count"] == 11)
    case("loose-nl-direction", ["--direction", "no shorting", "--assets", "btc"])
    ri = case("impossible", ["--assets", "btc_eth", "--exclude", "crypto,copy_trading"])
    ck("impossible: empty + unmet", ri and ri["candidates"] == [] and ri["meta"].get("unmet"))
    rl = case("limit-cap", ["--limit", "3"])
    ck("limit-cap: returned_n==3, eligible==11", rl and rl["meta"]["returned_n"] == 3 and rl["meta"]["eligible_count"] == 11)
    case("named-degrade", ["--assets", "DOGE"])

    # live path (network): exit 0 + valid JSON + a REAL market read (not all-null) even unauthenticated
    r = case("live", ["--assets", "btc_eth", "--limit", "2"], live=True)
    if r is not None:
        ck("live: market_facts present", all("market_facts" in c for c in r["candidates"]))
        ck("live: user_context returned", "user_context" in r["meta"])
        signals = [f for c in r["candidates"] for f in c["market_facts"]
                   if f.get("price_change_24h_pct") is not None or f.get("trend")]
        ck("live: market read carries real signal (not all-null)", len(signals) > 0,
           f"facts={[c['market_facts'] for c in r['candidates']]}")

    print(f"\n{_P} passed, {_F} failed")
    sys.exit(1 if _F else 0)
