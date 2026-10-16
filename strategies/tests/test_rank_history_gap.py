"""A rank jump is measured between CONSECUTIVE scans — never across a gap.

orca, penguin, pelican and cheetah keep a rolling rank history and score a coin by how far it moved
since `scan_history[-1]`. Their early returns (max positions, no account, no markets) record no
snapshot, so while a position is held the history stops advancing. The first free tick then compared
the live board against a snapshot that could be hours old, and a slow climb read as an
IMMEDIATE_MOVER. The fix stamps each snapshot (`history_ts`) and, when more than one tick was missed
(gap > 2.5 x `ctx.interval_seconds`), drops the history and re-seeds instead of scoring.

Run:
  python3 -m pytest strategies/tests/test_rank_history_gap.py -q
"""
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load(pkg):
    """Import a package's scanner modules with its own scanners/ dir first on sys.path."""
    d = os.path.join(_ROOT, pkg, "main", "scanners")
    sys.path.insert(0, d)
    try:
        for mod in ("scoring", "scan"):
            sys.modules.pop(mod, None)
        import scoring  # noqa: F401
        import scan
        return scan
    finally:
        sys.path.remove(d)


def _row(token, pct):
    return {"token": token, "dex": "", "direction": "long", "pct_of_top_traders_gain": pct,
            "contribution_pct_change_15m": 1.0, "token_price_change_pct_4h": 2.5,
            "token_price_change_pct_1h": 0.6, "trader_count": 30, "is_dominant_direction": True}


def _board(zro_rank, zro_pct):
    """60 steady filler rows with ZRO slotted in at `zro_rank` (rank = board index + 1)."""
    rows = [_row(f"F{i:02d}", 1.0) for i in range(59)]
    rows.insert(zro_rank - 1, _row("ZRO", zro_pct))
    return {"data": {"markets": {"markets": rows}}}


_FLAT = {"data": {"main": {"marginSummary": {"accountValue": "1000"}, "assetPositions": []}}}
_HELD = {"data": {"main": {"marginSummary": {"accountValue": "1000"},
                           "assetPositions": [{"position": {"coin": "ETH", "szi": "1"}}]}}}


class _State:
    def __init__(self):
        self.rows = []

    def last(self):
        return self.rows[-1] if self.rows else None

    def append(self, row):
        self.rows.append(row)


class _Mcp:
    def __init__(self):
        self.responses = {}

    def call_tool(self, name, args):
        if name not in self.responses:
            raise LookupError(name)   # the scanners' guarded _read turns this into a degraded read
        return self.responses[name]


class _Ctx:
    def __init__(self, interval):
        self.senpi_mcp = _Mcp()
        self.wallet = "0x" + "ab" * 20
        self.state = _State()
        self.interval_seconds = interval


def _tick(scan, ctx, account, board):
    ctx.senpi_mcp.responses = {"strategy_get_clearinghouse_state": account,
                               "leaderboard_get_markets": board}
    return scan.scan({"maxPositions": 1}, ctx)


ORCA_FAMILY = ["orca", "penguin", "pelican"]


@pytest.mark.parametrize("pkg", ORCA_FAMILY)
def test_consecutive_scan_still_scores_the_jump(pkg):
    scan, ctx = _load(pkg), _Ctx(interval=90)
    assert _tick(scan, ctx, _FLAT, _board(47, 0.1)) == []            # seeds the history
    ctx.state.rows[-1]["history_ts"] -= 90                           # one normal tick later
    out = _tick(scan, ctx, _FLAT, _board(24, 0.5))                   # ZRO 47 -> 24 in one scan
    assert [o["asset"] for o in out] == ["ZRO"]


@pytest.mark.parametrize("pkg", ORCA_FAMILY)
def test_held_position_gap_reseeds_instead_of_scoring(pkg):
    scan, ctx = _load(pkg), _Ctx(interval=90)
    _tick(scan, ctx, _FLAT, _board(47, 0.1))                         # seeds the history
    _tick(scan, ctx, _HELD, _board(30, 0.3))                         # at max positions: no fetch
    held, seeded = ctx.state.rows[-1], ctx.state.rows[-2]
    assert held["history_ts"] == seeded["history_ts"]                # early return keeps the old stamp
    held["history_ts"] -= 3600                                       # ...an hour of holding later
    out = _tick(scan, ctx, _FLAT, _board(24, 0.5))                   # same board as the jump above
    assert out == []
    assert ctx.state.rows[-1]["result"]["gate"] == "history_seed"
    assert len(ctx.state.rows[-1]["scan_history"]) == 1              # stale snapshots dropped


def test_cheetah_measures_rank_climb_only_across_one_tick():
    scan, ctx = _load("cheetah"), _Ctx(interval=300)                  # cheetah runs at 300s
    _tick(scan, ctx, _FLAT, _board(47, 0.1))
    ctx.state.rows[-1]["history_ts"] -= 300                          # consecutive: keep history
    _tick(scan, ctx, _FLAT, _board(24, 0.5))
    assert len(ctx.state.rows[-1]["scan_history"]) == 2
    ctx.state.rows[-1]["history_ts"] -= 3600                         # gap: drop it
    _tick(scan, ctx, _FLAT, _board(20, 0.6))
    assert len(ctx.state.rows[-1]["scan_history"]) == 1
