"""Jaguar — the rank-jump is real on the REAL board shape.

leaderboard_get_markets arrives as {success, data: {markets: {markets: [...], source_trader_count, window,
timestamp}}}; each row is one (token, dex, direction) with pct_of_top_traders_gain, contribution_pct_change_*,
token_price_change_pct_*, day_notional_volume, trader_count, is_dominant_direction — and NO rank field. The
old snapshot read `rank` with a 999 default on both ticks, so every rank_jump was 0 and the detector never
fired. The rank is now derived: the row's position ordered by pct_of_top_traders_gain descending (1 = the
biggest share of the top traders' gains).

Fixture: a 40-row board trimmed to the fields the scanner reads (payload order is NOT share order).
Run: python3 -m pytest strategies/jaguar/tests -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402
import scan  # noqa: E402

ZERO = "0x" + "0" * 40


def _row(token, share, **kw):
    r = {"token": token, "dex": "", "direction": "long", "max_leverage": 20, "pct_of_top_traders_gain": share,
         "contribution_pct_change_15m": 0.0, "contribution_pct_change_1h": 0.0, "contribution_pct_change_4h": 0.0,
         "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": 0.2, "token_price_change_pct_4h": 0.5,
         "day_notional_volume": 25000000.0, "trader_count": 12, "is_dominant_direction": True}
    r.update(kw)
    return r


_ABOVE = [30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 10]                                    # 11 shares above 8.0
_MID = [7, 6, 5, 4, 3, 2, 1, 0.9, 0.8, 0.7, 0.6, 0.55, 0.52, 0.51, 0.5,
        0.49, 0.48, 0.47, 0.46, 0.45, 0.44, 0.43, 0.42]                                  # 23 shares in (0.4, 8)
_BELOW = [0.3, 0.2, 0.1, 0.05, 0.01]

_JUMP = dict(contribution_pct_change_15m=2.5, contribution_pct_change_1h=1.5, token_price_change_pct_4h=4.0,
             trader_count=35, day_notional_volume=50000000.0)


def _board(zec_share, **zec):
    rows = [_row(f"F{i:02d}", s) for i, s in enumerate(_ABOVE + _MID + _BELOW, 1)]
    rows.insert(20, _row("ZEC", zec_share, **zec))                                       # payload order != rank order
    return {"success": True, "data": {"markets": {"markets": rows, "source_trader_count": 100,
                                                  "window": "4h", "timestamp": 1789503749}}}


def _snap(board):
    return scoring.build_scan_snapshot(board["data"]["markets"]["markets"], "2026-09-15T20:00:00+00:00")


def test_rank_is_derived_from_the_share_order_the_board_sends():
    prev = _snap(_board(0.4))
    assert prev["markets"][0]["rank"] == 1 and prev["markets"][0]["contribution"] == 30.0   # biggest share = rank 1
    by = {m["token"]: m["rank"] for m in prev["markets"]}
    assert by["ZEC"] == 35 and by["F39"] == 40                                             # 1..N, never 999
    assert {m["token"]: m["rank"] for m in _snap(_board(8.0))["markets"]}["ZEC"] == 12


def test_detector_fires_on_the_derived_rank_jump():
    prev, cur = _snap(_board(0.4)), _snap(_board(8.0, **_JUMP))
    sigs = scoring.detect_striker_signals(cur, [prev])
    assert len(sigs) == 1 and sigs[0]["token"] == "ZEC" and sigs[0]["direction"] == "LONG"
    assert sigs[0]["rankJump"] == 23 and sigs[0]["currentRank"] == 12 and sigs[0]["isFirstJump"] is True
    assert sigs[0]["score"] == 14 and "FIRST_JUMP #35->#12" in sigs[0]["reasons"]
    assert scoring.detect_striker_signals(prev, [prev]) == []                              # same board -> no jump


class _State:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def recent(self, n):
        return self.rows[-n:]

    def last(self):
        return self.rows[-1] if self.rows else None


class _MCP:
    def __init__(self, boards):
        self.boards = list(boards)

    def call_tool(self, name, args):
        if name == "leaderboard_get_markets":
            return self.boards.pop(0)
        if name == "strategy_get_clearinghouse_state":
            return {"success": True, "data": {"main": {"assetPositions": []}, "xyz": {"assetPositions": []}}}
        raise AssertionError(f"unexpected read {name}")


class _Ctx:
    def __init__(self, boards):
        self.senpi_mcp = _MCP(boards)
        self.wallet = ZERO
        self.state = _State()


def test_scan_emits_across_two_ticks_from_the_real_envelope():
    ctx = _Ctx([_board(0.4), _board(8.0, **_JUMP)])
    assert scan.scan({}, ctx) == []                                                        # tick 1 seeds history
    out = scan.scan({}, ctx)
    assert len(out) == 1 and out[0]["asset"] == "ZEC" and out[0]["direction"] == "LONG"
    assert out[0]["leverage"] == 10 and out[0]["data"]["rankJump"] == 23 and out[0]["data"]["currentRank"] == 12
    assert ctx.state.rows[-1]["scan"]["markets"][0]["rank"] == 1                            # rank lives in the snapshot state


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
