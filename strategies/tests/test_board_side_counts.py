"""Board reads that summed trader_count across both sides and took the 15m velocity from the
last row regardless of side.

`leaderboard_get_markets` returns one row per (token, dex, direction): `trader_count` is the
headcount on THAT side and `contribution_pct_change_15m` is THAT row's velocity. grizzly and
kodiak added the LONG and SHORT headcounts together (so the 58/42 dead band and the
DOMINANT/STRONG tiers hinged on a two-sided total) and kept whichever side's 15m velocity the
feed listed last. The read now carries the returned side's own headcount and velocity.

The fixture is the documented response shape (fixtures/leaderboard_get_markets.json): BTC and
SOL each carry a dominant LONG row followed by a minor SHORT row whose 15m velocity has the
opposite sign, so a summed count or a last-row velocity is visible in the result. No main-dex/xyz
twin instrument exists on the live instrument list today; grizzly's venue control uses a
synthetic main-dex GOLD row beside the xyz GOLD rows.

Run:
  python3 -m pytest strategies/tests/test_board_side_counts.py -q
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


def _board(last=()):
    doc = copy.deepcopy(json.load(open(_FIXTURE, encoding="utf-8")))
    doc["data"]["markets"] = doc["data"]["markets"] + list(last)
    return doc


class _FakeMcp:
    def __init__(self, responses):
        self.responses = responses

    def call_tool(self, name, args):
        if name not in self.responses:
            raise AssertionError(f"unexpected tool call {name!r}")
        return self.responses[name]


class _FakeCtx:
    def __init__(self, responses):
        self.senpi_mcp = _FakeMcp(responses)
        self.wallet = "0x" + "ab" * 20
        self.state = None


# ── grizzly (BTC): LONG 5.52% / 112 traders / 15m -6.84 dominant; SHORT 0.91% / 17 / +0.35 last ──

def test_grizzly_reads_the_side_it_returns_not_the_two_sided_total():
    scan = _load("grizzly")
    sm = scan._sm_for_asset(_FakeCtx({"leaderboard_get_markets": _board()}), "BTC")

    assert sm["direction"] == "LONG"
    assert sm["traders"] == 112, "headcount was summed across LONG and SHORT (112 + 17)"
    assert sm["cc_15m"] == pytest.approx(-6.84), "15m velocity came from the last row (SHORT)"


def test_grizzly_keeps_main_and_xyz_rows_apart():
    scan = _load("grizzly")
    ctx = _FakeCtx({"leaderboard_get_markets": _board(last=[_MAIN_GOLD_TWIN])})

    xyz = scan._sm_for_asset(ctx, "xyz:GOLD")     # xyz rows only: SHORT 2.20 / 18 dominant
    assert (xyz["direction"], xyz["traders"]) == ("SHORT", 18)
    main = scan._sm_for_asset(ctx, "GOLD")        # the main-dex row only
    assert (main["direction"], main["traders"]) == ("LONG", 22)


# ── kodiak (SOL): LONG 3.60% / 69 traders / 15m +2.14 dominant; SHORT 1.30% / 26 / -0.40 last ──

def test_kodiak_reads_the_side_it_returns_not_the_two_sided_total():
    scan = _load("kodiak")
    sm = scan._sm_for_asset(_FakeCtx({"leaderboard_get_markets": _board()}), "SOL")

    assert sm["direction"] == "LONG"
    assert sm["traders"] == 69, "headcount was summed across LONG and SHORT (69 + 26)"
    assert sm["cc_15m"] == pytest.approx(2.14), "15m velocity came from the last row (SHORT)"


def test_kodiak_tiers_hinge_on_the_side_headcount():
    """A 40-trader LONG side beside a 15-trader SHORT side is SM_STRONG (>=30), not SM_DOMINANT
    (>=50): the tier reads the side's own headcount, not the 55-trader two-sided total."""
    scan = _load("kodiak")
    board = _board()
    for row in board["data"]["markets"]:
        if row["token"] == "SOL":
            row["trader_count"] = 40 if row["direction"] == "long" else 15
    sm = scan._sm_for_asset(_FakeCtx({"leaderboard_get_markets": board}), "SOL")

    assert sm["traders"] == 40
    assert sm["pct"] == pytest.approx(3.60 / 4.90 * 100)   # positioning ratio, unchanged


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
