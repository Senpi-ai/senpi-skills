#!/usr/bin/env python3
"""CLI integration tests: run discover.py as a subprocess, assert exit 0 + valid JSON.
Run: python3 tests/test_cli.py"""
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
        return
    ck(f"{name}: valid JSON", True)
    ck(f"{name}: has build_custom", r.get("build_custom", {}).get("route") == "senpi-strategy-author")
    ck(f"{name}: candidates is list", isinstance(r.get("candidates"), list))
    if expect_top is not None:
        got = r["candidates"][0]["id"] if r["candidates"] else None
        ck(f"{name}: top == {expect_top}", got == expect_top, f"got {got}")
    return r


if __name__ == "__main__":
    case("safe-btc", ["--risk", "safe", "--assets", "btc_eth", "--budget", "$300"])
    case("copy", ["--belief", "copy", "--risk", "moderate"], expect_top="albatross")
    case("stocks-not-crypto", ["--assets", "xyz_equities", "--exclude", "crypto"], expect_top="bobcat")
    case("empty", [])
    case("loose-nl", ["--risk", "pretty cautious", "--assets", "btc and eth"])
    case("impossible", ["--assets", "btc_eth", "--exclude", "crypto,copy_trading"])
    case("paging", ["--offset", "8"])
    case("named-degrade", ["--assets", "DOGE"])

    # live path (network): must still exit 0 and emit valid JSON even unauthenticated
    r = case("live-degrade", ["--risk", "moderate", "--assets", "btc_eth", "--limit", "2"], live=True)
    if r is not None:
        ck("live-degrade: market_facts present (structure)",
           all("market_facts" in c for c in r["candidates"]))
        ck("live-degrade: user_context returned",
           "user_context" in r["meta"])

    print(f"\n{_P} passed, {_F} failed")
    sys.exit(1 if _F else 0)
