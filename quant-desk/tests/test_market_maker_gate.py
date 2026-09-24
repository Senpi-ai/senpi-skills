"""quant-desk — refusing a market maker's book, on the signal that actually separates them.

@betashop, 2026-09-24: "we definitely don't want users running quant desk on MM's it will lead to
bad results and clog our systems." The `userRole` gate (1.29.0) covers market makers that are
VAULTS. It returns `user` for one quoting from a plain address, and those are live on the
leaderboard right now.

The first attempt here gated on maker share — market makers rest, they don't cross — and measuring
it killed the idea. Over 7 days on 13 real wallets:

    HLP Strategy B (a real MM)   41% maker    0.00 bp
    whale B        (a real user) 91% maker    7.91 bp

Maker share would have missed the one that burned a user session and refused a legitimate trader.
The effective fee rate separates them with nothing in the gap, because it is not a behaviour — it
is a contract with the venue that retail cannot obtain.
"""
import os
import sys
from pathlib import Path as _P

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import desk  # noqa: E402


def fills(n, fee_bp, coin="BTC", px=100.0, sz=1.0):
    """n fills priced so the effective rate is exactly `fee_bp`."""
    fee = px * sz * fee_bp / 1e4
    return [dict(coin=coin, sz=str(sz), px=str(px), fee=str(fee), time=i, crossed=True)
            for i in range(n)]


# ---------------------------------------------------------------- the measured separation
def test_the_rates_we_measured_land_on_the_right_side():
    """Real numbers from 13 live wallets. Nothing sits between 0.41 and 1.48."""
    for bp, who in ((0.00, "HLP Strategy B"), (0.285, "0x956a… plain-address MM"), (0.41, "drkmttr")):
        assert desk.market_maker_rate(fills(500, bp)) <= desk.MM_FEE_BP, f"{who} not caught"
    for bp, who in ((1.48, "trader"), (2.07, "trader"), (2.93, "whale A"), (8.31, "whale B")):
        assert desk.market_maker_rate(fills(500, bp)) > desk.MM_FEE_BP, f"{who} wrongly refused"


def test_a_maker_rebate_is_the_strongest_signal_not_an_absolute_value():
    """Negative fees are money EARNED for providing liquidity — the most market-maker-ish thing on
    the venue. abs() would have hidden the clearest case."""
    r = desk.market_maker_rate(fills(500, -1.5))
    assert r is not None and r < 0 and r <= desk.MM_FEE_BP


def test_too_few_fills_is_unjudged_rather_than_guessed():
    """Under a couple of hundred fills the rate is noise, not a fee schedule. A quiet wallet gets
    its desk rather than an accusation."""
    assert desk.market_maker_rate(fills(desk.MM_MIN_FILLS - 1, 0.0)) is None
    assert desk.market_maker_rate([]) is None


def test_zero_volume_does_not_divide_by_zero():
    assert desk.market_maker_rate([dict(coin="BTC", sz="0", px="0", fee="0", time=i)
                                   for i in range(500)]) is None


def test_spot_fills_are_not_priced_as_perp_activity():
    """The desk is a perp desk; spot fills carry their own schedule and must not drag the rate."""
    spot = [dict(coin="@107", sz="1", px="100", fee="0.0", time=i) for i in range(500)]
    assert desk.market_maker_rate(spot) is None


# ---------------------------------------------------------------- what the gate must not become
def test_the_rule_does_not_gate_on_breadth_or_volume():
    """A systematic trader legitimately runs 80 names; drkmttr is a market maker on 11. Coin count
    and fill count are not evidence, and wiring them in would refuse real readers."""
    import ast, textwrap
    src = _P(HERE, "..", "scripts", "desk.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "market_maker_rate")
    # the executable body only — the docstring explains the reasoning and names these words
    code = "\n".join(ast.unparse(st) for st in fn.body if not (
        isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str)))
    for banned in ("coins", "breadth", "crossed", "maker"):
        assert banned not in code, f"the rate calculation started depending on {banned!r}"


def test_the_gate_runs_before_the_expensive_work():
    """Bailing early is half the point: the candle pull and the two cohort reads are ~60-90s of a
    ~120s run, and that is the load @betashop asked us to stop spending on market makers."""
    src = _P(HERE, "..", "scripts", "desk.py").read_text()
    gate = src.index("NotATraderError({")
    assert gate < src.index("hl.candles("), "the gate fires after the tape read"
    assert gate < src.index("smart_money.proven_cohort"), "the gate fires after the cohort reads"
    assert gate < src.index("coins = _tape("), "the gate fires after the tape cap"


def test_force_still_reads_it():
    src = _P(HERE, "..", "scripts", "desk.py").read_text()
    i = src.index("_bp = market_maker_rate(fills)")
    assert "not force" in src[i:i + 200], "--force cannot override the market-maker gate"


def test_it_exits_the_same_way_as_the_vault_gate():
    """Both mean 'this address is not a trader's book'; an agent should not need to tell them
    apart to do the right thing."""
    src = _P(HERE, "..", "scripts", "desk.py").read_text()
    i = src.index("except NotATraderError")
    assert "return 4" in src[i:i + 300]
    assert '"not_a_trader"' in src[src.index("NotATraderError({"):][:200]


def test_the_refusal_explains_itself_in_the_readers_terms():
    """A bare refusal reads as a broken product. Name the number, give the retail comparison, and
    offer the thing they probably wanted."""
    src = _P(HERE, "..", "scripts", "desk.py").read_text()
    # join adjacent string literals first: the payload is a wrapped f-string, and where the
    # author happened to break the line must not decide whether the copy is correct
    import re
    raw = src[src.index('"not_a_trader": "market_maker"'):][:1800]
    blk = " ".join(re.sub(r'"\s*f?"', "", raw).split())
    assert "say_to_the_reader" in blk
    assert "basis points" in blk and "Retail pays" in blk
    assert "your own wallet" in blk, "the reader is refused without being offered the desk they wanted"
