"""quant-desk — `dsl.attach()` against a fake MCP: the parsing that could not be verified live.

@0xsarvesh reviewed #753 against senpi-trading-runtime@main and the conclusion held (no UNPROTECTED
bug) but the mechanism did not:

* Phase 1 is **runtime-local** — `editPosition({stopLoss})` at `phase1.absoluteFloor`, the
  strategy's `max_loss_pct` (`engine/exchange-stop.ts:18`). No backend ratchet is registered.
* `addRatchetStop` is gated on `phase === 2` (`monitor/process-one-position.ts:807`), so a
  phase-1 position has **no `ratchet_stop_list` row at all**.
* An ACTIVE row with `currentTierIndex: -1` is therefore NOT phase 1 — most likely a stale row.
  `backendHasLiveSl()` (`:1052`) exists because a row can name an exchange SL no longer resting,
  and a ratchet can stay ACTIVE after its stop filled and the strategy re-entered.

So the desk renders a tier only where the exchange corroborates the row. These tests are the guard.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import dsl  # noqa: E402

ADDR = "0x" + "a" * 40
TIERS = [{"triggerRoe": 8, "lockRoe": 35}, {"triggerRoe": 18, "lockRoe": 60}]


class FakeMCP:
    """Captured production shapes. Records calls so the call COUNT is testable too."""

    def __init__(self, rows, strategies=None):
        self.rows = rows
        self.strategies = strategies if strategies is not None else [
            {"id": "st_1", "strategyName": "Phalanx", "strategyWalletAddress": ADDR}]
        self.calls = []

    def mcp_call(self, tool, **kw):
        self.calls.append((tool, kw))
        if tool == "strategy_list":
            return {"data": {"strategies": self.strategies}}
        if tool == "ratchet_stop_list":
            return {"data": {"positions": self.rows}}
        raise AssertionError(f"unexpected tool {tool}")


def row(**kw):
    base = dict(asset="ETH", status="ACTIVE", currentTierIndex=0, tierFloorPrice=3.1134,
                highWaterPrice=3.1684, highWaterRoe=13.7, direction="LONG",
                activeSLOrderId=777, dslConfig={"tiered": {"tiers": TIERS}})
    base.update(kw)
    return base


def pos(**kw):
    base = dict(coin="ETH", side="LONG", stop_covered_share=1.0, stop_px=3.1134, stop_oids=[777])
    base.update(kw)
    return base


def _book(*positions):
    return {"positions": list(positions)}


# ---------------------------------------------------------------- the happy path
def test_an_armed_tier_the_exchange_confirms_is_shown():
    b = _book(pos())
    assert dsl.attach(FakeMCP([row()]), ADDR, b) == 1
    d = b["positions"][0]["dsl"]
    assert d["tier_index"] == 0 and d["n_tiers"] == 2 and d["floor_px"] == 3.1134
    assert d["armed"] == TIERS[0] and d["next_tier"] == TIERS[1]
    assert "tier 1 of 2" in dsl.line(b["positions"][0])


def test_one_ratchet_call_per_strategy_not_one_per_position():
    """`asset` is optional on ratchet_stop_list, so asking per position was N calls for one answer."""
    m = FakeMCP([row(asset="ETH"), row(asset="BTC", activeSLOrderId=778)])
    b = _book(pos(coin="ETH"), pos(coin="BTC", stop_oids=[778]))
    assert dsl.attach(m, ADDR, b) == 2
    assert [t for t, _ in m.calls].count("ratchet_stop_list") == 1
    assert "asset" not in dict(m.calls[-1][1]), "still filtering server-side per asset"


def test_strategy_list_is_filtered_to_this_address():
    m = FakeMCP([row()])
    dsl.attach(m, ADDR, _book(pos()))
    tool, kw = m.calls[0]
    assert tool == "strategy_list" and kw.get("strategyAddresses") == [ADDR], \
        "fetching every strategy the user ever had to find one wallet"


# ---------------------------------------------------------------- the stale-row guard
def test_a_row_naming_an_order_that_is_not_resting_says_nothing():
    """The blocking case: a stale ACTIVE row rendering 'tier 1 armed, floor X' for a position it no
    longer belongs to — the exact overclaim this module exists to prevent."""
    b = _book(pos(stop_oids=[999], stop_px=2.5))       # a different order, at a different price
    assert dsl.attach(FakeMCP([row()]), ADDR, b) == 0
    assert "dsl" not in b["positions"][0]


def test_a_position_with_no_resting_stop_never_carries_a_floor_sentence():
    """A row claiming a floor must never sit under a table line that says UNPROTECTED."""
    b = _book(pos(stop_covered_share=0.0, stop_px=None, stop_oids=[]))
    assert dsl.attach(FakeMCP([row()]), ADDR, b) == 0
    assert "dsl" not in b["positions"][0]


def test_the_floor_price_alone_can_corroborate_when_the_oid_is_absent():
    """Older rows carry no usable activeSLOrderId; a stop resting AT the claimed floor is still
    proof enough, because the sentence only ever quotes a price an order really rests at."""
    b = _book(pos(stop_oids=[]))
    assert dsl.attach(FakeMCP([row(activeSLOrderId=None)]), ADDR, b) == 1


def test_a_floor_that_does_not_match_the_resting_stop_is_refused():
    b = _book(pos(stop_oids=[], stop_px=2.90))          # ~7% away from the claimed 3.1134
    assert dsl.attach(FakeMCP([row(activeSLOrderId=None)]), ADDR, b) == 0


def test_tick_rounding_still_corroborates():
    b = _book(pos(stop_oids=[], stop_px=3.1134 * 1.001))
    assert dsl.attach(FakeMCP([row(activeSLOrderId=None)]), ADDR, b) == 1


def test_a_row_from_the_opposite_side_is_refused():
    """A stale row from the other direction renders a floor on the wrong side of the mark."""
    b = _book(pos(side="SHORT"))
    assert dsl.attach(FakeMCP([row(direction="LONG")]), ADDR, b) == 0


def test_a_short_is_annotated_when_the_row_agrees():
    b = _book(pos(side="SHORT"))
    assert dsl.attach(FakeMCP([row(direction="SHORT")]), ADDR, b) == 1
    assert "only ever tightens" in dsl.line(b["positions"][0])


def test_an_uncorroborated_row_is_reported_in_meta_not_silently_dropped():
    meta = {}
    dsl.attach(FakeMCP([row()]), ADDR, _book(pos(stop_oids=[999], stop_px=2.5)), meta)
    assert any("did not match an order resting" in w for w in meta.get("warnings") or [])


# ---------------------------------------------------------------- tier -1 is not phase 1
def test_an_unarmed_row_says_nothing_at_all():
    """`currentTierIndex: -1` does NOT mean phase 1 — a phase-1 position has no backend row. An
    unarmed row is a rule that has not fired or a stale row, and neither has anything true to say."""
    b = _book(pos())
    assert dsl.attach(FakeMCP([row(currentTierIndex=-1, tierFloorPrice=None)]), ADDR, b) == 0
    assert "dsl" not in b["positions"][0]


def test_a_tier_index_past_the_end_of_the_ladder_says_nothing():
    """It used to render 'tier 4 of 2 armed ... locking 0% of the gain'. (@0xsarvesh, #753.)"""
    b = _book(pos())
    assert dsl.attach(FakeMCP([row(currentTierIndex=7)]), ADDR, b) == 0


def test_a_non_active_row_is_ignored():
    b = _book(pos())
    assert dsl.attach(FakeMCP([row(status="SL_TRIGGERED")]), ADDR, b) == 0


# ---------------------------------------------------------------- it can never cost the desk
def test_every_failure_mode_is_a_silent_no_op():
    class Broken:
        def mcp_call(self, tool, **kw):
            raise RuntimeError("senpi is down")
    b = _book(pos())
    assert dsl.attach(Broken(), ADDR, b) == 0 and "dsl" not in b["positions"][0]
    assert dsl.attach(None, ADDR, b) == 0                      # no token
    assert dsl.attach(FakeMCP([row()], strategies=[]), ADDR, b) == 0   # external wallet
    assert dsl.attach(FakeMCP([]), ADDR, b) == 0               # no rows
    assert dsl.attach(FakeMCP([row()]), ADDR, {"positions": []}) == 0
    assert dsl.attach(FakeMCP([{"garbage": True}]), ADDR, b) == 0


def test_a_strategy_row_without_an_id_is_skipped_not_crashed():
    m = FakeMCP([row()], strategies=[{"strategyName": "No id", "strategyWalletAddress": ADDR}])
    assert dsl.attach(m, ADDR, _book(pos())) == 0
