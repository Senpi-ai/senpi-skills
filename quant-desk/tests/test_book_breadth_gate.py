"""quant-desk — refusing a book the desk cannot honestly read.

@betashop, 2026-09-24: "we definitely don't want users running quant desk on MM's it will lead to
bad results and clog our systems." The `userRole` gate (1.29.0) covers market makers that are
VAULTS. It returns `user` for one quoting from a plain address, and those are live on the
leaderboard right now.

Two attempts at identifying those readers failed, and the way they failed is the reason this file
now tests something else.

  1. MAKER SHARE. Market makers rest, they don't cross — except HLP Strategy B (a real MM) was 41%
     maker while a real user was 91%. Measuring it killed it.
  2. EFFECTIVE FEE RATE. It looked structural: a gap between 0.41 and 1.48 bp across 13 wallets,
     and paying nothing to trade reads like a venue agreement retail cannot obtain. Both halves
     were wrong. (@im-vignesh, #763.) Hyperliquid PUBLISHES the schedule — `userFees.feeSchedule
     .tiers.vip` floors the maker fee at 0.0 above $500M of 14-day volume — so a patient limit
     trader at scale crosses 0.5 bp at ~18% taker share on fees anyone can get. Re-sampled over the
     top 30 of the leaderboard, 25 wallets with >=200 perp fills:
         -0.30 -0.25 -0.21 -0.10 -0.04 -0.02 0.05 0.11 0.15 0.21 0.24
          0.42  0.50  0.57  0.78  0.79  1.04 1.23 1.24 1.36 1.54 1.89 2.37 2.47 2.82
     NINE sit inside the claimed gap. It was an artefact of the sample, and the rule would have
     refused 13 of 25 — including VIP traders at 0.42 and 0.50 bp, while telling them they had a
     market-maker agreement, which is false.

The lesson both times: a CLASSIFIER of who someone is can be wrong, and when it is wrong it insults
a real reader. So the gate stopped classifying. It now states what this tool can do — the desk pulls
hourly candles per coin and caps that at MAX_TAPE_COINS, so past the cap it is scoring a SAMPLE of a
book while printing a verdict about the book. That is measurable, it is the actual harm, and it is
equally true of a systematic trader on 200 names and of a quoting engine. Both get the same honest
sentence instead of an accusation.

Measured on those same 29 wallets: the breadth line refuses 1 (115 coins, 5,514 fills/h) where the
fee line refused 13. It still refuses the book that prompted the work — 172 coins, 177 positions,
13,722 fills — and serves every VIP trader the fee line turned away.
"""
import os
import re
import sys
from pathlib import Path as _P

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import desk  # noqa: E402

SRC = _P(HERE, "..", "scripts", "desk.py").read_text()


def fills(n, fee_bp, coin="BTC", px=100.0, sz=1.0):
    fee = px * sz * fee_bp / 1e4
    return [dict(coin=coin, sz=str(sz), px=str(px), fee=str(fee), time=i, crossed=True)
            for i in range(n)]


def wide(n_coins, per_coin=10):
    out = []
    for c in range(n_coins):
        out += [dict(coin=f"C{c:03d}", sz="1", px="100", fee="0.02", time=i, crossed=True)
                for i in range(per_coin)]
    return out


# ---------------------------------------------------------------- the gate itself
def test_a_book_wider_than_the_tape_is_refused():
    """The book that prompted this: 172 coins. The desk reads 60, so a score would be about a
    third of it."""
    wide_book = wide(172)
    coins = len({f["coin"] for f in wide_book})
    assert coins > desk.MAX_TAPE_COINS
    assert coins / desk.MAX_TAPE_COINS > 2, "the shortfall should be obvious, not marginal"


def test_the_widest_book_the_desk_can_fully_read_is_served():
    """At or under the cap the desk sees everything, so there is nothing to refuse — including the
    47- and 53-coin books in the sample, which are plausibly market makers and get a COMPLETE read
    that costs no more than anyone else's."""
    assert desk.MAX_TAPE_COINS >= 60, "the served ceiling moved; re-check the measured sample"


