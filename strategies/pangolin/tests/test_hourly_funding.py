"""Pangolin never faded a crowd: two unit errors kept every candidate under its score floor.

1. Persistence came from market_get_funding_history, which annualizes Hyperliquid's hourly rate as if it
   were an 8h rate (8x too low) and filters at 20% of that — 160% true APR — so rows existed only for the
   most extreme names and persistence rarely cleared the 3h gate. Pangolin now counts persistence from
   market_get_asset_data's hourly fundingRate rows ({coin, fundingRate, premium, time}, read live Sep 17)
   at its own minFundingRate floor.
2. The funding-extremity tiers (0.0003 / 0.0006 / 0.001 per hour = 263% / 526% / 876% APR) were the v2
   8h values, left unconverted when the floor was recalibrated to hourly. They are now 0.0000375 /
   0.000075 / 0.000125 per hour (33% / 66% / 110% APR).
"""
import os
import sys
import types

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

FLOOR = 0.0000228          # runtime.yaml minFundingRate, ~20% APR
H = 3_600_000


def _hourly(rates, t0=1_789_000_000_000):
    return [{"coin": "BTC", "fundingRate": f"{r:.10f}", "premium": "0", "time": t0 + i * H}
            for i, r in enumerate(rates)]


def test_persistence_counts_the_latest_hours_on_the_crowd_side_at_the_floor():
    assert scoring.funding_history_stats(_hourly([0.00001] * 3 + [0.00003] * 5), FLOOR, 0.00003)["persistence_hours"] == 5
    assert scoring.funding_history_stats(_hourly([-0.00003] * 4), FLOOR, -0.00003)["persistence_hours"] == 4
    assert scoring.funding_history_stats(_hourly([-0.00003] * 3 + [0.00003] * 2), FLOOR, 0.00003)["persistence_hours"] == 2
    assert scoring.funding_history_stats([], FLOOR, 0.00003) is None


def test_trend_reads_the_crowd_paying_more_or_less():
    a, b = 0.0001, 0.00004
    assert scoring.funding_history_stats(_hourly([a, a, a, b, b, b]), FLOOR, b)["trend"] == "DECREASING"
    assert scoring.funding_history_stats(_hourly([b, b, b, a, a, a]), FLOOR, a)["trend"] == "INCREASING"
    assert scoring.funding_history_stats(_hourly([-b, -b, -b, -a, -a, -a]), FLOOR, -a)["trend"] == "INCREASING"
    assert scoring.funding_history_stats(_hourly([a] * 6), FLOOR, a)["trend"] == "STABLE"


def _score(funding):
    fh = {"persistence_hours": 3, "trend": "STABLE"}               # persistence +1, trend 0
    return scoring.score_candidate("BTC", {"funding": funding, "openInterest": 1, "markPx": 1}, fh, None, None, 0)["score"]


def test_the_funding_tiers_are_hourly():
    assert _score(0.000126) == 5      # >= 110% APR: EXTREME +4
    assert _score(0.000076) == 4      # >= 66% APR: HIGH +3
    assert _score(0.0000376) == 3     # >= 33% APR: ELEVATED +2
    assert _score(0.00003) == 1       # ~26% APR: over the floor, no extremity points


class _State:
    def __init__(self): self._l = []
    def last(self): return self._l[-1] if self._l else None
    def append(self, d): self._l.append(d)


class _MCP:
    def __init__(self): self.calls = []

    def call_tool(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "market_list_instruments":
            return {"data": {"instruments": [{"name": "BTC", "context": {
                "funding": "0.000126", "openInterest": "1000", "markPx": "100000", "dayNtlVlm": "900000000"}}]}}
        if tool == "market_get_funding_regime":
            return {"data": {"regime": "LONG_CROWDED"}}
        if tool == "leaderboard_get_markets":
            return {"data": {"markets": {"markets": []}}}
        if tool == "market_get_asset_data":
            return {"success": True, "data": {"asset": "BTC", "funding_history": _hourly([0.000126] * 12)}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"assetPositions": []}}}
        return None


def test_scan_fades_a_persistent_confirmed_crowd_from_the_hourly_rows():
    mcp = _MCP()
    ctx = types.SimpleNamespace(state=_State(), wallet="0xabc", senpi_mcp=mcp)
    out = scan.scan({"quietHoursStartUtc": 0, "quietHoursEndUtc": 0}, ctx)   # quiet window off for the test
    assert [(s["asset"], s["direction"]) for s in out] == [("BTC", "SHORT")]
    assert out[0]["data"]["score"] == 9                  # 110% APR +4, 12h +3, regime confirms +2
    assert out[0]["data"]["persistenceHours"] == 12.0
    tools = [t for t, _ in mcp.calls]
    assert "market_get_funding_history" not in tools
    assert ("market_get_asset_data", {"asset": "BTC", "candle_intervals": [], "include_funding": True,
                                      "include_order_book": False}) in mcp.calls


def test_the_liquidity_floor_is_10m_of_open_interest():
    rt = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
    inputs = next(s for s in rt["scanners"] if s["name"] == "pangolin_main_signals")["inputs"]
    assert inputs["minOiUsd"] == 10_000_000
