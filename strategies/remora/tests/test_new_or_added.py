"""Remora mirrored what whales HOLD, so every cooldown it re-entered the same standing position.

Each tick it took each whale's largest position and mirrored the consensus, so a $25-275M HYPE or ETH
short that had been open for days qualified again the moment the 8h per-asset cooldown expired: 77 of
111 opens re-entered a short it had already mirrored, and those re-entries were 76% of the loss. It
also never mirrors an exit, so nothing takes the position off on the whale's own close.

A whale's book is now diffed tick over tick: only a position they just opened, or added to by
minWhaleAddPct, is a signal. Sizes are |szi| so a price move is not mistaken for an add, and a failed
read keeps the previous book instead of replaying it as new.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

W = "0x" + "b" * 40


def _p(asset="HYPE", size=-1000.0, entry=40.0):
    return {"market": asset, "size": size, "entry_price": entry,
            "notional_size": abs(size) * entry, "direction": "SHORT" if size < 0 else "LONG"}


def test_a_book_snapshot_keys_side_and_size():
    snap = scoring.book_snapshot([_p(), _p(asset="ETH", size=500.0, entry=2400.0)])
    assert snap == {"HYPE|SHORT": 1000.0, "ETH|LONG": 500.0}


def test_only_a_new_or_added_position_is_a_signal():
    held = [_p()]
    assert scoring.new_or_added(held, None, 20) == []                       # first sight seeds
    assert scoring.new_or_added(held, {"HYPE|SHORT": 1000.0}, 20) == []     # still holding it
    assert scoring.new_or_added(held, {"ETH|LONG": 3.0}, 20) == held        # newly opened
    assert scoring.new_or_added(held, {"HYPE|SHORT": 700.0}, 20) == held    # added 43%
    assert scoring.new_or_added(held, {"HYPE|SHORT": 950.0}, 20) == []      # 5% — noise, not an add


class _State:
    def __init__(self, last=None): self._l = [last] if last else []
    def __len__(self): return len(self._l)
    def last(self): return self._l[-1] if self._l else None
    def append(self, d): self._l.append(d)


class _MCP:
    def __init__(self, positions, fail=False): self.positions, self.fail = positions, fail

    def call_tool(self, tool, args):
        if tool == "leaderboard_get_trader_positions":
            if self.fail:
                raise RuntimeError("upstream 500")
            return {"data": {"positions": {"positions": self.positions}}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"marginSummary": {"accountValue": "1000"}, "assetPositions": []}}}
        return {}


def _run(positions, books, fail=False):
    st = _State({"signaled": {}, "cohort": {}, "books": books})
    ctx = types.SimpleNamespace(state=st, wallet="0xw", senpi_mcp=_MCP(positions, fail))
    out = scan.scan({"whales": [W], "useWhaleQuality": False, "minScore": 0}, ctx)
    return out, st.last()


def test_a_position_the_whale_merely_still_holds_is_not_mirrored():
    out, rec = _run([_p()], books={W: {"HYPE|SHORT": 1000.0}})
    assert out == []
    assert rec["books"][W] == {"HYPE|SHORT": 1000.0}


def test_the_first_sight_of_a_whale_seeds_their_book():
    out, rec = _run([_p()], books={})
    assert out == [] and rec["books"][W] == {"HYPE|SHORT": 1000.0}


def test_an_add_is_mirrored():
    out, _ = _run([_p(size=-1500.0)], books={W: {"HYPE|SHORT": 1000.0}})
    assert [(s["asset"], s["direction"]) for s in out] == [("HYPE", "SHORT")]


def test_a_failed_read_keeps_the_book_it_had():
    out, rec = _run([_p()], books={W: {"HYPE|SHORT": 1000.0}}, fail=True)
    assert out == []
    assert rec["books"][W] == {"HYPE|SHORT": 1000.0}      # not wiped, so the next tick still diffs
