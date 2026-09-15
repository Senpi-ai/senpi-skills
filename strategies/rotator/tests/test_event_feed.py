"""Rotator — the smart-money reads return on the REAL payload shapes.

market_get_cross_asset_flows arrives as {success, data: {leader: {asset, move_pct, direction, ...},
laggards: [...], timestamp}}: the leader's move is `leader.move_pct` (no top-level `leader_move_pct`),
so the old read scored every laggard against a 0.0 move and found no candidates.
leaderboard_get_momentum_events arrives as {success, data: {events: {events: [...], query, total_count}}};
an event is a TRADER crossing a 4h delta-PnL tier, the markets driving it nested in `top_positions`
[{market, direction, delta_pnl, leverage}], its time in `detected_at` (ISO-8601). The old read unwrapped
one level short, then looked for a top-level token and an epoch `ts`, so the per-coin boost was 0.
And scan.py read its own inputs through `_f(value, 2)` — `2` is a KEY to `_f`, so maxSlots came back
0.0 and every tick logged "book full (0/0)".

Fixtures are the live shapes trimmed to the fields the scanner reads; trader addresses zeroed.
Run: python3 -m pytest strategies/rotator/tests -q
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402
import scan  # noqa: E402

ZERO = "0x" + "0" * 40


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _flows():
    """createResponse(result) -> data IS the CrossAssetFlowResponse: leader block + laggards."""
    return {"success": True, "data": {
        "leader": {"asset": "BTC", "move_pct": 2.3, "direction": "LONG",
                   "move_started_at": "2026-09-15T18:40:00Z", "sm_aligned": True},
        "laggards": [
            {"asset": "SOL", "correlation_30d": 0.84, "expected_move_pct": 3.4, "actual_move_pct": 0.3,
             "gap_pct": 3.1, "avg_lag_minutes": 45, "lag_stddev_minutes": 20, "follow_rate": 0.9,
             "sm_direction": "LONG", "sm_starting_to_rotate": True, "confidence": 0.85},
            {"asset": "DOGE", "correlation_30d": 0.61, "expected_move_pct": 2.0, "actual_move_pct": 1.8,
             "gap_pct": 0.2, "avg_lag_minutes": 70, "lag_stddev_minutes": 55, "follow_rate": 0.55,
             "sm_direction": "NEUTRAL", "sm_starting_to_rotate": False, "confidence": 0.41}],
        "timestamp": 1789503749}}


def _feed():
    """createResponse({events}) -> data.events is the feed's response object; the list is one level down."""
    return {"success": True, "data": {"events": {"events": [
        {"trader_id": ZERO, "tier": 2, "tier_label": "Rare", "delta_pnl": 5900000.0, "decision": "sent",
         "blocked_reason": None, "blocked_details": None, "concentration": 0.66,
         "top_positions": [{"market": "SOL", "delta_pnl": 3900000.0, "direction": "long", "leverage": 8},
                           {"market": "xyz:GOLD", "delta_pnl": 1400000.0, "direction": "short", "leverage": 5}],
         "trader_tags": {"tas": "Tactical", "tcs": "RELIABLE"}, "detected_at": _iso(20)},
        {"trader_id": ZERO, "tier": 3, "tier_label": "Extreme", "delta_pnl": 10800000.0, "decision": "blocked",
         "blocked_reason": "system_cooldown_active", "blocked_details": None, "concentration": 0.8,
         "top_positions": [{"market": "SOL", "delta_pnl": 8600000.0, "direction": "long", "leverage": 10}],
         "trader_tags": None, "detected_at": _iso(300)},                        # 5h old -> outside the 4h window
        {"trader_id": ZERO, "tier": 1, "tier_label": "Exceptional", "delta_pnl": 2050000.0, "decision": "sent",
         "blocked_reason": None, "blocked_details": None, "concentration": 0.5,
         "top_positions": [{"market": "SOL", "delta_pnl": 1000000.0, "direction": "long", "leverage": 3}],
         "trader_tags": None, "detected_at": _iso(3)},                          # tier 1 < minEventTier 2
    ], "query": {"from": "2026-09-15T16:00:00Z", "to": "2026-09-15T20:00:00Z",
                 "limit": 50, "tier": None, "asset": None},
        "total_count": 3}}}


def test_flow_read_takes_the_leader_move_from_the_nested_leader_block():
    flow = scoring.unwrap_flow(_flows())
    assert scoring.leader_move_from_flow(flow) == 2.3
    lag = scoring.laggards(flow, {})                       # defaults: follow >= 0.7, conf >= 0.5, |gap| >= 0.5
    assert [l["coin"] for l in lag] == ["SOL"] and lag[0]["direction"] == "LONG"
    assert lag[0]["leader_move"] == 2.3 and lag[0]["gap"] == 3.1


def test_events_by_coin_reads_top_positions_and_detected_at():
    events = _feed()["data"]["events"]["events"]
    per, total = scoring.events_by_coin(events, time.time(), {"eventWindowMin": 240, "minEventTier": 2})
    assert total == 1                                      # the 5h-old tier 3 and the tier 1 are out
    assert per == {"SOL": {"count": 1, "longs": 1, "shorts": 0},
                   "GOLD": {"count": 1, "longs": 0, "shorts": 1}}
    assert scoring.event_boost("SOL", "LONG", per, total, {}) == 0.8125


class _State:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def last(self):
        return self.rows[-1] if self.rows else None

    def __len__(self):
        return len(self.rows)


class _MCP:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "strategy_get_clearinghouse_state":
            return {"success": True, "data": {"main": {"assetPositions": []}, "xyz": {"assetPositions": []}}}
        if name == "market_get_cross_asset_flows":
            return _flows()
        if name == "market_get_funding_regime":
            return {"success": True, "data": {"regime": "NEUTRAL", "long_funding_assets": 12,
                                              "short_funding_assets": 11, "avg_funding_annualized": 4.1,
                                              "extreme_funding_count": 0, "regime_duration_hours": 6,
                                              "timestamp": 1789503749}}
        if name == "leaderboard_get_momentum_events":
            return _feed()
        raise AssertionError(f"unexpected read {name}")


class _Ctx:
    def __init__(self):
        self.senpi_mcp = _MCP()
        self.wallet = ZERO
        self.state = _State()


def test_scan_emits_with_the_event_boost_from_the_real_envelopes():
    ctx = _Ctx()
    out = scan.scan({"maxSlots": 2, "minScore": 3.5, "recentSignalTtlSeconds": 10800}, ctx)
    assert len(out) == 1 and out[0]["asset"] == "SOL" and out[0]["direction"] == "LONG"
    assert out[0]["data"]["score"] == pytest.approx(3.06 + 0.8125, abs=1e-3)   # flow + the event boost (was 0)
    assert out[0]["leverage"] == 4 and out[0]["marginPct"] == 22                  # base band
    assert [a for n, a in ctx.senpi_mcp.calls if n == "leaderboard_get_momentum_events"] == [{}]  # no exact-tier filter


def test_inputs_are_read_by_key_so_the_book_has_slots():
    ctx = _Ctx()
    assert scan.scan({"maxSlots": 1, "minScore": 3.5}, ctx) != []           # 1 free slot -> emits
    full = _Ctx()
    assert scan.scan({"maxSlots": 0, "minScore": 3.5}, full) == []          # 0 slots -> book full, no reads
    assert [n for n, _ in full.senpi_mcp.calls] == ["strategy_get_clearinghouse_state"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
