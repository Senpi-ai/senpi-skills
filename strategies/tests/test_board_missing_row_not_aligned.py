"""Lemur + Magpie (pre_listing): a name with NO leaderboard_get_markets row is neutral — never "assumed aligned".

Both books trade pre-IPO perpetuals, which the 4h top-trader board rarely holds, so a missing row is
the normal case. build_thesis treated it as agreement: it awarded the +2 smart-money points (reason
`..._assumed_aligned`), so the 4h trend alone reached minScore and the smart-money component never
gated anything. A missing row now scores 0 on that component; a present row still gates and scores
exactly as before (no threshold changes).

Payload shapes follow the tool guides: leaderboard_get_markets rows carry token / dex / direction /
pct_of_top_traders_gain / trader_count / is_dominant_direction; market_list_instruments rows carry
name / max_leverage / is_delisted / context.funding / context.dayNtlVlm; candles are o/h/l/c/v strings.

Run: python3 -m pytest strategies/tests/test_board_missing_row_not_aligned.py -q
"""
import os
import sys

import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load(pkg, instance):
    """Import a package's scanner modules with its own scanners/ dir first on sys.path."""
    d = os.path.join(_ROOT, pkg, instance, "scanners")
    sys.path.insert(0, d)
    try:
        for mod in ("scoring", "scan"):
            sys.modules.pop(mod, None)
        import scoring  # noqa: F401
        import scan
        return scan
    finally:
        sys.path.remove(d)


def _inputs(pkg, instance):
    """The instance's real runtime.yaml inputs (minScore 5, smTiltMinPct 55, ...)."""
    ry = yaml.safe_load(open(os.path.join(_ROOT, pkg, instance, "runtime.yaml")))
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
        self.interval_seconds = 60
        self.dry_run = False


# ── payloads ──────────────────────────────────────────────────────────────────
IPOP = "xyz:SPCX"   # the one live pre-IPO perpetual: tiny funding, 5x cap, over the volume floor

INSTRUMENTS = {"success": True, "data": {"instruments": [
    {"name": IPOP, "dex": "xyz", "max_leverage": 5, "is_delisted": False, "only_isolated": True,
     "context": {"funding": "0.00000001", "dayNtlVlm": "1850000", "markPx": "104.2", "prevDayPx": "101.9"}},
    {"name": "xyz:TSLA", "dex": "xyz", "max_leverage": 20, "is_delisted": False, "only_isolated": False,
     "context": {"funding": "0.0000125", "dayNtlVlm": "41000000", "markPx": "412.5", "prevDayPx": "408.1"}},
]}}


def _candles(lows, highs):
    return [{"t": 1789000000000 + i * 3600000, "T": 1789000000000 + (i + 1) * 3600000 - 1,
             "o": str(lo + 0.5), "h": str(hi), "l": str(lo), "c": str(hi - 0.5), "v": "1200", "n": 40}
            for i, (lo, hi) in enumerate(zip(lows, highs))]


BULL = _candles([100, 101, 102, 103, 104, 105], [102, 103, 104, 105, 106, 107])   # 5/5 higher lows -> BULLISH
FLAT = _candles([100, 99, 100, 99, 100, 99], [102, 103, 102, 103, 102, 103])       # 2/5 either way -> NEUTRAL


def _asset_data(c1, c4):
    return {"success": True, "data": {"asset": IPOP, "candles": {"1h": c1, "4h": c4}}}


def _row(token, dex, direction, pct, traders, dominant):
    return {"token": token, "dex": dex, "direction": direction, "max_leverage": 5 if dex == "xyz" else 40,
            "pct_of_top_traders_gain": pct, "contribution_pct_change_15m": 0.4,
            "contribution_pct_change_1h": 0.9, "contribution_pct_change_4h": 1.6,
            "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": 0.5,
            "token_price_change_pct_4h": 1.3, "day_notional_volume": 1.0e8, "trader_count": traders,
            "is_dominant_direction": dominant}


def _markets(*rows):
    return {"success": True, "data": {"markets": list(rows), "source_trader_count": 100,
                                      "window": "4h", "timestamp": 1789000000}}


