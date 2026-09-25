"""quant-desk — the multi-wallet book (`--book`). Synthetic wallets, no network.

The union is not a concatenation, and every test here guards one of the four places where naive
concatenation gives a confidently wrong answer.
"""
import os
import sys
from pathlib import Path as _P

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import book  # noqa: E402
import metrics  # noqa: E402
from roundtrips import episodes_from_fills  # noqa: E402

H = 3_600_000
A = "0x" + "a" * 40
B = "0x" + "b" * 40


def fill(coin, side, sz, px, t, start, tid, dir_=None, pnl=0.0, fee=0.1, crossed=True):
    return dict(coin=coin, side=side, sz=str(sz), px=str(px), time=t, startPosition=str(start),
                dir=dir_ or ("Open Long" if side == "B" else "Close Long"), closedPnl=str(pnl),
                hash="0x0", oid=tid, crossed=crossed, fee=str(fee), tid=tid, feeToken="USDC", twapId=None)


# ---------------------------------------------------------------- episodes per wallet
def test_two_wallets_trading_the_same_coin_do_not_interleave():
    """The reason book.py exists. `episodes_from_fills` follows position per coin through
    `startPosition`; one concatenated stream of two wallets' BTC fills is not a position track."""
    a = [fill("BTC", "B", 1, 100, 0, 0, 1), fill("BTC", "A", 1, 120, 2 * H, 1, 2, pnl=20)]
    b = [fill("BTC", "B", 3, 100, H, 0, 3), fill("BTC", "A", 3, 90, 3 * H, 3, 4, pnl=-30)]
    per_wallet = []
    for fs in (a, b):
        c, _o = episodes_from_fills(fs)
        per_wallet.extend(c)
    assert len(per_wallet) == 2
    assert round(sum(e["realized"] for e in per_wallet), 6) == -10.0
    # the wrong way: one stream, sorted by time, position track corrupted
    naive, _ = episodes_from_fills(sorted(a + b, key=lambda f: f["time"]))
    assert len(naive) != len(per_wallet) or round(sum(e["realized"] for e in naive), 6) != -10.0


def test_read_tags_every_episode_with_its_wallet():
    class HL:
        def trader(self, addr, days=90):
            n = 1 if addr == A else 3
            return dict(address=addr, now_ms=10 * H, window_start_ms=0, fetch_start_ms=0, days=days,
                        clearinghouseState={"assetPositions": [], "marginSummary": {"accountValue": "1"}},
                        clearinghouseState_xyz=None, frontendOpenOrders=[], frontendOpenOrders_xyz=[],
                        spotClearinghouseState=None,
                        fills=[fill("BTC", "B", n, 100, 0, 0, n), fill("BTC", "A", n, 110, 2 * H, n, n + 10, pnl=10 * n)],
                        userFunding=[], userFees={"userCrossRate": "0.0004", "userAddRate": "0.0001"},
                        userFees_xyz=None, portfolio=[], ledger=[])
    merged, closed, opened, per = book.read(HL(), [A, B], days=90)
    assert {e["wallet"] for e in closed} == {A, B}
    assert merged["wallets"] == [A, B] and len(merged["fills"]) == 4
    assert [p["address"] for p in per] == [A, B]


def test_duplicate_addresses_are_read_once():
    calls = []
    class HL:
        def trader(self, addr, days=90):
            calls.append(addr)
            return dict(address=addr, now_ms=0, window_start_ms=0, fetch_start_ms=0, days=days,
                        clearinghouseState={"assetPositions": []}, clearinghouseState_xyz=None,
                        frontendOpenOrders=[], frontendOpenOrders_xyz=[], spotClearinghouseState=None,
                        fills=[], userFunding=[], userFees={}, userFees_xyz=None, portfolio=[], ledger=[])
    book.read(HL(), [A, A.upper(), B], days=90)
    assert calls == [A, B]


