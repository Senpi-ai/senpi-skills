"""Board reads keyed by bare token: the LAST row per token won, not the dominant side.

`leaderboard_get_markets` returns one row per (token, dex, direction): a LONG row and a SHORT
row can both exist for the same token, `is_dominant_direction` marks the larger side, and an
xyz-dex row carries a BARE token plus `dex: "xyz"` (never a prefixed name). condor, lemon and
kestrel built `sm_map[token]` straight from the row stream, so whichever side the feed listed
last replaced the dominant one — and condor's xyz ban tested for a `XYZ:` prefix the board
never sends, so xyz rows leaked into a crypto-only map.

The fixture is the documented response shape (fixtures/leaderboard_get_markets.json): both
sides present for several tokens in BOTH orders (minor row last, dominant row last), xyz rows
as bare token + dex, and single-sided tokens. No main-dex/xyz twin instrument exists on the
live instrument list today, so the venue needle uses a synthetic main-dex GOLD row inserted
around the xyz GOLD rows.

Run:
  python3 -m pytest strategies/tests/test_board_last_row_overwrite.py -q
"""
import copy
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "leaderboard_get_markets.json")

# A main-dex row with the same bare token as the xyz GOLD rows (synthetic; see module docstring).
_MAIN_GOLD_TWIN = {
    "token": "GOLD", "dex": "", "direction": "long", "max_leverage": 5,
    "pct_of_top_traders_gain": 1.75, "contribution_pct_change_15m": 0.90,
    "contribution_pct_change_1h": 0.40, "contribution_pct_change_4h": None,
    "token_price_change_pct_15m": 0.05, "token_price_change_pct_1h": 0.20,
    "token_price_change_pct_4h": 0.70, "day_notional_volume": 1234567.8,
    "trader_count": 22, "is_dominant_direction": True,
}


def _load(pkg, instance="main"):
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


def _board(first=(), last=()):
    doc = copy.deepcopy(json.load(open(_FIXTURE, encoding="utf-8")))
    doc["data"]["markets"] = list(first) + doc["data"]["markets"] + list(last)
    return doc


class _FakeState:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def last(self):
        return self.rows[-1] if self.rows else None

    def recent(self, n):
        return self.rows[-n:]


class _FakeMcp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name not in self.responses:
            raise AssertionError(f"unexpected tool call {name!r}")
        return self.responses[name]


class _FakeCtx:
    def __init__(self, responses, wallet="0x" + "ab" * 20):
        self.senpi_mcp = _FakeMcp(responses)
        self.wallet = wallet
        self.state = _FakeState()


# ── condor: follows the dominant side, crypto only ──

def test_condor_keeps_the_dominant_side_whichever_row_comes_last():
    scan = _load("condor")
    sm_map = scan.fetch_sm_map(_FakeCtx({"leaderboard_get_markets": _board()}), {})

    # minor SHORT row listed after the dominant LONG row
    assert sm_map["BTC"]["direction"] == "LONG"
    assert sm_map["BTC"]["traders"] == 112
    # dominant SHORT row listed after the minor LONG row
    assert sm_map["ETH"]["direction"] == "SHORT"
    assert sm_map["ETH"]["traders"] == 114


def test_condor_bans_xyz_rows_by_dex_so_a_main_dex_name_keeps_its_own_row():
    scan = _load("condor")
    board = _board(first=[_MAIN_GOLD_TWIN])   # xyz GOLD rows follow the main-dex row
    sm_map = scan.fetch_sm_map(_FakeCtx({"leaderboard_get_markets": board}), {})

    assert "NVDA" not in sm_map, "an xyz row (bare token + dex='xyz') leaked into the crypto map"
    assert (sm_map["GOLD"]["direction"], sm_map["GOLD"]["traders"]) == ("LONG", 22), \
        "the xyz GOLD rows overwrote the main-dex GOLD row"
    assert "DOGE" in sm_map, "a single-sided token must survive the dominant-side filter"


# ── kestrel: follows the dominant side, xyz only ──

def test_kestrel_keeps_the_dominant_xyz_side_whichever_row_comes_last():
    scan = _load("kestrel")
    sm_map = scan._fetch_sm_xyz_map(_FakeCtx({"leaderboard_get_markets": _board()}))

    # minor SHORT row listed after the dominant LONG row
    assert sm_map["NVDA"]["direction"] == "long"
    assert sm_map["NVDA"]["trader_count"] == 31
    # dominant SHORT row listed after the minor LONG row
    assert sm_map["GOLD"]["direction"] == "short"
    assert sm_map["GOLD"]["trader_count"] == 18


def test_kestrel_reads_only_the_xyz_venue_it_trades():
    scan = _load("kestrel")
    board = _board(last=[_MAIN_GOLD_TWIN])    # main-dex GOLD row listed after the xyz rows
    sm_map = scan._fetch_sm_xyz_map(_FakeCtx({"leaderboard_get_markets": board}))

    assert (sm_map["GOLD"]["dex"], sm_map["GOLD"]["direction"]) == ("xyz", "short"), \
        "a main-dex row with the same bare token replaced the xyz row"
    assert "BTC" not in sm_map and "SP500" in sm_map


# ── lemon: fades the dominant (crowded) side, on the venue each basket name trades ──

_LEMON_TRACKED = ["BTC", "ETH", "SOL", "HYPE", "xyz:GOLD", "xyz:SP500"]


def test_lemon_fades_the_dominant_side_whichever_row_comes_last():
    scan = _load("lemon")
    sm_map = scan._fetch_sm_map(_FakeCtx({"leaderboard_get_markets": _board()}),
                                100, _LEMON_TRACKED, False)

    # minor SHORT row listed after the dominant LONG row
    assert sm_map["BTC"]["direction"] == "LONG"
    assert sm_map["BTC"]["traders"] == 112
    # dominant SHORT row listed after the minor LONG row
    assert sm_map["ETH"]["direction"] == "SHORT"
    assert sm_map["ETH"]["traders"] == 114


def test_lemon_scans_the_venue_the_basket_trades():
    """End to end through scan(): the basket's GOLD is xyz:GOLD, so the xyz row is the one
    scanned (its funding read carries dex='xyz') and a main-dex GOLD row is never a candidate."""
    scan = _load("lemon")
    ctx = _FakeCtx({
        "strategy_get_clearinghouse_state": {"data": {"main": {
            "marginSummary": {"accountValue": "1000", "totalMarginUsed": "0"},
            "assetPositions": []}}},
        "leaderboard_get_markets": _board(last=[_MAIN_GOLD_TWIN]),
        "market_get_asset_data": {"data": {"asset_context": {"funding": 0.0001}}},
    })
    scan.scan({"minScore": 0}, ctx)

    funding_reads = {a["asset"]: a["dex"] for n, a in ctx.senpi_mcp.calls
                     if n == "market_get_asset_data"}
    assert funding_reads.get("xyz:GOLD") == "xyz", "the xyz GOLD row was not the one scanned"
    assert "GOLD" not in funding_reads, "a main-dex GOLD row stood in for the basket's xyz:GOLD"
    # every basket name on the board (DOGE is in the default basket), nothing else
    assert set(funding_reads) == {"BTC", "ETH", "SOL", "HYPE", "DOGE", "xyz:GOLD", "xyz:SP500"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