def test_the_refusal_makes_no_claim_about_who_the_reader_is():
    """Both failed attempts refused people by identity, and the fee one said something false while
    doing it. The refusal must describe the TOOL's limit, not the reader."""
    blk = SRC[SRC.index('"not_a_trader": "book_wider_than_the_desk_reads"'):][:2000]
    flat = " ".join(re.sub(r'"\s*f?"', "", blk).split())
    for accusation in ("market-maker agreement", "market maker", "quoting engine",
                       "not a retail schedule", "Retail pays"):
        assert accusation not in flat, f"the refusal still accuses the reader: {accusation!r}"
    assert "coins" in flat and "tape" in flat.lower()


def test_the_refusal_explains_itself_and_offers_a_way_forward():
    blk = SRC[SRC.index('"not_a_trader": "book_wider_than_the_desk_reads"'):][:2000]
    flat = " ".join(re.sub(r'"\s*f?"', "", blk).split())
    assert "say_to_the_reader" in flat
    assert "I would rather say that" in flat, "a bare refusal reads as a broken product"
    assert "give me those" in flat, "the reader is refused without being offered anything"
    assert "readable_share" in flat, "the refusal does not quantify how much it could see"


def test_force_still_reads_it():
    i = SRC.index("if len(_coins) > MAX_TAPE_COINS")
    assert "not force" in SRC[i:i + 120], "--force cannot override the breadth gate"


def test_the_gate_runs_before_the_expensive_work():
    """Bailing early is half the point: the candle pull and the two cohort reads are ~60-90s of a
    ~120s run, and that is the load @betashop asked us to stop spending."""
    gate = SRC.index("NotATraderError({")
    assert gate < SRC.index("hl.candles("), "the gate fires after the tape read"
    assert gate < SRC.index("smart_money.proven_cohort"), "the gate fires after the cohort reads"


def test_it_exits_the_same_way_as_the_vault_gate():
    i = SRC.index("except NotATraderError")
    assert "return 4" in SRC[i:i + 300]
    assert '"not_a_trader"' in SRC[SRC.index("NotATraderError({"):][:220]


# ---------------------------------------------------------------- the fee rate, demoted
def test_the_measured_vip_rates_are_no_longer_grounds_to_refuse():
    """The nine wallets inside the old 'gap', plus the two at 0.42 and 0.50 the rule would have
    turned away. None of these may appear in a refusal condition any more."""
    assert not re.search(r"_bp\s*(<=|<)\s*MM_FEE_BP", SRC), "the fee threshold still refuses"
    assert "MM_FEE_BP" not in SRC, "the fee threshold constant is still live"


def test_a_maker_rebate_is_disclosed_rather_than_refused():
    """Being PAID to quote is real and worth saying — the published schedule floors the maker fee
    at 0.0, so a negative rate is the one thing a public tier cannot produce. It colours how the
    edge figures should be read; it is not grounds to turn someone away."""
    r = desk.market_maker_rate(fills(500, -1.5))
    assert r is not None and r < 0
    i = SRC.index("if _bp is not None and _bp < 0:")
    blk = " ".join(SRC[i:i + 700].split())
    assert "warnings" in blk, "a rebate is not surfaced at all"
    assert "NotATraderError" not in blk, "a rebate still refuses the reader"


def test_too_few_fills_is_unjudged_rather_than_guessed():
    assert desk.market_maker_rate(fills(desk.MM_MIN_FILLS - 1, 0.0)) is None
    assert desk.market_maker_rate([]) is None


def test_zero_volume_does_not_divide_by_zero():
    assert desk.market_maker_rate([dict(coin="BTC", sz="0", px="0", fee="0", time=i)
                                   for i in range(500)]) is None


def test_spot_fills_are_not_priced_as_perp_activity():
    spot = [dict(coin="@107", sz="1", px="100", fee="0.0", time=i) for i in range(500)]
    assert desk.market_maker_rate(spot) is None


def test_breadth_is_counted_on_perps_only():
    """Spot names must not inflate the coin count into a refusal."""
    i = SRC.index("_coins = sorted({")
    assert "metrics.is_perp" in SRC[i:i + 160], "spot fills can push a reader over the cap"