# what the 4h board normally looks like for a pre-IPO name: rows for the majors, none for it
OTHER_ROWS = (_row("BTC", "", "long", 38.2, 41, True), _row("BTC", "", "short", 0.9, 3, False),
              _row("ETH", "", "long", 17.5, 26, True))
# 6% of the board's gains long vs 1% short -> tilt 85.71% (>= 55 aligned, >= 70 strong)
ALIGNED_ROWS = OTHER_ROWS + (_row("SPCX", "xyz", "long", 6.0, 12, True), _row("SPCX", "xyz", "short", 1.0, 2, False))
OPPOSED_ROWS = OTHER_ROWS + (_row("SPCX", "xyz", "short", 6.0, 12, True), _row("SPCX", "xyz", "long", 1.0, 2, False))

CLEARINGHOUSE = {"success": True, "data": {
    "main": {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0",
                               "totalRawUsd": "1000.0"}, "withdrawable": "1000.0", "assetPositions": []},
    "xyz": {"marginSummary": {"accountValue": "0.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0",
                              "totalRawUsd": "0.0"}, "withdrawable": "0.0", "assetPositions": []}}}


def _ctx(rows, c1, c4):
    return _Ctx({"strategy_get_clearinghouse_state": CLEARINGHOUSE, "market_list_instruments": INSTRUMENTS,
                 "market_get_asset_data": _asset_data(c1, c4), "leaderboard_get_markets": _markets(*rows)})


# ── lemur ─────────────────────────────────────────────────────────────────────
def test_lemur_missing_board_row_scores_no_smart_money_points():
    scan = _load("lemur", "main")
    inputs = _inputs("lemur", "main")
    # 4h trend only (1h neutral) = 3 points: this used to reach minScore 5 on the assumed +2
    assert scan.scan(inputs, _ctx(OTHER_ROWS, FLAT, BULL)) == []
    # 4h + 1h trend = 5 points, emitted on the trend alone: the missing row adds nothing and blocks nothing
    (sig,) = scan.scan(inputs, _ctx(OTHER_ROWS, BULL, BULL))
    assert (sig["asset"], sig["direction"], sig["data"]["score"]) == (IPOP, "LONG", 5)
    assert sig["data"]["smDirection"] == "NONE" and sig["data"]["smTiltPct"] == 0.0
    assert not [r for r in sig["data"]["reasons"] if "aligned" in r]


def test_lemur_present_board_row_still_gates_and_scores():
    scan = _load("lemur", "main")
    inputs = _inputs("lemur", "main")
    (sig,) = scan.scan(inputs, _ctx(ALIGNED_ROWS, BULL, BULL))
    assert sig["data"]["score"] == 8 and sig["data"]["smTiltPct"] == 85.71
    assert {"sm_aligned_86%", "sm_strongly_tilted"} <= set(sig["data"]["reasons"])
    assert scan.scan(inputs, _ctx(OPPOSED_ROWS, BULL, BULL)) == []   # a present row that disagrees still blocks


# ── magpie / pre_listing ──────────────────────────────────────────────────────
def test_magpie_pre_listing_missing_board_row_scores_no_smart_money_points():
    scan = _load("magpie", "pre_listing")
    inputs = _inputs("magpie", "pre_listing")
    assert scan.scan(inputs, _ctx(OTHER_ROWS, FLAT, BULL)) == []
    (sig,) = scan.scan(inputs, _ctx(OTHER_ROWS, BULL, BULL))
    assert (sig["asset"], sig["direction"], sig["data"]["score"]) == (IPOP, "LONG", 5)
    assert sig["data"]["smTilt"] == 0.0 and not [r for r in sig["data"]["reasons"] if "aligned" in r]


def test_magpie_pre_listing_present_board_row_still_gates_and_scores():
    scan = _load("magpie", "pre_listing")
    inputs = _inputs("magpie", "pre_listing")
    (sig,) = scan.scan(inputs, _ctx(ALIGNED_ROWS, BULL, BULL))
    assert sig["data"]["score"] == 8 and sig["data"]["smTilt"] == 85.7
    assert {"sm_aligned_86%", "sm_strong"} <= set(sig["data"]["reasons"])
    assert scan.scan(inputs, _ctx(OPPOSED_ROWS, BULL, BULL)) == []
