"""Remora: the whale-quality tier is read from the row that carries it.

The bonus read called discovery_get_trader_state with `trader_id` (the tool takes
`trader_addresses`) and then looked for `tier` on a payload that carries no tier at all, so
the ELITE/RELIABLE +1 never fired: a single whale's top conviction (score 3) could never clear
minScore 4 and only >= 2-whale coincidences traded. The tier lives on the
discovery_get_top_traders row (`tcsLabel`) the cohort is already built from. Fixtures are the
live row shapes trimmed to the fields read; addresses are zeroed.
Run: python3 -m pytest strategies/remora/tests/test_tier_read.py -q"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402

ELITE = "0x" + "0" * 39 + "1"
STREAKY = "0x" + "0" * 39 + "2"

# discovery_get_top_traders(time_frame=ALL_TIME, sort_by=PROFIT_AND_LOSS_REALIZED) — live envelope
_TOP_TRADERS = {"success": True, "data": {"traders": [
    {"address": ELITE, "shortAddress": "0x0000...0001", "returnOnInvestment": 62.0,
     "profitAndLoss": 1700000, "unRealizedProfitAndLoss": 200000,
     "realizedProfitAndLoss": 1500000, "winRate": 58.0, "traderAgeSeconds": 7776000,
     "tcsLabel": "ELITE", "tcsValue": 88, "riskLabel": "BALANCED"},
    {"address": STREAKY, "shortAddress": "0x0000...0002", "returnOnInvestment": 30.0,
     "profitAndLoss": 950000, "unRealizedProfitAndLoss": 50000,
     "realizedProfitAndLoss": 900000, "winRate": 47.0, "traderAgeSeconds": 2592000,
     "tcsLabel": "STREAKY", "tcsValue": 40, "riskLabel": "AGGRESSIVE"},
]}}


def _positions(market, size, px):
    """leaderboard_get_trader_positions — the observed nested data.positions.positions shape."""
    return {"success": True, "data": {"positions": {"positions": [
        {"market": market, "size": size, "direction": "long" if size >= 0 else "short",
         "entry_price": px, "notional_size": abs(size) * px, "account_value": 250000,
         "leverage": 5, "delta_pnl": 1200.5}]}}}


class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)
    def __len__(self): return len(self._log)


class _MCP:
    def __init__(self): self.calls = []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "discovery_get_top_traders":
            return _TOP_TRADERS if not args.get("offset") else {"success": True, "data": {"traders": []}}
        if tool == "leaderboard_get_trader_positions":
            return _positions("BTC", 1.0, 60000) if args["trader_id"] == ELITE else _positions("ETH", -20.0, 3000)
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0"},
                             "assetPositions": []}}
        return {}


class _Ctx:
    def __init__(self, mcp):
        self.senpi_mcp, self.wallet, self.state = mcp, "0x" + "0" * 40, _State()


_INPUTS = {"whales": [], "cohortSize": 10, "cohortRefreshHours": 24, "cohortMinRealizedUsd": 0,
           "cohortFetchLimit": 1000, "cohortMaxPages": 6, "useWhaleQuality": True,
           "minNotionalUsd": 5000, "minScore": 4, "emitTopN": 2, "marginPct": 15, "leverage": 4,
           "recentSignalTtlSeconds": 240}


def test_the_cohort_build_keeps_each_rows_tcsLabel():
    cohort = scan._build_cohort(_Ctx(_MCP()), {}, _INPUTS, now=1000.0)
    assert cohort["whales"] == [ELITE, STREAKY]                                   # ranked by realized, desc
    assert (cohort["tiers"], cohort["cache_version"]) == ({ELITE: "ELITE", STREAKY: "STREAKY"},
                                                           scoring.COHORT_CACHE_VERSION)


def test_one_elite_whale_now_clears_min_score():
    """3 (a whale's top conviction) + 1 (ELITE) = 4 = minScore: a single elite whale trades; the
    STREAKY whale's solo ETH short stays at 3 and does not. No trader_state read is made."""
    ctx = _Ctx(_MCP())
    out = scan.scan(dict(_INPUTS), ctx)
    assert [(s["asset"], s["direction"], s["data"]["score"], s["data"]["eliteTier"]) for s in out] \
        == [("BTC", "LONG", 4, True)]
    assert {t for t, _ in ctx.senpi_mcp.calls if t.startswith("discovery_")} == {"discovery_get_top_traders"}
