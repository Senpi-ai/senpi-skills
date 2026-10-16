"""Unknown is never none, for positions too: a clearinghouse read that failed (the xyz dex is its own
read) leaves that dex's positions UNKNOWN. The desk warns, names the dex in a "Check first" line, and
never prints "No open positions" or "nothing here" over it; a --book run names the wallet whose read
failed instead of silently merging what it got. Mirrors the orders-unread signal (test_protection_unread)."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import book  # noqa: E402
import desk  # noqa: E402
import hl_api  # noqa: E402
import metrics  # noqa: E402
import render  # noqa: E402
import score  # noqa: E402

A = "0x" + "a" * 40
B = "0x" + "b" * 40
FIXTURE = os.path.join(HERE, "fixtures", "sample_trader.json")


def _pos(coin, szi, entry, wallet=None):
    p = dict(coin=coin, szi=str(szi), entryPx=str(entry), liquidationPx=None, marginUsed="10",
             unrealizedPnl="0", returnOnEquity="0", leverage={"value": 3, "type": "cross"}, cumFunding={"sinceOpen": "0"})
    if wallet:
        p["wallet"] = wallet
    return {"type": "oneWay", "position": p}


def _ctxs(*coins):
    return [{"universe": [{"name": c} for c in coins]}, [{"markPx": "100", "funding": "0"} for _ in coins]]


def _stop(coin, sz):
    return dict(coin=coin, side="A", sz=str(sz), oid=1, isTrigger=True, triggerPx="90", triggerCondition="Price below 90",
                reduceOnly=True, orderType="Stop Market", isPositionTpsl=False, children=[])


def _cs(*aps):
    return {"assetPositions": list(aps), "marginSummary": {"accountValue": "1000", "totalMarginUsed": "0"}, "withdrawable": "0"}


def _book(*aps, unread=(("xyz", None),)):
    """A main dex read that came back with `aps` (each fully stopped), and an xyz read that failed."""
    return metrics.open_book(_cs(*aps), [_stop(ap["position"]["coin"], 1) for ap in aps], _ctxs("ETH"), None, None, [], None,
                             positions_unread_by_wallet=[list(x) for x in unread])


def test_open_book_names_the_dex_whose_positions_could_not_be_read():
    bk = _book()
    assert bk["positions"] == [] and bk["positions_unread"] == ["xyz"]
    assert bk["positions_unread_by_wallet"] == [["xyz", None]]
    assert metrics.positions_unread_phrase(bk) == "the xyz dex"
    assert metrics.positions_unread_phrase(_book(unread=(("xyz", A),))) == "the xyz dex of 0xaaaa…aaaa"
    clean = metrics.open_book(_cs(), [], _ctxs("ETH"))
    assert clean["positions_unread"] == [] and metrics.positions_unread_phrase(clean) is None


def test_the_book_merge_keeps_which_wallets_positions_could_not_be_read():
    class HL:
        def trader(self, addr, days=90):
            return dict(address=addr, now_ms=1, window_start_ms=0, fetch_start_ms=0, days=days,
                        clearinghouseState={"assetPositions": [], "marginSummary": {"accountValue": "1"}},
                        clearinghouseState_xyz=None if addr == B else {"assetPositions": []},
                        spotClearinghouseState=None, frontendOpenOrders=[], frontendOpenOrders_xyz=[],
                        fills=[], userFunding=[], userFees={"userCrossRate": "0.0004", "userAddRate": "0.0001"},
                        userFees_xyz=None, portfolio=[], ledger=[])
    merged, _c, _o, _p = book.read(HL(), [A, B], days=90)
    assert merged["positions_unread_by_wallet"] == [["xyz", B]]
    assert book.positions_unread({"clearinghouseState": {}, "clearinghouseState_xyz": None}) == [["xyz", None]]


def test_an_empty_book_with_an_unread_dex_never_says_no_open_positions():
    r = {"book": _book(), "now_ms": 0, "whose": "mine", "leaks": []}
    audit = render.protection(r)
    assert "No open positions" not in audit
    assert "couldn't read the positions on the xyz dex" in audit
    assert "check them on Hyperliquid before acting" in audit


def test_a_fully_stopped_book_with_an_unread_dex_never_says_nothing_here():
    r = {"book": _book(_pos("ETH", 1, 100)), "now_ms": 0, "whose": "mine", "leaks": []}
    audit = render.protection(r)
    assert "nothing here" not in audit and "every position carries a full stop" not in audit
    assert "- **the xyz dex** — couldn't read the positions there; check them on Hyperliquid before acting." in audit


def test_next_steps_name_the_unread_dex_in_check_first():
    ns = render.next_steps({"whose": "mine", "book": _book(), "leaks": []})
    assert ("**Check first.** the xyz dex: the desk couldn't read the positions there — check them on "
            "Hyperliquid before acting.") in ns


def test_the_analyst_voice_warns_before_copying():
    bk = _book()
    assert "the positions on the xyz dex could not be read — the book shown may be incomplete" in render.copy_warnings(bk, [])
    other = render.next_steps_other(dict(whose="other", book=bk, best_setups=[], families=[], cohorts=[]))
    assert "The book is protected" not in other


def test_the_score_lines_never_say_no_open_positions_over_an_unread_dex():
    bk = _book()
    _s, risk_line = score.dim_risk({}, bk, None)
    _m, market_line = score.dim_market(bk, None)
    for line in (risk_line, market_line):
        assert "No open positions" not in line and "the xyz dex" in line, line
    v = score.verdict(dict(trades=0), bk, {}, [])
    assert "nothing for the desk to protect" not in v and "the xyz dex" in v


def _desk(rec, **kw):
    return desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, want_rank=False, want_cohort=False, **kw)


def _rec():
    with open(FIXTURE) as fh:
        return json.load(fh)


def test_the_recorded_desk_reads_both_dexes_cleanly():
    r = _desk(_rec())
    assert r["book"]["positions_unread"] == []
    assert not any("positions unreadable" in w for w in r["meta"]["warnings"])


def test_a_desk_whose_xyz_clearinghouse_read_failed_says_so_end_to_end():
    rec = _rec()
    del rec[f"hl::clearinghouseState::{rec['address'].lower()}::xyz"]       # the read fails → None
    r = _desk(rec)
    assert r["book"]["positions_unread"] == ["xyz"]
    assert ("positions unreadable on the xyz dex: its open positions are unknown, not none"
            in r["meta"]["warnings"])
    page = render.render(r)
    assert "**Check first.** the xyz dex: the desk couldn't read the positions there" in page
    assert "No open positions right now" not in page and "nothing here" not in page


def test_a_book_run_names_the_wallet_whose_xyz_read_failed():
    rec = _rec()
    main, other = rec["address"].lower(), B
    for k in [k for k in rec if k.startswith("hl::") and f"::{main}" in k]:     # a second wallet: the same recorded reads…
        rec[k.replace(main, other)] = rec[k]
    del rec[f"hl::clearinghouseState::{other}::xyz"]                         # …except its xyz read fails
    r = _desk(rec, wallets=[main, other])
    assert r["book"]["positions_unread_by_wallet"] == [["xyz", other]]
    want = f"positions unreadable on the xyz dex of {render.short(other)}: its open positions are unknown, not none"
    assert want in r["meta"]["warnings"]


def test_the_skill_and_methodology_say_an_unread_dex_is_never_empty():
    skill = " ".join(open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8").read().split())
    assert ('A dex whose **positions** could not be read is named instead of counted: never "no open '
            'positions" or "nothing here" over it.') in skill
    assert "or on any dex whose positions could not be read" in skill
    meth = " ".join(open(os.path.join(HERE, "..", "references", "methodology.md"), encoding="utf-8").read().split())
    assert "A dex whose positions could not be read (a failed clearinghouse read" in meth
