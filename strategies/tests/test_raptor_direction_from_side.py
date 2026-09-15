"""Raptor: a followed position's direction comes from its side (signed size) — never from the sign of its PnL.

select_best_position read `direction` / `side`, else fell back to "LONG if delta_pnl >= 0 else SHORT": a
losing long read as SHORT, a winning short as LONG, and the board-agreement gate then compared the
wrong side. The fallback is now the sign of `size` (`szi`) — the field the documented `direction` is
derived from upstream; a row with neither is skipped, never guessed.

Payload shapes follow the tool guides: leaderboard_get_trader_positions -> data.positions.positions[]
with market / size (signed native units) / direction / notional_size / account_value / leverage /
delta_pnl; leaderboard_get_markets rows with token / dex / direction / pct_of_top_traders_gain /
trader_count / price + contribution changes; discovery_get_top_traders rows with address / tcsLabel /
unRealizedProfitAndLoss / realizedProfitAndLoss / returnOnInvestment.

Run: python3 -m pytest strategies/tests/test_raptor_direction_from_side.py -q
"""
import os
import sys

import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load():
    d = os.path.join(_ROOT, "raptor", "main", "scanners")
    sys.path.insert(0, d)
    try:
        for mod in ("scoring", "scan"):
            sys.modules.pop(mod, None)
        import scoring  # noqa: F401
        import scan
        return scan
    finally:
        sys.path.remove(d)


def _inputs():
    ry = yaml.safe_load(open(os.path.join(_ROOT, "raptor", "main", "runtime.yaml")))
    return dict(next(s for s in ry["scanners"] if s.get("type") == "external_scanner")["inputs"])


class _State:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def last(self):
        return self.rows[-1] if self.rows else None

    def recent(self, n):
        return self.rows[-n:]

    def __len__(self):
        return len(self.rows)


class _Mcp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name not in self.responses:
            raise AssertionError(f"unexpected tool call {name!r}")
        return self.responses[name]


class _Ctx:
    def __init__(self, responses):
        self.senpi_mcp = _Mcp(responses)
        self.wallet = "0x" + "0" * 40
        self.state = _State()
        self.scanner_name = "test"
        self.interval_seconds = 120
        self.dry_run = False


# ── payloads ──────────────────────────────────────────────────────────────────
TRADER = "0x" + "0" * 39 + "1"

TOP_TRADERS = {"success": True, "data": {"traders": [
    {"address": TRADER, "tcsLabel": "ELITE", "tcsValue": 88, "unRealizedProfitAndLoss": 820000.0,
     "realizedProfitAndLoss": 140000.0, "profitAndLoss": 960000.0, "returnOnInvestment": 63.4}]}}


def _positions(*rows):
    return {"success": True, "data": {"positions": {
        "trader_id": TRADER, "rank": 4, "total_delta_pnl": sum(r["delta_pnl"] for r in rows), "positions": list(rows)}}}


def _row(token, direction, pct, traders, p4h, p1h, c15m):
    return {"token": token, "dex": "", "direction": direction, "max_leverage": 25, "pct_of_top_traders_gain": pct,
            "contribution_pct_change_15m": c15m, "contribution_pct_change_1h": 0.9, "contribution_pct_change_4h": 1.6,
            "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": p1h, "token_price_change_pct_4h": p4h,
            "day_notional_volume": 6.0e8, "trader_count": traders, "is_dominant_direction": True}


# the dominant board row per token; the tape agrees with each side under test
MARKETS = {"success": True, "data": {"markets": [
    _row("ETH", "long", 9.5, 27, 1.4, 0.5, 0.9),
    _row("SOL", "short", 6.0, 14, -1.8, -0.6, 0.7),
], "source_trader_count": 100, "window": "4h", "timestamp": 1789000000}}

CLEARINGHOUSE = {"success": True, "data": {
    "main": {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0",
                               "totalRawUsd": "1000.0"}, "withdrawable": "1000.0", "assetPositions": []},
    "xyz": {"marginSummary": {"accountValue": "0.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0",
                              "totalRawUsd": "0.0"}, "withdrawable": "0.0", "assetPositions": []}}}

# a long that is under water in the 4h window (size > 0, delta_pnl < 0)
LOSING_LONG = {"market": "ETH", "size": 118.0, "notional_size": 531000.0, "account_value": 3900000.0,
               "leverage": 5, "delta_pnl": -212000.0}
# a short that is paying (size < 0, delta_pnl > 0)
WINNING_SHORT = {"market": "SOL", "size": -8200.0, "notional_size": 1804000.0, "account_value": 3900000.0,
                 "leverage": 5, "delta_pnl": 305000.0}


def _ctx(*rows):
    return _Ctx({"discovery_get_top_traders": TOP_TRADERS, "leaderboard_get_markets": MARKETS,
                 "strategy_get_clearinghouse_state": CLEARINGHOUSE,
                 "leaderboard_get_trader_positions": _positions(*rows)})


def test_losing_long_is_followed_long():
    """Sign of size says long; sign of PnL used to say SHORT, which the LONG board row then rejected."""
    (sig,) = _load().scan(_inputs(), _ctx(LOSING_LONG))
    assert (sig["asset"], sig["direction"], sig["data"]["direction"]) == ("ETH", "LONG", "LONG")
    assert sig["data"]["positionDeltaPnl"] < 0


def test_winning_short_is_followed_short():
    """Sign of size says short; sign of PnL used to say LONG."""
    (sig,) = _load().scan(_inputs(), _ctx(WINNING_SHORT))
    assert (sig["asset"], sig["direction"], sig["data"]["direction"]) == ("SOL", "SHORT", "SHORT")
    assert sig["data"]["positionDeltaPnl"] > 0


def test_documented_direction_field_is_read_as_is():
    """The guide's `direction` ("long"/"short", derived from size upstream) is the first source."""
    (sig,) = _load().scan(_inputs(), _ctx(dict(LOSING_LONG, direction="long")))
    assert sig["direction"] == "LONG" and sig["data"]["positionDeltaPnl"] < 0
    assert _load().scan(_inputs(), _ctx({"market": "ETH", "delta_pnl": -212000.0, "leverage": 5})) == []  # no side: skipped
