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
REGISTRY_COUGAR_DIR = os.path.join(HERE, "fixtures", "registry_cougar")   # 2-instance strategy fixture
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))                  # senpi-skills/
KODIAK_YAML = os.path.join(REPO_ROOT, "strategies", "kodiak", "main", "runtime.yaml")
# the wallet the registry fixture keys the kodiak runtime.yaml under (see fixtures/registry/…json)
KODIAK_WALLET = "0xKODIAK00000000000000000000000000000kdk"
# the two wallets of the cougar long/short pair (one strategy, two instances — see registry_cougar/…json)
COUGAR_LONG_WALLET = "0xCOUGARLONG000000000000000000000000000lng"
COUGAR_SHORT_WALLET = "0xCOUGARSHORT00000000000000000000000000sht"


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


def test_multi_wallet_strategy_groups_into_one():
    """A STRATEGY IS ALL ITS WALLETS. cougar deploys as TWO instances on TWO wallets (cougar-long +
    cougar-short, sharing `group: cougar` in their runtime.yamls). `strategy_list` returns them as two
    separate rows; the engine must re-unite them into ONE `strategy_groups[]` entry with
    `is_multi_wallet: true` and 2 instances — never present the two sleeves as two strategies."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 2000, "total_withdrawable": 1200,
            "total_in_hyperliquid": 0, "token_balances": []}},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "cougar-long", "strategyWalletAddress": COUGAR_LONG_WALLET, "status": "ACTIVE"},
            {"tradingStrategyName": "cougar-short", "strategyWalletAddress": COUGAR_SHORT_WALLET, "status": "ACTIVE"}]},
        # long sleeve: flat, all idle (its other-sleeve-waiting-for-signal case)
        f"strategy_get_clearinghouse_state::{COUGAR_LONG_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "1000"}, "withdrawable": "1000", "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": "1000"}, "withdrawable": "1000", "assetPositions": []}},
        # short sleeve: one working short
        f"strategy_get_clearinghouse_state::{COUGAR_SHORT_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "1000"}, "withdrawable": "800", "assetPositions": [
                {"position": {"coin": "ETH", "szi": -0.5, "positionValue": 800, "marginUsed": 200,
                              "entryPx": 1719.7, "unrealizedPnl": 20, "returnOnEquity": 0.1,
                              "leverage": {"value": 4}, "liquidationPx": 2100}}]},
            "xyz": {"marginSummary": {"accountValue": "800"}, "withdrawable": "800", "assetPositions": []}},
    }
    old = os.environ.get("SENPI_STATE_DIR")
    os.environ["SENPI_STATE_DIR"] = REGISTRY_COUGAR_DIR
    try:
        res = portfolio.run(portfolio._FixtureClient(fixture), want_market=False)
    finally:
        if old is None:
            os.environ.pop("SENPI_STATE_DIR", None)
        else:
            os.environ["SENPI_STATE_DIR"] = old

    # still two per-wallet rows in strategies[] (bucket math relies on it) …
    assert len(res["strategies"]) == 2
    # … but ONE strategy_groups[] entry re-uniting them
    groups = res["strategy_groups"]
    assert len(groups) == 1
    g = groups[0]
    assert g["label"] == "cougar"
    assert g["is_multi_wallet"] is True
    assert len(g["instances"]) == 2
    names = {i["name"] for i in g["instances"]}
    assert names == {"cougar-long", "cougar-short"}
    # the flat long sleeve is its OTHER book waiting for a signal — surfaced, not "dead money"
    assert g["flat_instances"] == ["cougar-long"]
    # totals summed across BOTH wallets
    assert g["totals"]["account_value"] == 1000 + 1000        # long wallet + short wallet
    assert g["totals"]["deployed"] == 200                     # only the short sleeve has a position
    assert g["totals"]["upnl"] == 20
    # mandate shared across instances (from the deployed runtime.yaml description)
    assert isinstance(g["mandate"], str) and "market-neutral" in g["mandate"]
    # meta flag flips on
    assert res["meta"]["has_multi_wallet_strategy"] is True


def test_single_wallet_strategy_is_one_instance_group():
    """A single-instance strategy is its own group with is_multi_wallet: false and one instance —
    and with no multi-wallet strategy present, the meta flag stays False."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 200, "total_withdrawable": 200,
            "total_in_hyperliquid": 0, "token_balances": []}},
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
    groups = res["strategy_groups"]
    assert len(groups) == 1
    g = groups[0]
    assert g["label"] == "kodiak"                # profile.group from the deployed runtime.yaml
    assert g["is_multi_wallet"] is False
    assert len(g["instances"]) == 1
    assert res["meta"]["has_multi_wallet_strategy"] is False


