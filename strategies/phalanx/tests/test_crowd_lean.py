"""The 4h board read that feeds the divergence booster. The MCP returns the board nested:
the leaderboard_get_markets handler wraps its client's {markets: [...]} under `markets` again,
so live rows arrive at data.markets.markets. A reader that accepts only a flat list there reads
nothing, and the booster never fires. Athena's phalanx leg is byte-identical (athena drift test)."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402

ROWS = [{"token": "ETH", "dex": "", "direction": "long", "pct_of_top_traders_gain": 40.0,
         "token_price_change_pct_4h": 1.2, "trader_count": 300, "is_dominant_direction": True},
        {"token": "ETH", "dex": "", "direction": "short", "pct_of_top_traders_gain": 10.0,
         "token_price_change_pct_4h": 1.2, "trader_count": 60, "is_dominant_direction": False}]


def _ctx(payload):
    return types.SimpleNamespace(senpi_mcp=types.SimpleNamespace(call_tool=lambda name, args: payload))


def test_the_board_is_read_from_the_nested_response_the_mcp_returns():
    live = {"success": True, "data": {"markets": {"markets": ROWS, "source_trader_count": 500,
                                                  "window": "4h", "timestamp": 0}}}
    assert scan._crowd_lean(_ctx(live), 100) == {"ETH": 80.0}      # 40 long of 50 → 80% long


def test_a_flat_board_still_reads():
    assert scan._crowd_lean(_ctx({"success": True, "data": {"markets": ROWS}}), 100) == {"ETH": 80.0}
