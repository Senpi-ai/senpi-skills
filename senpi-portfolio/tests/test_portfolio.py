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
import _yaml  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "portfolio_fixture.json")
REGISTRY_DIR = os.path.join(HERE, "fixtures", "registry")           # holds installed_runtimes.json
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))                  # senpi-skills/
KODIAK_YAML = os.path.join(REPO_ROOT, "strategies", "kodiak", "main", "runtime.yaml")
# the wallet the registry fixture keys the kodiak runtime.yaml under (see fixtures/registry/…json)
KODIAK_WALLET = "0xKODIAK00000000000000000000000000000kdk"


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


def test_shared_dex_collateral_not_double_counted():
    """REGRESSION: `withdrawable` is shared/mirrored across the main+xyz views — count it ONCE.
    cub-short raw: main.av 1149.42 / xyz.av 970.67 / shared withdrawable 740.32.
    Correct wallet value = 1149.42 + 970.67 − 740.32 = 1379.77 (NOT 2120.09 summed, NOT 1149.42 max)."""
    strat = {s["name"]: s for s in _result()["strategies"]}
    short = strat["cub-short"]
    assert short["idle_withdrawable"] == 740.32          # shared idle, counted once (not 1480.64)
    assert short["account_value"] == 1379.77             # main.av + xyz.av − shared idle
    assert short["deployed"] == 639.45                   # position equity across BOTH dexes (409.10 + 230.35)
    # and the grand total reflects it — ~$3,103, not the double-counted ~$5,560
    t = _result()["totals"]
    assert 3050 <= t["grand_total_usd"] <= 3150


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


def test_yaml_parses_kodiak_description():
    """The vendored parser reads the runtime.yaml folded `description` block — non-empty, real text."""
    with open(KODIAK_YAML) as f:
        doc = _yaml.loads(f.read())
    assert isinstance(doc, dict)
    desc = doc.get("description")
    assert isinstance(desc, str) and desc.strip()
    assert "KODIAK" in desc            # sanity — it's the kodiak thesis, not an empty capture


def test_profile_description_from_runtime_registry():
    """UNIVERSAL mandate: with SENPI_STATE_DIR pointed at a registry holding the kodiak runtime.yaml
    (keyed by wallet), the engine attaches `profile.description` for that wallet — read from the
    DEPLOYED runtime.yaml the runtime registers, NOT from the catalog."""
    # a minimal MCP fixture: one ACTIVE strategy whose wallet matches the registry entry
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 200, "total_withdrawable": 200,
                                  "total_usdc_in_hyperliquid": 0, "token_balances": []},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "kodiak", "strategyWalletAddress": KODIAK_WALLET, "status": "ACTIVE"}]},
        f"strategy_get_clearinghouse_state::{KODIAK_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "200"}, "withdrawable": "200", "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": "200"}, "withdrawable": "200", "assetPositions": []}},
    }
    old = os.environ.get("SENPI_STATE_DIR")
    os.environ["SENPI_STATE_DIR"] = REGISTRY_DIR
    try:
        res = portfolio.run(portfolio._FixtureClient(fixture), want_market=False)
    finally:
        if old is None:
            os.environ.pop("SENPI_STATE_DIR", None)
        else:
            os.environ["SENPI_STATE_DIR"] = old
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    prof = strat["profile"]
    assert prof is not None
    assert isinstance(prof["description"], str) and "KODIAK" in prof["description"]
    assert prof["source"] in ("registry", "registry+catalog")
    assert strat["protected"] is True                      # runtime.yaml ships an `exit:` block
    assert res["meta"]["registry_source"] == "registry"
    assert res["meta"]["profile_source"] in ("registry", "mixed")


def test_embedded_idle_reads_nested_total_in_hyperliquid():
    """Regression (the invisible-$10k bug): account_get_portfolio nests balances under a `portfolio`
    key and the idle-HL field is `total_in_hyperliquid` — NOT `total_usdc_in_hyperliquid`. The old code
    missed both, so embedded idle always read $0 and a large infusion was invisible. This fixture
    (nested + correct field, $10,446 idle, no strategies) must surface as idle-in-embedded; it reads $0
    under the pre-fix code."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 10446.0, "total_allocated_in_strategy": 0, "total_withdrawable": 0,
            "total_in_hyperliquid": 10446.0, "total_spot_usd_in_hyperliquid": 0, "token_balances": []}},
        "strategy_list": {"strategies": []},
    }
    res = portfolio.run(portfolio._FixtureClient(fixture), want_market=False)
    assert res["embedded_wallet"]["idle_hl_usdc"] == 10446.0
    assert res["totals"]["idle_in_embedded"] == 10446.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
