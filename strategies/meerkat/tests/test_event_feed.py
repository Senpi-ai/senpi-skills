"""Meerkat — the momentum-event feed read returns on the REAL payload shape.

leaderboard_get_momentum_events is TRADER profit-threshold crossings (tier 1/2/3 = $2M/$5.5M/$10M 4h
delta-PnL), delivered by the MCP tool as {success, data: {events: {events: [...], query, total_count}}}.
Each event's tradeable markets sit in `top_positions` [{market, direction, delta_pnl, leverage}] and its
time in `detected_at` (ISO-8601). Before this fix the scanner unwrapped `data.events` as if it were the
list (it is the feed's response object), then looked for a top-level token and an epoch `ts` — so it saw
no events, dropped every event it could have seen, and sat WAITING forever.

Fixtures are the live shape trimmed to the fields the scanner reads; trader addresses zeroed.
Run: python3 -m pytest strategies/meerkat/tests -q
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


def _feed():
    """The tool envelope verbatim: createResponse -> data.events = the feed's response object."""
    return {"success": True, "data": {"events": {"events": [
        {"trader_id": ZERO, "tier": 2, "tier_label": "Rare", "delta_pnl": 5600000.0, "decision": "sent",
         "blocked_reason": None, "blocked_details": None, "concentration": 0.72,
         "top_positions": [
             {"market": "ETH", "delta_pnl": 4000000.0, "direction": "long", "leverage": 10},
             {"market": "xyz:NVDA", "delta_pnl": 1500000.0, "direction": "short", "leverage": 5}],
         "trader_tags": {"tas": "Active", "tcs": "ELITE"}, "detected_at": _iso(5)},
        {"trader_id": ZERO, "tier": 3, "tier_label": "Extreme", "delta_pnl": 10400000.0, "decision": "blocked",
         "blocked_reason": "trader_cooldown_active", "blocked_details": None, "concentration": 0.91,
         "top_positions": [{"market": "ETH", "delta_pnl": 9800000.0, "direction": "long", "leverage": 20}],
         "trader_tags": None, "detected_at": _iso(180)},                       # 3h old -> stale
        {"trader_id": ZERO, "tier": 1, "tier_label": "Exceptional", "delta_pnl": 2100000.0, "decision": "blocked",
         "blocked_reason": "no_positions", "blocked_details": None, "concentration": None,
         "top_positions": None, "trader_tags": None, "detected_at": _iso(1)},   # null positions -> nothing
    ], "query": {"from": "2026-09-15T16:00:00Z", "to": "2026-09-15T20:00:00Z",
                 "limit": 50, "tier": None, "asset": None},
        "total_count": 3}}}


_CONFIG = {"minTier": 2, "maxEventAgeMinutes": 30.0, "smTiltMinPct": 55, "smStrongTiltPct": 70}


def test_feed_flattens_to_per_market_records_with_the_feeds_tier_and_time():
    events = _feed()["data"]["events"]["events"]
    recs = scoring.flatten_events(events)
    assert [r["market"] for r in recs] == ["ETH", "xyz:NVDA", "ETH"]
    th = scoring.build_thesis(recs[0], _CONFIG, time.time(), None, False)
    assert th["coin"] == "ETH" and th["direction"] == "LONG" and th["tier"] == 2     # tier = the feed's field
    assert th["age_min"] == 5.0 and "fresh_5min" in th["reasons"]                   # age from detected_at
    assert th["magnitude_pct"] == 71.43                                              # ETH drove 71% of the crossing
    assert scoring.build_thesis(recs[2], _CONFIG, time.time(), None, False) is None  # 3h old -> stale


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
    def __init__(self, held=()):
        self.calls = []
        self.held = held

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "leaderboard_get_momentum_events":
            return _feed()
        if name == "strategy_get_clearinghouse_state":
            pos = [{"position": {"coin": c, "szi": "1.5", "marginUsed": "300.0"}} for c in self.held]
            used = "300.0" if pos else "0.0"
            main = {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": used, "totalNtlPos": used},
                    "assetPositions": pos}
            xyz = {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0"},
                   "assetPositions": []}
            return {"success": True, "data": {"main": main, "xyz": xyz}}
        if name == "leaderboard_get_markets":
            return {"success": True, "data": {"markets": {"markets": [
                {"token": "ETH", "dex": "", "direction": "long", "pct_of_top_traders_gain": 38.2,
                 "trader_count": 41, "is_dominant_direction": True},
                {"token": "ETH", "dex": "", "direction": "short", "pct_of_top_traders_gain": 0.4,
                 "trader_count": 3, "is_dominant_direction": False},
                {"token": "NVDA", "dex": "xyz", "direction": "short", "pct_of_top_traders_gain": 6.1,
                 "trader_count": 9, "is_dominant_direction": True}],
                "source_trader_count": 100, "window": "4h", "timestamp": 1789503749}}}
        if name == "market_get_asset_data":
            return {"success": True, "data": {"candles": {"1h": [{"v": "100"}] * 3 + [{"v": "160"}] * 3}}}
        raise AssertionError(f"unexpected read {name}")


class _Ctx:
    def __init__(self, held=()):
        self.senpi_mcp = _MCP(held)
        self.wallet = ZERO
        self.state = _State()


_INPUTS = {"minTier": 2, "maxEventAgeMinutes": 30, "minScore": 4, "marginPct": 15, "leverage": 4}


def test_envelope_unwraps_data_events_events():
    ctx = _Ctx()
    events = scan._fetch_momentum_events(ctx)
    assert len(events) == 3 and events[0]["tier"] == 2
    assert events[0]["top_positions"][0]["market"] == "ETH"


def test_scan_emits_the_freshest_crossings_market_from_the_real_envelope():
    ctx = _Ctx()
    out = scan.scan(dict(_INPUTS), ctx)
    assert len(out) == 1 and out[0]["asset"] == "ETH" and out[0]["direction"] == "LONG"
    d = out[0]["data"]
    assert d["tier"] == 2 and d["ageMin"] == 5.0 and d["score"] == 7        # tier2 + fresh + sm + sm_strong + vol
    # the per-asset reads ran once per fresh market, never once per event x position
    reads = sorted(a["asset"] for n, a in ctx.senpi_mcp.calls if n == "market_get_asset_data")
    assert reads == ["ETH", "xyz:NVDA"]
    assert ctx.state.last()["result"]["emitted"] is True and "ETH" in ctx.state.last()["signaled"]


def test_held_market_is_skipped_and_the_xyz_name_keeps_the_venue_casing():
    ctx = _Ctx(held=("ETH",))
    out = scan.scan(dict(_INPUTS), ctx)
    assert len(out) == 1 and out[0]["asset"] == "xyz:NVDA" and out[0]["direction"] == "SHORT"
    assert out[0]["data"]["heldAssets"] == ["ETH"] and out[0]["leverage"] == 4


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
