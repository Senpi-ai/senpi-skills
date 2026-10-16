"""Shadow mirrored positions that were neither fresh nor near the trader's fill.

Three reads were wrong against the live discovery_get_trader_state shape (read Sep 17: each open
position carries coin, szi, entryPx, positionValue, leverage{value}, startTime, durationInSeconds —
and NO mark price):

1. A failed trader read emptied that trader's seen-set, so the next tick replayed their whole
   standing book as fresh opens. 46 of 142 opens copied positions whose entry was more than 2% away
   (up to 65%), 38 of them in same-second multi-coin bursts.
2. The entry-slippage guard never ran: it needed `markPx`, which the response does not carry, so the
   chase was 0.0 on all 1,208 decisions. positionValue / |szi| IS the current mark.
3. Position age was requested (include_position_age) and never read, so a position opened hours ago
   counted as a fresh entry.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

A = "0x" + "a" * 40


def _pos(coin="BTC", szi=0.01, entry=100.0, value=1.0, age=60, lev=5):   # value = |szi| x mark
    p = {"coin": coin, "szi": str(szi), "entryPx": str(entry), "positionValue": str(value),
         "leverage": {"type": "cross", "value": lev}}
    if age is not None:                                  # age=None → the response carries neither field
        p["durationInSeconds"] = age
        p["startTime"] = 1789000000
    return p


def _state(positions):
    return {"address": A, "openPositions": positions}


def test_the_mark_comes_from_position_value_over_size():
    p = scoring.extract_positions(_state([_pos(szi=0.01, entry=100.0, value=1.03)]))[0]
    assert abs(p["mark"] - 103.0) < 1e-9                 # 1.03 / 0.01
    assert scoring.chase_pct(p["entry"], p["mark"], "LONG") == 3.0
    assert p["age_seconds"] == 60


def test_a_position_that_ran_either_way_is_not_chased():
    assert scoring.chase_within(3.0, 1.5) is False       # ran 3% past their fill
    assert scoring.chase_within(-3.0, 1.5) is False      # 3% underwater on their fill already
    assert scoring.chase_within(-1.2, 1.5) is True
    assert scoring.chase_within(0.0, 1.5) is True


class _State:
    def __init__(self, last=None): self._l = [last] if last else []
    def last(self): return self._l[-1] if self._l else None
    def append(self, d): self._l.append(d)


class _MCP:
    def __init__(self, positions, fail_state=False):
        self.positions, self.fail_state, self.calls = positions, fail_state, []

    def call_tool(self, tool, args):
        self.calls.append(tool)
        if tool == "discovery_get_trader_state":
            if self.fail_state:
                raise RuntimeError("upstream 500")
            return {"data": {"traders": [_state(self.positions)]}}
        return {}


def _run(positions, seen, fail_state=False, inputs=None):
    mcp = _MCP(positions, fail_state)
    st = _State({"cohort": {"refreshed_at": 9e9, "cache_version": scan.CACHE_VERSION, "addrs": [A]},
                 "seen": seen, "recent": {}})
    ctx = types.SimpleNamespace(state=st, wallet="0xw", senpi_mcp=mcp)
    out = scan.scan({"traderAddresses": [A], **(inputs or {})}, ctx)
    return out, st.last()


def test_a_failed_trader_read_keeps_the_book_it_already_seeded():
    out, rec = _run([_pos()], seen={A: ["ETH|LONG"]}, fail_state=True)
    assert out == []
    assert rec["seen"] == {A: ["ETH|LONG"]}              # not emptied — the next tick still diffs


def test_a_fresh_open_near_their_fill_is_mirrored():
    out, rec = _run([_pos(coin="BTC", szi=0.01, entry=100.0, value=1.005, age=60)], seen={A: []})
    assert [(s["asset"], s["direction"]) for s in out] == [("BTC", "LONG")]
    assert rec["seen"] == {A: ["BTC|LONG"]}


def test_an_old_position_is_not_a_fresh_entry():
    out, _ = _run([_pos(age=1800)], seen={A: []})        # opened 30 minutes ago
    assert out == []


def test_an_unknown_age_still_mirrors():
    out, _ = _run([_pos(age=None)], seen={A: []})        # field absent → fail open, never a silent stall
    assert [s["asset"] for s in out] == ["BTC"]