def test_dsl_ladder_parses_from_runtime_yaml():
    """(1) HOW DSL works: the kodiak registry runtime.yaml has a real phase1+phase2 dsl_preset; the
    engine parses profile.dsl into a hard-stop floor + arm-at + the full tier ladder — the CONFIG side of
    the "protected from entry" story. (kodiak ships phase1.max_loss_pct 15 → -15, tiers[0].trigger 10.)"""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 200, "total_withdrawable": 200,
            "total_in_hyperliquid": 0, "token_balances": []}},
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
    dsl = {s["name"]: s for s in res["strategies"]}["kodiak"]["profile"]["dsl"]
    assert dsl is not None
    assert dsl["hard_stop_roe_pct"] == -15.0        # phase1.max_loss_pct 15 → the hard floor
    assert dsl["arm_at_roe_pct"] == 10              # tiers[0].trigger_pct — where the ratchet ARMS
    assert dsl["has_phase2"] is True
    assert len(dsl["tiers"]) == 5
    assert dsl["tiers"][1] == {"trigger_pct": 18, "lock_hw_pct": 40}   # tiers parse fully
    # the group surfaces the ladder once per strategy too
    assert res["strategy_groups"][0]["dsl"]["arm_at_roe_pct"] == 10


def test_dsl_ladder_parses_cougar_short_repo_yaml():
    """Ground-truth check on the actual deployed cougar/short/runtime.yaml in the repo: profile.dsl has
    hard_stop -14, arm-at 8, and the exact 5-tier ladder (the example from the task spec)."""
    path = os.path.join(REPO_ROOT, "strategies", "cougar", "short", "runtime.yaml")
    with open(path) as f:
        prof = portfolio._profile_from_runtime_yaml(f.read())
    dsl = prof["dsl"]
    assert dsl["hard_stop_roe_pct"] == -14.0
    assert dsl["arm_at_roe_pct"] == 8
    assert [t["trigger_pct"] for t in dsl["tiers"]] == [8, 18, 35, 60, 100]
    assert [t["lock_hw_pct"] for t in dsl["tiers"]] == [0, 40, 60, 78, 88]


def test_named_preset_dsl_is_reported_not_dropped():
    """A NAMED string preset ("conviction", from the cougar registry fixture) → profile.dsl records the
    preset name + a note, never None — the ladder just isn't inlined in the runtime.yaml."""
    with open(os.path.join(REGISTRY_COUGAR_DIR, "installed_runtimes.json")) as f:
        reg = json.load(f)
    text = reg["runtimes"][0]["runtimeYamlContent"]
    prof = portfolio._profile_from_runtime_yaml(text)
    assert prof["dsl"] == {"preset_name": "conviction", "note": "named preset — ladder not inlined"}


