#!/usr/bin/env python3
"""Offline engine test — runs smartmoney.run() against a recorded MCP fixture (no network).

    python3 -m pytest senpi-smart-money/tests/   # or: python3 tests/test_smartmoney.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import smartmoney  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "smartmoney_fixture.json")


def _result():
    with open(FIXTURE) as f:
        client = smartmoney._FixtureClient(json.load(f))
    return smartmoney.run(client, want_near=True)


def test_cohorts_built():
    c = _result()["cohorts"]
    assert c["smart"]["members_sampled"] >= 2   # 0xsmart1/2 land in the >=$1M band
    assert c["crowd"]["members_sampled"] >= 2   # 0xcrowd1/2 land in the $10-100k band


def test_smart_leaning_headline():
    """The proven cohort: short HYPE, long BTC — both above the lean threshold with enough members."""
    leaning = {x["asset"]: x for x in _result()["smart_leaning"]}
    assert leaning["HYPE"]["direction"] == "short" and leaning["HYPE"]["members"] >= 5
    assert leaning["BTC"]["direction"] == "long" and leaning["BTC"]["members"] >= 5


def test_divergence_opposite_sides():
    """The core signal: smart short HYPE while the crowd is long it — flagged opposite_sides."""
    div = {x["asset"]: x for x in _result()["divergences"]}
    assert "HYPE" in div, "HYPE divergence not detected"
    h = div["HYPE"]
    assert h["opposite_sides"] is True
    assert h["smart_direction"] == "short" and h["crowd_direction"] == "long"
    assert h["smart_members"] >= 5 and h["crowd_members"] >= 5


def test_near_term_present_when_healthy():
    res = _result()
    assert res["meta"]["near_term_available"] is True
    assert res["near_term"]["concentration"]["concentration"][0]["asset"] == "HYPE"


def test_cohorts_unavailable_flag_on_empty():
    """No discovery data (e.g. app-scoped token) → flagged honestly, no exception."""
    res = smartmoney.run(smartmoney._FixtureClient({}), want_near=True)
    assert res["meta"].get("cohorts_unavailable")
    assert res["smart_leaning"] == [] and res["divergences"] == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