# ---------------------------------------------------------------- portfolio series
def test_series_sum_is_step_interpolated_on_the_union_grid():
    a = [(0, 100.0), (2 * H, 200.0)]
    b = [(H, 50.0)]
    out = book._sum_series([a, b])
    assert out == [[0, 100.0], [H, 150.0], [2 * H, 250.0]]


def test_a_wallet_contributes_nothing_before_it_existed():
    """A strategy opened halfway through the window did not hold equity in the first half. Carrying
    its first value backwards would invent equity and flatten the drawdown it sits in."""
    old = [(0, 100.0), (4 * H, 100.0)]
    new = [(2 * H, 500.0), (4 * H, 500.0)]
    out = dict((t, v) for t, v in book._sum_series([old, new]))
    assert out[0] == 100.0
    assert out[2 * H] == 600.0


def test_a_closed_wallet_carries_its_last_value_forward():
    closed_w = [(0, 100.0), (H, 80.0)]
    live = [(0, 10.0), (3 * H, 10.0)]
    out = dict((t, v) for t, v in book._sum_series([closed_w, live]))
    assert out[3 * H] == 90.0            # 80 held + 10, not 10


def test_merge_portfolio_sums_each_window_and_keeps_hl_shape():
    p1 = [["allTime", {"accountValueHistory": [[0, "100"], [H, "120"]], "pnlHistory": [[0, "5"], [H, "9"]], "vlm": "1000"}]]
    p2 = [["allTime", {"accountValueHistory": [[0, "300"], [H, "280"]], "pnlHistory": [[0, "7"], [H, "1"]], "vlm": "500"}]]
    out = dict(book._merge_portfolio([p1, p2]))
    assert out["allTime"]["accountValueHistory"] == [[0, "400.0"], [H, "400.0"]]
    assert out["allTime"]["pnlHistory"] == [[0, "12.0"], [H, "10.0"]]
    assert out["allTime"]["vlm"] == "1500.0"
    # and the shape survives the real readers — pnl_series rebases on its first point
    merged = [["allTime", out["allTime"]]]
    assert metrics.pnl_series(merged, 0) == [(0, 0.0), (H, -2.0)]
    assert metrics.equity_curve(merged, [], 0) == [(0, 400.0), (H, 400.0)]


# ---------------------------------------------------------------- ledger
def test_a_transfer_between_two_wallets_of_the_book_is_not_a_flow():
    led = [{"time": H, "delta": {"type": "send", "user": A, "destination": B, "amount": "1000"}}]
    assert book._internal(led, {A, B}) == []
    assert metrics.flows(book._internal(led, {A, B}), [A, B]) == []


def test_a_transfer_to_the_outside_world_is_still_a_flow():
    out_addr = "0x" + "c" * 40
    led = [{"time": H, "delta": {"type": "send", "user": A, "destination": out_addr, "amount": "1000"}},
           {"time": 2 * H, "delta": {"type": "deposit", "usdc": "500"}}]
    kept = book._internal(led, {A, B})
    assert len(kept) == 2
    assert metrics.flows(kept, [A, B]) == [(H, -1000.0), (2 * H, 500.0)]


def test_flows_signs_a_sibling_deposit_as_inbound_for_the_whole_book():
    """Matching on one address would have signed a transfer INTO wallet B as an outflow."""
    out_addr = "0x" + "c" * 40
    led = [{"time": H, "delta": {"type": "send", "user": out_addr, "destination": B, "amount": "700"}}]
    assert metrics.flows(led, [A, B]) == [(H, 700.0)]
    assert metrics.flows(led, A) == [(H, -700.0)]        # single-wallet behaviour unchanged


# ---------------------------------------------------------------- fees
def test_daily_volume_is_summed_and_the_busiest_schedule_wins():
    s1 = {"userCrossRate": "0.0004", "userAddRate": "0.0001",
          "dailyUserVlm": [{"date": "2026-09-01", "userCross": "100", "userAdd": "10"}]}
    s2 = {"userCrossRate": "0.0002", "userAddRate": "0.00005",
          "dailyUserVlm": [{"date": "2026-09-01", "userCross": "900", "userAdd": "90"}]}
    out = book._merge_fees([s1, s2])
    assert out["userCrossRate"] == "0.0002"              # the wallet that traded most
    assert out["dailyUserVlm"] == [{"date": "2026-09-01", "userCross": "1000.0", "userAdd": "100.0"}]