def test_live_position_dsl_armed_and_unarmed():
    """(2) WHICH open position is in WHICH tier — the core fix.

    Two open positions on a kodiak-style strategy:
      - SOL: a LIVE ratchet record at tier 2 → dsl.armed True, tier_index 2, locked = lock_hw_pct at
        tier 2 (from the parsed ladder = 40).
      - ETH: NO ratchet record (sub-Tier-1) → dsl.armed False, but framed as PROTECTED from entry with
        the arm-at note — NEVER a falsy/'none' that reads as unprotected.
    ratchet_stop_list is keyed by wallet in the fixture (the engine calls it with strategy_wallet_address)."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 500, "total_withdrawable": 100,
            "total_in_hyperliquid": 0, "token_balances": []}},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "kodiak", "id": "strat-kodiak-1",
             "strategyWalletAddress": KODIAK_WALLET, "status": "ACTIVE"}]},
        f"strategy_get_clearinghouse_state::{KODIAK_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "500"}, "withdrawable": "100", "assetPositions": [
                # SOL: deep in profit, has crossed Tier 1 → will get a live ratchet record
                {"position": {"coin": "SOL", "szi": 3.0, "positionValue": 600, "marginUsed": 120,
                              "entryPx": 150, "unrealizedPnl": 40, "returnOnEquity": 0.33,
                              "leverage": {"value": 5}, "liquidationPx": 90}},
                # ETH: only +6% ROE, sub-Tier-1 → NO ratchet record, must still read as protected
                {"position": {"coin": "ETH", "szi": 0.2, "positionValue": 400, "marginUsed": 100,
                              "entryPx": 1700, "unrealizedPnl": 6, "returnOnEquity": 0.06,
                              "leverage": {"value": 4}, "liquidationPx": 1300}}]},
            "xyz": {"marginSummary": {"accountValue": "100"}, "withdrawable": "100", "assetPositions": []}},
        # LIVE ratchet state — only SOL has crossed Tier 1 (tier 2 = 35% trigger). ETH is absent BY DESIGN.
        f"ratchet_stop_list::{KODIAK_WALLET.lower()}": {"configs": [
            {"asset": "SOL", "status": "ACTIVE", "currentTierIndex": 2, "highWaterRoe": 36.5}]},
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

    pos = {p["asset"]: p for p in {s["name"]: s for s in res["strategies"]}["kodiak"]["positions"]}

    # SOL — armed at the live tier, with the locked % pulled from the parsed ladder (tier 2 → 60 for kodiak)
    sol = pos["SOL"]["dsl"]
    assert sol["armed"] is True
    assert sol["tier_index"] == 2
    assert sol["high_water_roe"] == 36.5
    assert sol["status"] == "ACTIVE"
    assert sol["locked"] == 60          # lock_hw_pct at kodiak tier 2 (tiers = 20/40/60/75/88)

    # ETH — NO ratchet record, but NEVER unprotected: armed False + the arm-at framing, and the note
    # must read as PROTECTED, not as a gap.
    eth = pos["ETH"]["dsl"]
    assert eth["armed"] is False
    assert eth["hard_stop_roe_pct"] == -15.0    # phase1 floor still protecting from entry
    assert eth["arm_at_roe_pct"] == 10          # ratchet arms at Tier 1 (+10%)
    assert eth["roe"] == 6.0                    # this position is at +6% — below the arm point
    assert "protected from entry" in eth["note"]
    low = eth["note"].lower()
    assert "no dsl" not in low and "unprotected" not in low and "no monitoring" not in low


def test_live_position_dsl_fails_open_when_ratchet_call_absent():
    """If the ratchet_stop_list call returns nothing at all (no fixture entry → the engine's list read
    yields no records), an open position STILL gets a config-based dsl object (armed False + arm-at
    framing) that stands alone — never left looking unprotected."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 500, "total_withdrawable": 100,
            "total_in_hyperliquid": 0, "token_balances": []}},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "kodiak", "id": "strat-kodiak-1",
             "strategyWalletAddress": KODIAK_WALLET, "status": "ACTIVE"}]},
        f"strategy_get_clearinghouse_state::{KODIAK_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "500"}, "withdrawable": "100", "assetPositions": [
                {"position": {"coin": "SOL", "szi": 3.0, "positionValue": 600, "marginUsed": 120,
                              "entryPx": 150, "unrealizedPnl": 40, "returnOnEquity": 0.33,
                              "leverage": {"value": 5}, "liquidationPx": 90}}]},
            "xyz": {"marginSummary": {"accountValue": "100"}, "withdrawable": "100", "assetPositions": []}},
        # NOTE: no ratchet_stop_list::<wallet> fixture — the list call yields no records at all.
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
    sol = {s["name"]: s for s in res["strategies"]}["kodiak"]["positions"][0]["dsl"]
    assert sol["armed"] is False
    assert sol["hard_stop_roe_pct"] == -15.0
    assert sol["arm_at_roe_pct"] == 10
    assert "protected from entry" in sol["note"]


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
