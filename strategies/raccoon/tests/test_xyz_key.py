"""The smart-money read keys the board the way the universe names a market, so an `xyz:` name
resolves to its own dex row and a main/xyz twin never collides — and the weekend scan emits.
Run: python3 -m pytest strategies/raccoon/tests -q"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402

# leaderboard_get_markets, the live envelope: data.markets.markets[] + window metadata; one row per
# token+dex+direction with a BARE `token`, a separate `dex` ("" main / "xyz") and a lowercase
# `direction` (hyperfeed-markets guide; senpi-mcp LeaderboardTrendingMarketEntry). NVDA on the
# main dex is a synthetic twin: the board keys token+dex, so one is always possible.
def _row(token, dex, direction, gain, traders, dominant):
    return {"token": token, "dex": dex, "direction": direction, "max_leverage": 20,
            "pct_of_top_traders_gain": gain, "contribution_pct_change_15m": 0.4,
            "contribution_pct_change_1h": 1.1, "contribution_pct_change_4h": 2.0,
            "token_price_change_pct_15m": -0.2, "token_price_change_pct_1h": -0.9,
            "token_price_change_pct_4h": -3.1, "day_notional_volume": 42251428.13,
            "trader_count": traders, "is_dominant_direction": dominant}


BOARD = {"success": True, "data": {"markets": {"markets": [
    _row("NVDA", "xyz", "short", 6.0, 41, True),
    _row("NVDA", "xyz", "long", 2.0, 12, False),
    _row("NVDA", "", "long", 6.0, 30, True),      # main-dex twin: long-leaning
    _row("NVDA", "", "short", 4.0, 19, False),
    _row("BTC", "", "long", 5.52, 112, True),
], "source_trader_count": 100, "window": "4h", "timestamp": 1789503722}}}

# market_list_instruments(dex="xyz"), two rows trimmed from a live read (2026-09-15): names carry
# the `xyz:` prefix and there is no `dex` field on the row.
INSTRUMENTS = {"success": True, "data": {"instruments": [
    {"name": "xyz:NVDA", "sz_decimals": 3, "max_leverage": 20, "is_delisted": False,
     "only_isolated": False, "margin_modes": ["CROSS", "ISOLATED"], "margin_mode": None,
     "context": {"coin": "xyz:NVDA", "funding": "0.00000625", "openInterest": "599909.298",
                 "prevDayPx": "211.58", "dayNtlVlm": "42251428.1297499985", "premium": "0.0002165725",
                 "oraclePx": "212.4", "markPx": "212.43", "midPx": "212.435",
                 "impactPxs": ["212.43", "212.462"], "dayBaseVlm": "198816.463"}},
    {"name": "xyz:GOLD", "sz_decimals": 3, "max_leverage": 25, "is_delisted": False,
     "only_isolated": False, "margin_modes": ["CROSS", "ISOLATED"], "margin_mode": None,
     "context": {"coin": "xyz:GOLD", "funding": "0.0000139846", "openInterest": "40874.363",
                 "prevDayPx": "4298.9", "dayNtlVlm": "56507321.3679500222", "premium": "-0.0000605",
                 "oraclePx": "4298.86", "markPx": "4298.6", "midPx": "4298.75",
                 "impactPxs": ["4298.6", "4298.9"], "dayBaseVlm": "13144.72"}},
], "count": 2}}

# 48 hourly candles (o/h/l/c/v strings, as Hyperliquid serves them): -3% over the window with a
# volume pickup in the last 6 bars — a SHORT weekend drift.
_CANDLES = [{"t": 1789331200000 + i * 3600000, "o": "100.0", "h": "100.5", "l": "96.5",
             "c": "100.0" if i < 42 else "97.0", "v": "10" if i < 42 else "20"} for i in range(48)]


class _MCP:
    def call_tool(self, name, args):
        if name == "leaderboard_get_markets":
            return BOARD
        if name == "market_list_instruments":
            return INSTRUMENTS
        if name == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"marginSummary": {"accountValue": "1000"}, "assetPositions": []},
                             "xyz": {"marginSummary": {"accountValue": "1000"}, "assetPositions": []}}}
        if name == "market_get_asset_data":
            return {"success": True, "data": {"candles": {"1h": _CANDLES, "4h": []}, "asset_context": {}}}
        raise AssertionError(f"unexpected tool {name}")


def _ctx():
    return SimpleNamespace(senpi_mcp=_MCP(), state=None, wallet="0x0")


def test_xyz_name_keys_to_its_own_dex_row():
    sm = scan._fetch_sm_map(_ctx())
    assert sm["XYZ:NVDA"] == (2.0, 6.0, 12, 41)   # xyz:NVDA finds the NVDA rows on dex xyz (pct, then headcount, per side)
    assert sm["NVDA"] == (6.0, 4.0, 30, 19)       # the main-dex twin keeps its own key, neither overwrites


def test_sm_direction_resolves_the_xyz_row_not_the_twin():
    sm = scan._fetch_sm_map(_ctx())
    assert scan._sm_direction(sm, "xyz:NVDA") == ("SHORT", 75.0)
    assert scan._sm_direction(sm, "NVDA") == ("LONG", 60.0)


def test_weekend_scan_emits_on_the_xyz_row(monkeypatch):
    monkeypatch.setattr(scan.scoring, "in_weekend_window", lambda _now: True)
    out = scan.scan({}, _ctx())
    assert [o["asset"] for o in out] == ["xyz:NVDA"]      # the SM gate passes; GOLD has no board row
    assert (out[0]["direction"], out[0]["data"]["smTiltPct"]) == ("SHORT", 75.0)   # the xyz lean, not the twin's LONG 60