def test_merge_fees_survives_an_all_empty_set():
    assert book._merge_fees([None, None]) is None


# ---------------------------------------------------------------- the live book
def _pos(coin, szi, entry, wallet=None):
    p = dict(coin=coin, szi=str(szi), entryPx=str(entry), liquidationPx=None, marginUsed="10",
             unrealizedPnl="0", returnOnEquity="0", leverage={"value": 3, "type": "cross"}, cumFunding={"sinceOpen": "0"})
    if wallet:
        p["wallet"] = wallet
    return {"type": "oneWay", "position": p}


def _ctxs(coin, mark):
    return [{"universe": [{"name": coin}]}, [{"markPx": str(mark), "funding": "0"}]]


def test_positions_and_orders_are_stamped_with_their_wallet():
    cs = book._merge_clearinghouse([{"assetPositions": [_pos("ETH", 1, 100)], "marginSummary": {"accountValue": "100"}},
                                    {"assetPositions": [_pos("ETH", 2, 100)], "marginSummary": {"accountValue": "300"}}],
                                   [A, B])
    assert [ap["position"]["wallet"] for ap in cs["assetPositions"]] == [A, B]
    assert cs["marginSummary"]["accountValue"] == "400.0"


def test_one_wallets_stop_does_not_protect_another_wallets_position():
    """The protection audit's one job, and the failure would be in the dangerous direction: a bare
    position reported as covered because a sibling strategy happens to hold a stop on the same coin."""
    cs = {"assetPositions": [_pos("ETH", 1, 100, wallet=A), _pos("ETH", 1, 100, wallet=B)],
          "marginSummary": {"accountValue": "1000"}, "withdrawable": "0"}
    orders = [dict(coin="ETH", isTrigger=True, side="A", triggerPx="90", sz="1", wallet=A)]
    bk = metrics.open_book(cs, orders, _ctxs("ETH", 110))
    by_w = {p["wallet"]: p for p in bk["positions"]}
    assert by_w[A]["stop_covered_share"] == 1.0
    assert by_w[B]["stop_covered_share"] == 0.0
    assert bk["naked"] == ["ETH"]


def test_an_untagged_single_wallet_read_matches_stops_as_before():
    cs = {"assetPositions": [_pos("ETH", 1, 100)], "marginSummary": {"accountValue": "1000"}, "withdrawable": "0"}
    orders = [dict(coin="ETH", isTrigger=True, side="A", triggerPx="90", sz="1")]
    bk = metrics.open_book(cs, orders, _ctxs("ETH", 110))
    assert bk["positions"][0]["stop_covered_share"] == 1.0 and bk["naked"] == []


def test_spot_balances_are_summed_by_token():
    out = book._merge_spot([{"balances": [{"coin": "USDC", "total": "100", "hold": "0"}]},
                            {"balances": [{"coin": "USDC", "total": "50", "hold": "5"}]}])
    assert out["balances"] == [{"coin": "USDC", "total": "150.0", "hold": "5.0"}]


# ---------------------------------------------------------------- the instruction
def _skill():
    return " ".join((__import__("pathlib").Path(HERE).parent / "SKILL.md").read_text().split())


def test_the_skill_sends_a_senpi_user_to_the_whole_book_not_one_wallet():
    """Jason, 2026-09-23: "make it so for senpi users the quant desk report is across all their
    strategy wallets, not just one wallet and not just current". Running one strategy wallet answers
    a question they did not ask — and the leak worth money is usually only visible in the total."""
    sk = _skill()
    assert "--book 0x… 0x… 0x…" in sk
    assert "unions them into ONE desk" in sk
    assert "score my trading" in sk, "the trigger phrasing is not tied to the book"


