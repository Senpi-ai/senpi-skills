"""The swing leg's smart-money read keys the board the way the universe names a market, so an
`xyz:` name resolves to its own dex row, a main/xyz twin never collides, and the emitted signal
carries the xyz lean. Read failures are logged, not swallowed. (The scalp leg has no smart-money read.)
Run: python3 -m pytest strategies/spider/tests -q"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "swing", "scanners"))

import scan  # noqa: E402

# leaderboard_get_markets, the live envelope: data.markets.markets[] + window metadata; one row per
# token+dex+direction with a BARE `token`, a separate `dex` ("" main / "xyz") and a lowercase
# `direction` (hyperfeed-markets guide; senpi-mcp LeaderboardTrendingMarketEntry). NVDA on the
# main dex is a synthetic twin: the board keys token+dex, so one is always possible.
def _row(token, dex, direction, gain, traders, dominant):
    return {"token": token, "dex": dex, "direction": direction, "max_leverage": 20,
            "pct_of_top_traders_gain": gain, "contribution_pct_change_15m": 0.4,
            "contribution_pct_change_1h": 1.1, "contribution_pct_change_4h": 2.0,
            "token_price_change_pct_15m": 0.2, "token_price_change_pct_1h": 0.9,
            "token_price_change_pct_4h": 3.1, "day_notional_volume": 42251428.13,
            "trader_count": traders, "is_dominant_direction": dominant}


BOARD = {"success": True, "data": {"markets": {"markets": [
    _row("NVDA", "xyz", "short", 6.0, 41, True),
    _row("NVDA", "xyz", "long", 2.0, 12, False),
    _row("NVDA", "", "long", 6.0, 30, True),      # main-dex twin: long-leaning
    _row("NVDA", "", "short", 4.0, 19, False),
    _row("SUI", "", "long", 3.6, 69, True),
], "source_trader_count": 100, "window": "4h", "timestamp": 1789503722}}}

# market_list_instruments, one xyz row trimmed from a live read (2026-09-15): the name carries the
# `xyz:` prefix and there is no `dex` field on the row.
INSTRUMENTS = {"success": True, "data": {"instruments": [
    {"name": "xyz:NVDA", "sz_decimals": 3, "max_leverage": 20, "is_delisted": False,
     "only_isolated": False, "margin_modes": ["CROSS", "ISOLATED"], "margin_mode": None,
     "context": {"coin": "xyz:NVDA", "funding": "0.00000625", "openInterest": "599909.298",
                 "prevDayPx": "211.58", "dayNtlVlm": "42251428.1297499985", "premium": "0.0002165725",
                 "oraclePx": "212.4", "markPx": "212.43", "midPx": "212.435",
                 "impactPxs": ["212.43", "212.462"], "dayBaseVlm": "198816.463"}},
], "count": 1}}


def _rising(n, start, drift):
    out, px = [], start
    for _ in range(n):
        o, px = px, px * (1 + drift)
        out.append({"o": str(o), "c": str(px), "h": str(px * 1.001), "l": str(o * 0.999), "v": "100"})
    return out


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
            return {"success": True, "data": {"candles": {"1h": _rising(30, 200, 0.004),
                                                          "4h": _rising(10, 190, 0.01)},
                                              "asset_context": {}}}
        raise AssertionError(f"unexpected tool {name}")


def _ctx(mcp=None):
    return SimpleNamespace(senpi_mcp=mcp or _MCP(), state=None, wallet="0x0")


def test_xyz_name_keys_to_its_own_dex_row():
    sm = scan._get_sm_map(_ctx())
    assert sm["XYZ:NVDA"] == 25.0      # xyz:NVDA finds the NVDA rows on dex xyz (2 long / 6 short)
    assert sm["NVDA"] == 60.0          # the main-dex twin keeps its own key, neither overwrites


def test_scan_scores_the_xyz_name_with_its_own_lean():
    out = scan.scan({"cryptoAlts": [], "xyzIncludeSet": ["NVDA"], "minScore": -100}, _ctx())
    assert [o["asset"] for o in out] == ["xyz:NVDA"]
    assert (out[0]["data"]["smPct"], "sm_short_25%" in out[0]["data"]["reasons"]) == (25.0, True)   # not the twin's 60


def test_read_failure_is_logged_and_degrades_to_empty(capsys):
    class _Down:
        def call_tool(self, name, args):
            raise RuntimeError("upstream unavailable")

    assert scan._get_sm_map(_ctx(_Down())) == {}
    assert "leaderboard_get_markets read failed" in capsys.readouterr().err
