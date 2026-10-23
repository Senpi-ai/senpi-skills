#!/usr/bin/env python3
"""Offline engine test — runs status.run() against a recorded MCP fixture (no network).

The fixture entries are REAL response shapes captured from the senpi MCP server
(success/data envelopes, decimals-as-strings) —
do not hand-edit shapes; re-record with `status.py --dry` if the API changes."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import status  # noqa: E402
import mcp_client  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "status_fixture.json")


def _recorded():
    with open(FIXTURE) as f:
        return json.load(f)


def _result():
    return status.run(status._FixtureClient(_recorded()))


def _paid_result():
    """The same user on a paid plan: the backend reports the fee the engine charges, 0."""
    recorded = _recorded()
    points = recorded["user_get_senpi_points::0xme"]["data"]
    points.update(loyaltyTierFee=0, builderFeePercent="0%", nextTierFee="0%")
    return status.run(status._FixtureClient(recorded))


def test_identity_and_points():
    r = _result()
    assert r["identity"]["senpi_user_id"] == "u123"
    assert r["identity"]["wallet"] == "0xme"          # embedded, not injected/subwallet
    assert r["points"]["total"] == 15000 and r["points"]["rank"] == 42.0
    assert r["points"]["base"] == 2000 and r["points"]["perp"] == 13000


def test_a_free_account_reports_the_flat_fee_whatever_its_tier():
    # the fixture user holds SILVER with no paid plan: the flat free rate, no tier discount
    loy = _result()["loyalty"]
    assert loy["tier"] == "SILVER"
    assert loy["fee_bps"] == 50.0                      # loyaltyTierFee (tenths of a bp), not feeBps
    assert loy["fee_pct"] == "0.05%"
    assert loy["fee_discount_pct"] == 0.0              # get_loyalty_tiers: 0 on every tier
    assert loy["next_tier"] == "GOLD" and loy["points_to_next"] == 5000.0


def test_a_paid_account_reports_a_zero_fee_not_a_missing_one():
    loy = _paid_result()["loyalty"]
    assert loy["fee_bps"] == 0.0
    assert loy["fee_pct"] == "0%"
    assert loy["tier"] == "SILVER" and loy["next_tier"] == "GOLD"


def test_loyalty_demotion():
    loy = _result()["loyalty"]
    assert loy["demoted"] is True and loy["previous_tier"] == "APEX"
    assert loy["maintenance_deadline"] == "2026-08-23T13:25:02.760Z"


def test_arena_retired():
    """Arena is deprecated and the arena_* MCP tools were removed from the server —
    the engine must neither emit an arena section nor attempt an arena call."""
    r = _result()
    assert "arena" not in r
    assert not any("arena" in w for w in r["meta"]["warnings"])


def test_referral():
    r = _result()
    assert r["referral"]["balance_usdc"] == 12.5
    assert "wins" not in r                              # get_share_your_wins is retired


def test_fails_open_on_empty():
    r = status.run(status._FixtureClient({}))
    assert "points" in r and r["meta"].get("degraded")


def test_client_raises_on_rpc_error():
    try:
        mcp_client._unwrap({"jsonrpc": "2.0", "id": 1,
                            "error": {"code": -32602, "message": "Unknown tool"}})
        assert False, "expected MCPError"
    except mcp_client.MCPError as e:
        assert "Unknown tool" in str(e)


def test_client_raises_on_tool_iserror():
    try:
        mcp_client._unwrap({"jsonrpc": "2.0", "id": 1, "result": {
            "isError": True, "content": [{"type": "text", "text": "Unauthorized"}]}})
        assert False, "expected MCPError"
    except mcp_client.MCPError as e:
        assert "Unauthorized" in str(e)


def test_client_raises_on_app_level_failure():
    # the senpi server reports tool failures as HTTP 200 + {"success": false, "error": {...}}
    doc = {"success": False, "error": {"code": "NOT_FOUND", "message": "Unknown tool: x"}}
    rpc = {"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": json.dumps(doc)}]}}
    try:
        mcp_client._unwrap(rpc)
        assert False, "expected MCPError"
    except mcp_client.MCPError as e:
        assert "NOT_FOUND" in str(e) and "Unknown tool" in str(e)


def test_client_unwraps_success():
    doc = {"success": True, "data": {"x": 1}}
    rpc = {"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": json.dumps(doc)}]}}
    assert mcp_client._unwrap(rpc) == doc


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