def test_the_skill_says_closed_strategies_count():
    """"not just current". A strategy shut down six weeks ago still traded inside a 90-day window,
    and omitting it hides exactly the losses it was closed for."""
    sk = _skill()
    assert "Include CLOSED and PAUSED strategies, not just ACTIVE" in sk
    assert "`CLOSED`" in sk and "`PAUSED`" in sk


def test_the_skill_states_what_the_union_does_not_do():
    """A book has no leaderboard rank (rank is a per-address fact) and two wallets holding the same
    coin stay two positions. Both surprise a reader who is not told."""
    sk = _skill()
    assert "no leaderboard rank for a book" in sk
    assert "two rows in the live book" in sk


def test_a_book_is_the_readers_own_even_if_one_wallet_was_once_analysed():
    """Caught on a live integration run: `0xb4a3…` carried an `analyzed` mark from an earlier
    --other run, so `resolve_whose` returned "other" and the header read "Their desk — across 2
    wallets" to the person who owns them. A book comes from the reader's own `strategy_list`; one
    stale mark on one of N wallets must not re-voice the whole thing."""
    src = (_P(HERE).parent / "scripts" / "desk.py").read_text()
    i = src.index("whose = (")
    blk = src[i:i + 200]
    assert '"other" if a.other else "mine"' in blk and "if wallets" in blk, \
        "a --book run still resolves its voice through the address book"


# ── @shnoodles's two blocking findings on #773 ──────────────────────────────────────────────
def test_a_book_and_a_single_wallet_do_not_share_a_state_file():
    """Reproduced by @shnoodles: the state file keyed on `wallets[0]`, so `desk.py A` and
    `desk.py --book A B` both wrote desk-A.json and read each other back inside the 10-minute
    freshness window. Whichever ran first was served as the other — a single wallet returned as
    "across 2 wallets" (11,594 fills), or a two-wallet book returned as A alone (5,797).
    `--compare` reads the same file for an hour, so a book also surfaced as its first column."""
    import re as _re
    src = _P(HERE, "..", "scripts", "desk.py").read_text()
    i = src.index("state_path = os.path.join(")
    line = src[i:i + 200]
    assert "_state_key" in line, f"the state file is still keyed on a bare address: {line[:90]}"
    assert _re.search(r'book-"\s*\+\s*hashlib\.sha1', src), "a book is not keyed on its whole set"
    # the key must depend on EVERY wallet, and not on their order
    import hashlib
    k = lambda ws: "book-" + hashlib.sha1("|".join(sorted(ws)).encode()).hexdigest()[:16]
    a, b, c = "0xaaa", "0xbbb", "0xccc"
    assert k([a, b]) == k([b, a]), "the key depends on wallet order"
    assert k([a, b]) != k([a]), "a book and its first wallet share a key"
    assert k([a, b]) != k([a, b, c]), "adding a wallet does not change the key"


def test_one_indexed_wallet_does_not_erase_the_others_trades():
    """Reproduced by @shnoodles: `closed = rows` replaced the WHOLE book's public episodes with
    whatever senpi returned. Wallet B held 48 public closed trades and had no senpi rows; the book
    reported 3 trades, by_wallet B = 0, source "senpi discovery (3 closed positions)", indexed True.
    A failed read on B did the same, and `senpi_history_partial` is set only when a LATER page
    fails — so the only trace was a footnote."""
    src = _P(HERE, "..", "scripts", "desk.py").read_text()
    blk = src[src.index("        if rows:"):]
    blk = blk[:blk.index("meta[\"sources\"][\"trades\"]")]
    assert "public_fallback_wallets" in blk, "the book still takes senpi rows for every wallet"
    assert "closed = sorted(rows + _pub" in blk, \
        "a wallet senpi has no rows for still contributes nothing"
    assert "not indexed" in blk, "the source line does not name the fallback wallets"
    # and the single-wallet path must be untouched
    assert "closed, source = rows," in blk, "the single-wallet path changed shape"
