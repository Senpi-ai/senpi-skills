"""The scanner against a fake MCP: a full book emits nothing, a partly full one fills only the free
slots (positions on either dex count), and the funding rate is read from asset_context.
Run: python3 -m pytest strategies/aegis/tests -q"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402

_FALLING = [{"h": str(120 - 2 * i), "l": str(110 - 2 * i)} for i in range(6)]   # lower highs on every market


class _State:
    def __init__(self):
        self.rows = []

    def last(self):
        return self.rows[-1] if self.rows else None

    def append(self, row):
        self.rows.append(row)


class _MCP:
    """Long-crowded funding and every market falling: risk-off, and positive funding pays shorts."""

    def __init__(self, held):
        self.held = held

    def call_tool(self, name, args):
        if name == "strategy_get_clearinghouse_state":
            def section(coins):
                return {"marginSummary": {"accountValue": "1000", "totalMarginUsed": str(10 * len(coins))},
                        "assetPositions": [{"position": {"coin": c, "szi": "-1"}} for c in coins]}
            return {"data": {"main": section([c for c in self.held if not c.startswith("xyz:")]),
                             "xyz": section([c for c in self.held if c.startswith("xyz:")])}}
        if name == "market_get_funding_regime":
            return {"data": {"regime": "LONG_CROWDED"}}
        if name == "market_get_asset_data":
            return {"data": {"candles": {"4h": _FALLING}, "asset_context": {"funding": "0.0000125"},
                             "oi_velocity": {"oi_trend": "BUILDING", "oi_acceleration": "INCREASING"}}}
        raise AssertionError(f"unexpected tool {name}")


def _run(held):
    ctx = SimpleNamespace(senpi_mcp=_MCP(held), state=_State(), wallet="0x0")
    return scan.scan({"maxSlots": 4, "minScore": 25}, ctx)


def test_an_empty_book_fills_every_slot_and_reads_funding():
    out = _run([])
    assert len(out) == 4
    assert all(o["direction"] == "SHORT" and "funding collecting" in o["data"]["reasons"] for o in out)


def test_slots_count_positions_on_both_dexes():
    assert _run(["BTC", "ETH", "xyz:SP500", "xyz:XYZ100"]) == []
    out = _run(["BTC", "ETH", "xyz:SP500"])
    assert [o["asset"] for o in out] == ["SOL"]
