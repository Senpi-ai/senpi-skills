"""Pilotfish: the proven-cohort read returns on the REAL discovery_get_top_traders shape.

The cohort filter parsed lifetime realized PnL as `realizedPnl` / `realized_profit_and_loss`.
The tool's rows carry `realizedProfitAndLoss`, so every wallet read as $0, the >= $1M cohort
was always empty and the scanner never had anything to follow. The fixture below is the live
row shape trimmed to the fields the scanner reads; addresses are zeroed.
Run: python3 -m pytest strategies/pilotfish/tests/test_realized_key.py -q"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
for _m in ("scoring", "scan"):          # never inherit another package's module of the same name
    sys.modules.pop(_m, None)
import scan  # noqa: E402

A1 = "0x" + "0" * 39 + "1"
A2 = "0x" + "0" * 39 + "2"
A3 = "0x" + "0" * 39 + "3"

# discovery_get_top_traders(time_frame=ALL_TIME, sort_by=PROFIT_AND_LOSS_REALIZED) — live envelope
_TOP_TRADERS = {"success": True, "data": {"traders": [
    {"address": A1, "shortAddress": "0x0000...0001", "returnOnInvestment": 62.0,
     "profitAndLoss": 1700000, "unRealizedProfitAndLoss": 200000,
     "realizedProfitAndLoss": 1500000, "winRate": 58.0, "tcsLabel": "ELITE"},
    {"address": A2, "shortAddress": "0x0000...0002", "returnOnInvestment": 14.0,
     "profitAndLoss": 300000, "unRealizedProfitAndLoss": 50000,
     "realizedProfitAndLoss": 250000, "winRate": 51.0, "tcsLabel": "RELIABLE"},
    {"address": A3, "shortAddress": "0x0000...0003", "returnOnInvestment": 120.0,
     "profitAndLoss": 4100000.5, "unRealizedProfitAndLoss": 100000,
     "realizedProfitAndLoss": 4000000.5, "winRate": 44.0, "tcsLabel": "STREAKY"},
]}}

# discovery_get_trader_state — live row shape (address + openPositions, string numerics)
def _state(addr):
    return {"address": addr, "shortAddress": "0x0000...000x", "openPositions": [
        {"coin": "BTC", "coinDisplayName": "BTC", "szi": "0.8", "entryPx": "60000",
         "positionValue": "50000", "unrealizedPnl": "1200",
         "leverage": {"type": "cross", "value": 10}, "marginUsed": "5000"}],
        "openOrders": [], "marginSummary": {"accountValue": "20000", "totalMarginUsed": "5000"}}


class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)
    def __len__(self): return len(self._log)


class _MCP:
    def __init__(self, rows=None):
        self.rows, self.calls = rows, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "discovery_get_top_traders":
            if (args or {}).get("offset", 0) > 0:
                return {"success": True, "data": {"traders": []}}
            return _TOP_TRADERS if self.rows is None else {"success": True, "data": {"traders": self.rows}}
        if tool == "discovery_get_trader_state":
            return {"success": True, "data": {"traders": [_state(a) for a in args["trader_addresses"]]}}
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"assetPositions": []}}
        return {}


class _Ctx:
    def __init__(self, mcp):
        self.senpi_mcp, self.wallet, self.state = mcp, "0x" + "0" * 40, _State()


_INPUTS = {"cohortRefreshHours": 12, "minRealizedUsd": 1e6, "cohortCap": 80, "pageSize": 500,
           "maxPages": 4, "stateBatch": 40, "tiltThreshold": 65, "deltaMin": 2,
           "goodConsensus": 10, "apexConsensus": 15, "maxSlots": 5, "recentSignalTtlSeconds": 21600,
           "leverageTiers": {"apex": 4, "good": 4, "base": 3},
           "marginPctTiers": {"apex": 12, "good": 11, "base": 10}, "maxLeverage": 4, "maxMarginPct": 20}


def test_the_cohort_filter_reads_realizedProfitAndLoss():
    """The two >= $1M wallets are in (page order), the $250k wallet is out — and a cold-start
    scan() carries that cohort into the persisted state (the first read seeds the headcount
    baseline) instead of 'no cohort'."""
    assert scan._refresh_cohort(_Ctx(_MCP()), _INPUTS) == [A1, A3]
    ctx = _Ctx(_MCP())
    assert scan.scan(dict(_INPUTS), ctx) == []                 # cold start: history only, no opens
    assert ctx.state.last()["cohort"] == [A1, A3]


def test_the_legacy_spellings_are_not_read():
    """Rows carrying only the old aliases read as $0 — those keys never existed on the live row."""
    ctx = _Ctx(_MCP(rows=[{"address": A1, "realizedPnl": 5e6},
                          {"address": A2, "realized_profit_and_loss": 5e6}]))
    assert scan._refresh_cohort(ctx, _INPUTS) == []
    tool, args = ctx.senpi_mcp.calls[0]
    assert (tool, args["time_frame"], args["sort_by"], args["offset"]) == (
        "discovery_get_top_traders", "ALL_TIME", "PROFIT_AND_LOSS_REALIZED", 0)
