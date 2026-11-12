"""Squid reads its traders' books LIVE from Hyperliquid, and takes freshness from the
tick-to-tick transition rather than from a reported age.

Shadow — the package this replaces — read the same books from our indexed trader-state, which
lands a median ~132 minutes after the trade. Over 14 days and ~280,000 position opens, 0.0%
were visible inside its 4-minute window, so its age gate rejected every real open and it never
fired. See strategies/shadow/DESIGN.md.

What that leaves to pin here:

1. The HL envelope: positions arrive as `assetPositions[].position`, carrying coin / szi /
   entryPx / positionValue / leverage{value} and NO mark price. The mark is positionValue /
   |szi|, which is exact.
2. The transition IS the freshness contract. Held-last-tick is not fresh; first sight seeds and
   emits nothing.
3. A PARTIAL book is dropped, not used. If one venue answers and the other fails, using the half
   we got makes the next tick read the missing venue's standing positions as brand-new opens —
   the #664 failure (mirroring positions 2-65% from their fill) arriving by a different road.
4. The venue stays in the coin id: `xyz:` is required to trade a HIP-3 asset.
5. A failed read never empties a seen-set.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

A = "0x" + "a" * 40
B = "0x" + "b" * 40


def _hl_pos(coin="BTC", szi=0.01, entry=100.0, value=1.0, lev=5):
    """One entry of HL's `assetPositions`, verbatim in shape — value = |szi| x mark."""
    return {"position": {"coin": coin, "szi": str(szi), "entryPx": str(entry),
                         "positionValue": str(value),
                         "leverage": {"type": "cross", "value": lev}}}


class _State:
    def __init__(self, last=None): self._l = [last] if last else []
    def last(self): return self._l[-1] if self._l else None
    def append(self, d): self._l.append(d)


class _MCP:
    """Squid resolves an explicit cohort without any MCP read; this records that fact."""
    def __init__(self): self.calls = []

    def call_tool(self, tool, args):
        self.calls.append(tool)
        return {}


def _run(books, seen, addrs=(A,), inputs=None, monkey=None):
    """`books` maps (addr, dex) -> HL assetPositions list, or an Exception to raise."""
    reads = []

    def fake_book(url, addr, dex, timeout):
        reads.append((addr, dex))
        v = books.get((addr, dex), [])
        if isinstance(v, Exception):
            raise v
        return v

    orig = scan._hl_book
    scan._hl_book = fake_book
    try:
        mcp = _MCP()
        st = _State({"cohort": {"refreshed_at": 9e9, "cache_version": scan.CACHE_VERSION,
                                "addrs": list(addrs)},
                     "seen": seen, "recent": {}})
        ctx = types.SimpleNamespace(state=st, wallet="0xw", senpi_mcp=mcp)
        out = scan.scan({"traderAddresses": list(addrs), **(inputs or {})}, ctx)
        return out, st.last(), reads, mcp
    finally:
        scan._hl_book = orig


# ── 1. the HL envelope ──────────────────────────────────────────────────────────────────────

def test_the_mark_comes_from_position_value_over_size():
    p = scoring.extract_positions(
        {"openPositions": [_hl_pos(szi=0.01, entry=100.0, value=1.03)["position"]]})[0]
    assert abs(p["mark"] - 103.0) < 1e-9                  # 1.03 / 0.01
    assert scoring.chase_pct(p["entry"], p["mark"], "LONG") == 3.0


def test_a_real_hyperliquid_position_parses():
    """Captured from the live endpoint 2026-10-12, not hand-written."""
    real = {"coin": "GRASS", "szi": "-177.2", "entryPx": "0.80197",
            "positionValue": "137.944884", "leverage": {"type": "cross", "value": 3},
            "liquidationPx": "1.1", "unrealizedPnl": "1.70", "marginUsed": "45.9"}
    p = scoring.extract_positions({"openPositions": [real]})[0]
    assert p["coin"] == "GRASS" and p["side"] == "SHORT"
    assert abs(p["mark"] - 137.944884 / 177.2) < 1e-9
    assert p["leverage"] == 3.0


def test_there_is_no_age_gate_left_to_fail_open():
    assert not hasattr(scoring, "opened_within")


def test_a_position_that_ran_either_way_is_not_chased():
    assert scoring.chase_within(3.0, 1.5) is False        # ran 3% past their fill
    assert scoring.chase_within(-3.0, 1.5) is False       # already 3% underwater on their fill
    assert scoring.chase_within(-1.2, 1.5) is True
    assert scoring.chase_within(0.0, 1.5) is True


