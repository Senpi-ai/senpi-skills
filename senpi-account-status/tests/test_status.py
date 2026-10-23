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


class _Recording:
    """A fixture client that remembers which tools the engine asked for."""

    def __init__(self, recorded):
        self._inner = status._FixtureClient(recorded)
        self.tools = []

    def mcp_call(self, tool, timeout=12, **kw):
        self.tools.append(tool)
        return self._inner.mcp_call(tool, timeout=timeout, **kw)


def _with_fee_fields(**fields):
    """The fixture user with its points payload's fee fields replaced by `fields`."""
    recorded = _recorded()
    points = recorded["user_get_senpi_points::0xme"]["data"]
    for name in ("loyaltyTierFee", "feeBps", "builderFeeBps", "builderFeePercent"):
        points.pop(name, None)
    points.update(fields)
    return status.run(status._FixtureClient(recorded))


def test_identity_and_points():
    r = _result()
    assert r["identity"]["senpi_user_id"] == "u123"
    assert r["identity"]["wallet"] == "0xme"          # embedded, not injected/subwallet
    assert r["points"]["total"] == 15000 and r["points"]["rank"] == 42.0
    assert r["points"]["base"] == 2000 and r["points"]["perp"] == 13000


def test_the_output_is_points_fee_and_referral_and_nothing_about_tiers():
    # the recorded points payload still carries loyaltyTier, isDemoted, nextTier and the rest;
    # none of it may reach the agent
    assert _result() == {
        "as_of": "live",
        "identity": {"senpi_user_id": "u123", "wallet": "0xme"},
        "points": {"total": 15000.0, "base": 2000.0, "perp": 13000.0, "multiplier": 1.5,
                   "rank": 42.0, "rank_change": 3.0, "found": True},
        "fee": {"bps": 30.0, "pct": "0.03%"},
        "referral": {"balance_usdc": 12.5, "wallet": "0xme"},
        "meta": {"warnings": []},
    }


def test_a_paid_account_reports_a_zero_fee_not_a_missing_one():
    paid = _with_fee_fields(loyaltyTierFee=0, builderFeePercent="0%")
    assert paid["fee"] == {"bps": 0.0, "pct": "0%"}


def test_the_fee_bps_is_read_from_whichever_field_the_payload_carries():
    for field in ("loyaltyTierFee", "feeBps", "builderFeeBps"):
        assert _with_fee_fields(**{field: 12})["fee"]["bps"] == 12.0, field


def test_a_payload_without_fee_fields_reports_none_not_a_guess():
    assert _with_fee_fields()["fee"] == {"bps": None, "pct": None}


def test_the_engine_never_asks_for_the_tier_table():
    client = _Recording(_recorded())
    status.run(client)
    status._dry(client)
    assert sorted(set(client.tools)) == [
        "user_get_me", "user_get_referral_rewards", "user_get_senpi_points"]


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
