"""Unknown is never NONE: an order read that failed leaves those positions' protection unknown — no
"naked", no "set a stop", no reassurance anywhere the book is read — on the main dex and on xyz alike.
Plus the edges the shared fixtures do not cover: a missing `reduceOnly` key is not a stop, a stop that
is not reduce-only is named truthfully, and a PARTIAL row never prints 100%."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import book  # noqa: E402
import deep  # noqa: E402
import desk  # noqa: E402
import hl_api  # noqa: E402
import metrics  # noqa: E402
import render  # noqa: E402
import score  # noqa: E402

A = "0x" + "a" * 40
B = "0x" + "b" * 40
UNKNOWN_KEYS = ("protection", "covered_size", "stops", "stop_covered_share", "take_profit", "non_reduce_only_stops")


def _pos(coin, szi, entry, wallet=None, liq=None):
    p = dict(coin=coin, szi=str(szi), entryPx=str(entry), liquidationPx=liq, marginUsed="10",
             unrealizedPnl="0", returnOnEquity="0", leverage={"value": 3, "type": "cross"}, cumFunding={"sinceOpen": "0"})
    if wallet:
        p["wallet"] = wallet
    return {"type": "oneWay", "position": p}


def _ctxs(*coins):
    return [{"universe": [{"name": c} for c in coins]}, [{"markPx": "100", "funding": "0"} for _ in coins]]


def _stop(coin, sz, **kw):
    o = dict(coin=coin, side="A", sz=str(sz), oid=1, isTrigger=True, triggerPx="90", triggerCondition="Price below 90",
             reduceOnly=True, orderType="Stop Market", isPositionTpsl=False, children=[])
    o.update(kw)
    return o


def _cs(*aps):
    return {"assetPositions": list(aps), "marginSummary": {"accountValue": "1000", "totalMarginUsed": "0"}, "withdrawable": "0"}


def _unknown_book():
    return metrics.open_book(_cs(_pos("ETH", 1, 100)), None, _ctxs("ETH"), None, _cs(), [], None)


def test_a_failed_main_order_read_is_unknown_not_naked():
    bk = _unknown_book()
    p = bk["positions"][0]
    assert all(p[k] is None for k in UNKNOWN_KEYS), {k: p[k] for k in UNKNOWN_KEYS}
    assert bk["naked"] == [] and bk["partial"] == [] and bk["unknown"] == ["ETH"] and bk["orders_unread"] == [""]
    assert metrics.protection_of(p) is None


def test_a_failed_xyz_order_read_is_unknown_and_main_is_still_judged():
    bk = metrics.open_book(_cs(_pos("ETH", 1, 100)), [_stop("ETH", 1)], _ctxs("ETH"), None,
                           _cs(_pos("xyz:XYZ100", 1, 100)), None, _ctxs("xyz:XYZ100"))
    by = {p["coin"]: p for p in bk["positions"]}
    assert by["ETH"]["protection"] == "FULL"
    assert by["xyz:XYZ100"]["protection"] is None and bk["orders_unread"] == ["xyz"] and bk["naked"] == []


def test_one_wallets_failed_read_in_a_book_is_unknown_for_that_wallet_only():
    cs = _cs(_pos("ETH", 1, 100, wallet=A), _pos("ETH", 1, 100, wallet=B))
    bk = metrics.open_book(cs, [_stop("ETH", 1, wallet=A)], _ctxs("ETH"), None, _cs(), [], None,
                           orders_unread_by_wallet=[["", B]])
    by = {p["wallet"]: p for p in bk["positions"]}
    assert by[A]["protection"] == "FULL" and by[B]["protection"] is None and bk["naked"] == []
    assert bk["orders_unread"] == [""]


def test_the_book_merge_keeps_which_wallet_could_not_be_read():
    class HL:
        def trader(self, addr, days=90):
            return dict(address=addr, now_ms=1, window_start_ms=0, fetch_start_ms=0, days=days,
                        clearinghouseState={"assetPositions": [], "marginSummary": {"accountValue": "1"}},
                        clearinghouseState_xyz=None, spotClearinghouseState=None,
                        frontendOpenOrders=None if addr == B else [],
                        frontendOpenOrders_xyz=None if addr == A else [],
                        fills=[], userFunding=[], userFees={"userCrossRate": "0.0004", "userAddRate": "0.0001"},
                        userFees_xyz=None, portfolio=[], ledger=[])
    merged, _c, _o, _p = book.read(HL(), [A, B], days=90)
    assert merged["orders_unread_by_wallet"] == [["xyz", A], ["", B]]


def test_the_trader_read_keeps_a_failed_order_read_as_none():
    class Boom(hl_api.HL):
        def info(self, body):
            if body.get("type") == "frontendOpenOrders":
                raise hl_api.HLError("HTTP 500")
            return super().info(body)
    hl = Boom(cache_dir=None)
    assert hl._optional({"type": "frontendOpenOrders", "user": A}) is None
    src = open(os.path.join(HERE, "..", "scripts", "hl_api.py")).read()
    assert '"frontendOpenOrders": self._optional(' in src
    assert '"dex": "xyz"}) or [],' not in src, "a failed xyz order read is folded into [] again"


def test_a_desk_whose_order_read_failed_reads_unknown_end_to_end():
    with open(os.path.join(HERE, "fixtures", "sample_trader.json")) as fh:
        rec = json.load(fh)
    del rec[f"hl::frontendOpenOrders::{rec['address'].lower()}"]          # the read fails → None
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, want_rank=False, want_cohort=False)
    assert r["book"]["naked"] == [] and r["book"]["unknown"] == ["ETH", "NEAR", "ZEC"]
    assert "open orders unreadable: protection unknown for ETH, NEAR, ZEC" in r["meta"]["warnings"]


def test_the_note_names_the_dex_and_never_nudges_a_stop():
    status, note = render._protection_note(dict(coin="xyz:XYZ100", liq_distance_pct=2.0, stop_covered_share=None,
                                                protection=None, leverage=5, stop_distance_pct=None), None)
    assert (status, note) == ("UNKNOWN", "couldn't read the orders on the xyz dex — check this position's stop "
                                         "on Hyperliquid before acting on it")
    r = {"whose": "mine", "book": _unknown_book(), "leaks": []}
    assert "Protect first" not in render.next_steps(r)


def test_every_reader_of_an_unread_book_says_unknown_never_reassures():
    """Nine readers turned an unread book into reassurance; each is pinned here."""
    bk = _unknown_book()
    r = {"book": bk, "now_ms": 0, "whose": "mine", "leaks": []}
    # 1. the audit section: no "every position carries a full stop", the UNKNOWN row in the todo list
    audit = render.protection(r)
    assert "every position carries a full stop" not in audit
    assert "1 unknown (orders not read)" in audit and "couldn't read the orders on the main dex" in audit
    # 2. the analyst voice
    assert render.copy_warnings(bk, []) == ["protection is unknown on 1 of 1 open positions — their orders could not be read"]
    other = render.next_steps_other(dict(r, whose="other", best_setups=[], families=[], cohorts=[]))
    assert "The book is protected" not in other and "protection is unknown on 1 of 1 open positions" in other
    # 3. the compare table row and its separator
    row = dict(render.COMPARE_ROWS)["Open positions · unprotected"]
    assert row(dict(book=bk)) == "1 · 0 (+1 unread)"
    assert metrics.unprotected_label(bk) == "0 (+1 unread)"
    # 4. the risk dimension: an unread book costs what a naked one costs
    naked_s, _ = score.dim_risk({}, metrics.open_book(_cs(_pos("ETH", 1, 100)), [], _ctxs("ETH")), None)
    full_s, _ = score.dim_risk({}, metrics.open_book(_cs(_pos("ETH", 1, 100)), [_stop("ETH", 1)], _ctxs("ETH")), None)
    unread_s, line = score.dim_risk({}, bk, None)
    assert unread_s == naked_s < full_s
    assert line == "1 of 1 open positions has orders the desk could not read — protection unknown for ETH."
    # 5. the header chips
    assert "PROTECTION UNKNOWN (1/1)" in score.flags({}, bk, None, None, None, None)
    # 6. the verdict's under-water line
    v = score.verdict(dict(trades=0), dict(bk, unrealized=-5000.0, account_value=10000.0), {}, [])
    assert "with 0 (+1 unread) of 1 positions unprotected" in v
    # 7. the watch list
    assert deep.watch(dict(book=bk))["items"][0] == \
        "Risk guard: a stop missing or a position within 5% of liquidation (ETH (orders unread) today)"
    # 8. the squeeze critique
    import strategy_read
    fp = dict(simultaneity=dict(both_share=0.0), leg_correlation=None, net_over_gross=None, pnl_beta=None,
              outcome_concentration=None, dead_sides=[])
    cohorts = [dict(name="proven", agreement=1.0, against=[], rows=[1]), dict(name="hot", agreement=-1.0, against=["ETH"], rows=[1])]
    crit = " ".join(strategy_read.critique(fp, {}, bk, None, None, cohorts))
    assert "the desk could not read the stops on ETH" in crit and "the stops are what make" not in crit
    # 9. the protect deep dive and the stderr progress line
    [prow] = deep.protect(dict(book=bk), {})["rows"]
    assert prow["note"] == metrics.unread_note("ETH")
    src = open(os.path.join(HERE, "..", "scripts", "desk.py")).read()
    assert "{metrics.unprotected_label(book)} unprotected" in src


def test_a_missing_reduce_only_key_is_not_a_stop():
    o = _stop("ETH", 1)
    del o["reduceOnly"]
    bk = metrics.open_book(_cs(_pos("ETH", 1, 100)), [o], _ctxs("ETH"))
    assert bk["positions"][0]["protection"] == "NONE" and bk["positions"][0]["stops"] == []


def test_a_non_reduce_only_stop_is_named_not_called_no_stop():
    """C2 keeps reduceOnly required (escalated to the product owner). A trader whose only exit-side stop
    is not reduce-only HAS a stop order: the desk says so truthfully, never "no stop / attach a stop"."""
    bk = metrics.open_book(_cs(_pos("ETH", 1, 100)), [_stop("ETH", 1, reduceOnly=False)], _ctxs("ETH"))
    p = bk["positions"][0]
    assert p["protection"] == "NONE" and p["stops"] == [] and p["non_reduce_only_stops"] == 1
    assert render._protection_note(p, None) == (
        "UNPROTECTED", "has a stop order that isn't reduce-only — not counted as protection; "
                       "make it reduce-only or replace it with a reduce-only stop")
    ns = render.next_steps({"whose": "mine", "book": bk, "leaks": []})
    assert "ETH: each has a stop order that isn't reduce-only — not counted as protection." in ns
    assert "is naked" not in ns
    [row] = deep.protect(dict(book=bk), {})["rows"]
    assert row["note"] == "stop isn't reduce-only — no cover"


def test_near_liquidation_a_non_reduce_only_stop_is_not_told_to_put_a_stop():
    p = dict(metrics.open_book(_cs(_pos("ETH", 1, 100, liq="98")), [_stop("ETH", 1, reduceOnly=False)],
                               _ctxs("ETH"))["positions"][0])
    status, note = render._protection_note(p, None)
    assert status == "AT RISK" and "put a stop" not in note
    assert note == ("2.0% from liquidation and it has a stop order that isn't reduce-only — not counted as "
                    "protection; make it reduce-only or replace it with a reduce-only stop")


def test_a_full_stop_near_liquidation_is_never_called_partial():
    p = dict(coin="ETH", side="LONG", leverage=20, liq_distance_pct=2.0, protection="FULL", stop_covered_share=1.0)
    assert score._stop_phrase(p) == "a full stop"
    assert score._stop_phrase(dict(p, protection=None)) == "orders the desk could not read"
    assert score._stop_phrase(dict(p, protection="NONE", non_reduce_only_stops=1)) == \
        "a stop order that isn't reduce-only, which is no protection"


def test_a_partial_row_never_prints_100_percent():
    bk = metrics.open_book(_cs(_pos("ETH", 2, 100)), [_stop("ETH", "1.995")], _ctxs("ETH"))
    p = bk["positions"][0]
    assert p["protection"] == "PARTIAL" and p["covered_size"] == "1.995"
    assert render.cover_pct(p) == "99%"
    assert render._protection_note(p, None) == ("PARTLY COVERED", "the other 1% rides naked — extend the stop to the full size")
    assert render.cover_pct(dict(p, protection="FULL", stop_covered_share=1.0)) == "100%"
    assert render.cover_pct(dict(p, protection=None, stop_covered_share=None)) == "—"


# ---- fix round 1 ----

def test_an_unread_position_gets_a_check_first_line_in_next_steps():
    bk = metrics.open_book(_cs(_pos("ETH", 1, 100, liq="98")), None, _ctxs("ETH"), None, _cs(), [], None)
    ns = render.next_steps({"whose": "mine", "book": bk, "leaks": []})
    assert "**Check first.** ETH: the desk couldn't read its orders — check its stop on Hyperliquid before acting." in ns
    assert "Protect first" not in ns and "nothing needs protecting" not in ns
    # a known-naked position still gets Protect first, and both lines coexist
    both = metrics.open_book(_cs(_pos("ETH", 1, 100), _pos("xyz:XYZ100", 1, 100)), [], _ctxs("ETH"), None,
                             _cs(_pos("xyz:XYZ100", 1, 100)), None, _ctxs("xyz:XYZ100"))
    ns2 = render.next_steps({"whose": "mine", "book": both, "leaks": []})
    assert "**Protect first.** ETH" in ns2 and "**Check first.**" in ns2


def test_skill_md_names_unknown_and_check_first():
    txt = open(os.path.join(HERE, "..", "SKILL.md")).read()
    assert "`UNPROTECTED` / `PARTLY COVERED` /\n   `PROTECTED` / `UNKNOWN`" in txt or "`PROTECTED` / `UNKNOWN`" in txt
    assert "name the AT RISK / UNPROTECTED positions, and **check first** on any UNKNOWN one" in txt


def test_an_unread_position_has_no_stop_oids():
    bk = _unknown_book()
    assert bk["positions"][0]["stop_oids"] is None
    read = metrics.open_book(_cs(_pos("ETH", 1, 100)), [], _ctxs("ETH"))
    assert read["positions"][0]["stop_oids"] == []


def test_an_unread_protect_row_says_check_before_acting_and_the_footer_does_not_nudge_a_stop():
    bk = _unknown_book()
    [prow] = deep.protect(dict(book=bk), {})["rows"]
    assert prow["note"] == ("couldn't read the orders on the main dex — check this position's stop on "
                            "Hyperliquid before acting on it")
    out = render.render_deep("protect", dict(rows=[dict(prow)], total_risk_now=1.0, total_risk_after=1.0), {"book": bk})
    assert "number to set on each position onchain" not in out and "on each position whose orders the desk read" in out
    assert "ETH" in out.split("These are yours to place.")[1] and "check" in out.split("These are yours to place.")[1]


def test_an_unread_position_near_liquidation_ranks_protect_like_a_missing_stop():
    import followups
    bk = metrics.open_book(_cs(_pos("ETH", 1, 100, liq="98")), None, _ctxs("ETH"), None, _cs(), [], None)
    r = dict(book=bk, track={}, leaks=[], timing={})
    sc = {}
    orig = followups._fill
    followups._fill = lambda ranked, bank, rr: ranked
    try:
        ranked = followups.offer(r, n=2)
    finally:
        followups._fill = orig
    assert ranked[0] == "protect"