# ── 2. the transition is the freshness contract ─────────────────────────────────────────────

def test_a_fresh_open_near_their_fill_is_mirrored():
    out, rec, _, mcp = _run({(A, ""): [_hl_pos(value=1.005)]}, seen={A: []})
    assert [(s["asset"], s["direction"]) for s in out] == [("BTC", "LONG")]
    assert rec["seen"] == {A: ["BTC|LONG"]}
    assert mcp.calls == []                                # explicit cohort needs no MCP read


def test_a_position_held_last_tick_is_not_fresh():
    out, rec, _, _ = _run({(A, ""): [_hl_pos(value=1.005)]}, seen={A: ["BTC|LONG"]})
    assert out == []
    assert rec["seen"] == {A: ["BTC|LONG"]}


def test_first_sight_of_a_trader_seeds_and_emits_nothing():
    out, rec, _, _ = _run({(A, ""): [_hl_pos(value=1.005)]}, seen={})
    assert out == []                                      # never inherit a standing book
    assert rec["seen"] == {A: ["BTC|LONG"]}


def test_a_run_up_open_is_seeded_but_not_chased():
    """Detected as a transition, then rejected by the cap — and still recorded as seen, so it
    is not re-offered every tick for the rest of its life."""
    out, rec, _, _ = _run({(A, ""): [_hl_pos(value=1.05)]}, seen={A: []})   # 5% past their fill
    assert out == []
    assert rec["seen"] == {A: ["BTC|LONG"]}


# ── 3. a partial book is dropped, not used ──────────────────────────────────────────────────

def test_a_partial_book_is_dropped_so_the_next_tick_cannot_see_a_false_open():
    out, rec, reads, _ = _run(
        {(A, ""): [_hl_pos()], (A, "xyz"): RuntimeError("xyz timeout")},
        seen={A: ["ETH|LONG"]})
    assert out == []
    assert rec["seen"] == {A: ["ETH|LONG"]}               # untouched, not replaced by the half-book
    assert reads == [(A, ""), (A, "xyz")]                 # stopped at the failure, nothing after


def test_a_failed_read_keeps_the_book_it_already_seeded():
    out, rec, _, _ = _run({(A, ""): RuntimeError("upstream 500")}, seen={A: ["ETH|LONG"]})
    assert out == []
    assert rec["seen"] == {A: ["ETH|LONG"]}


def test_one_traders_failure_does_not_block_another():
    out, rec, _, _ = _run(
        {(A, ""): RuntimeError("timeout"), (A, "xyz"): [],
         (B, ""): [_hl_pos(coin="SOL", value=1.004)], (B, "xyz"): []},
        seen={A: ["ETH|LONG"], B: []}, addrs=(A, B))
    assert [s["asset"] for s in out] == ["SOL"]
    assert rec["seen"][A] == ["ETH|LONG"]                 # carried forward
    assert rec["seen"][B] == ["SOL|LONG"]


# ── 4. the venue stays in the coin id ───────────────────────────────────────────────────────

def test_an_xyz_position_keeps_its_venue_prefix():
    out, rec, reads, _ = _run(
        {(A, ""): [], (A, "xyz"): [_hl_pos(coin="AMZN", entry=200.0, szi=0.05, value=10.02)]},
        seen={A: []})
    assert [s["asset"] for s in out] == ["xyz:AMZN"]
    assert rec["seen"] == {A: ["xyz:AMZN|LONG"]}
    assert (A, "xyz") in reads


def test_an_already_prefixed_coin_is_not_double_prefixed():
    out, _, _, _ = _run({(A, ""): [], (A, "xyz"): [_hl_pos(coin="xyz:AMZN", entry=200.0,
                                                           szi=0.05, value=10.02)]},
                        seen={A: []})
    assert [s["asset"] for s in out] == ["xyz:AMZN"]


def test_the_xyz_venue_can_be_switched_off():
    _, _, reads, _ = _run({(A, ""): [_hl_pos()]}, seen={A: []},
                          inputs={"readXyzDex": False})
    assert reads == [(A, "")]


# ── 5. reads are sequential and bounded ─────────────────────────────────────────────────────

def test_reads_are_one_per_wallet_per_venue_in_order():
    _, _, reads, _ = _run({(A, ""): [], (A, "xyz"): [], (B, ""): [], (B, "xyz"): []},
                          seen={A: [], B: []}, addrs=(A, B))
    assert reads == [(A, ""), (A, "xyz"), (B, ""), (B, "xyz")]
