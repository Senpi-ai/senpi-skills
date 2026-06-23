#!/usr/bin/env python3
"""Offline engine test — runs portfolio.run() against a recorded MCP fixture (no network).

The fixture reproduces the canonical bug scenario: embedded wallet holds ~$0 idle, all funds are in
strategies, and `total_withdrawable` is large. The test guards that the engine reports those as
SEPARATE buckets and never collapses strategy-margin into "embedded idle."

    python3 -m pytest senpi-portfolio/tests/   # or: python3 tests/test_portfolio.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import portfolio  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "portfolio_fixture.json")


def _result():
    with open(FIXTURE) as f:
        client = portfolio._FixtureClient(json.load(f))
    return portfolio.run(client, want_market=True)


def test_embedded_idle_is_not_total_withdrawable():
    """THE bug guard: embedded idle is the $1.51 EVM USDC, NOT the $2,301 total_withdrawable."""
    t = _result()["totals"]
    assert t["idle_in_embedded"] == 1.51                 # only the EVM USDC; HL embedded is $0
    assert t["idle_in_strategies"] > 2000                # this is where total_withdrawable lives
    assert t["idle_in_embedded"] != t["idle_in_strategies"]


def test_three_buckets_sum_to_total():
    t = _result()["totals"]
    s = t["idle_in_embedded"] + t["idle_in_strategies"] + t["deployed_in_positions"]
    assert abs(s - t["grand_total_usd"]) <= 2.0          # the three buckets reconcile to the total


def test_embedded_address_resolved():
    e = _result()["embedded_wallet"]
    assert e["address"] == "0xembed00000000000000000000000000000000ed"
    assert e["idle_hl_usdc"] == 0                         # all funds moved into strategies


def test_short_book_positions_and_market_alignment():
    """cub-short holds 3 shorts, all working WITH today's selloff."""
    strat = {s["name"]: s for s in _result()["strategies"]}
    short = strat["cub-short"]
    assert len(short["positions"]) == 3
    assert all(p["direction"] == "short" for p in short["positions"])
    eth = next(p for p in short["positions"] if p["asset"] == "ETH")
    assert eth["market_24h_pct"] < 0 and eth["vs_market"] == "with the move"
    assert eth["return_on_equity_pct"] == 11.9           # leveraged return, not raw price %


def test_flat_strategies_are_all_idle():
    strat = {s["name"]: s for s in _result()["strategies"]}
    lng = strat["cub-long"]
    assert lng["positions"] == [] and lng["deployed"] == 0
    assert lng["idle_withdrawable"] > 1500               # 100% free margin


def test_exposure_net_short():
    exp = _result()["exposure"]
    assert exp["net_bias"] == "short"                    # every open position is a short
    assert exp["gross_long_usd"] == 0


def test_fails_open_on_empty():
    res = portfolio.run(portfolio._FixtureClient({}), want_market=True)
    assert "totals" in res and res["meta"].get("degraded")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
