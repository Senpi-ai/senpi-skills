"""Otter could never emit. Two reads broke on the live shapes, one after the other.

1. The order book. market_get_asset_data returns the book as {coin, time, levels: [[bids], [asks]]}
   with {px, sz, n} rows (read live, Sep 17). fetch_spread_bps looked for `bids` / `asks` keys, got
   nothing, returned None, and apply_spread_bonus dropped the candidate — on 1,202 of 4,737 ticks a
   live wallet had a score-9 candidate and emitted nothing.
2. The leverage tiers. runtime.yaml writes leverageTiers as [[min_score, leverage], ...];
   get_leverage_for_score indexed tier["min_score"], so the first candidate to survive the book read
   would raise TypeError.
"""
import os
import sys
import types

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

LIVE_BOOK = {"success": True, "data": {"asset": "SOL", "order_book": {"coin": "SOL", "time": 1789666216440, "levels": [
    [{"px": "101.86", "sz": "798.47", "n": 16}, {"px": "101.85", "sz": "1549.25", "n": 11}],
    [{"px": "101.87", "sz": "943.77", "n": 14}, {"px": "101.88", "sz": "831.12", "n": 9}]]}}}


def _ctx(payload):
    return types.SimpleNamespace(senpi_mcp=types.SimpleNamespace(call_tool=lambda name, args: payload))


def test_the_spread_is_read_from_the_levels_the_mcp_returns():
    bps = scan.fetch_spread_bps(_ctx(LIVE_BOOK), "SOL")
    assert bps is not None and abs(bps - (0.01 / 101.865 * 10000)) < 1e-6


def test_a_bids_asks_book_still_reads():
    legacy = {"data": {"order_book": {"bids": [{"px": "10.0"}], "asks": [{"px": "10.1"}]}}}
    assert abs(scan.fetch_spread_bps(_ctx(legacy), "X") - (0.1 / 10.05 * 10000)) < 1e-6


def test_the_runtime_yaml_tiers_map_score_to_leverage():
    rt = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
    tiers = next(s for s in rt["scanners"] if s.get("inputs", {}).get("leverageTiers"))["inputs"]["leverageTiers"]
    assert [scoring.get_leverage_for_score(s, tiers) for s in (14, 13, 12, 11, 10, 9, 8)] == [10, 10, 7, 7, 5, 5, 5]
    assert scoring.get_leverage_for_score(13) == 10          # the dict defaults still work


def test_entries_are_capped_at_three_a_day():
    # each entry is 1.25-2.5x the account in notional; 8 a day would bleed fees the way owl's did
    rt = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
    assert rt["risk"]["guard_rails"]["max_entries_per_day"] == 3
