"""The closed block prints the record — winners / losers / win rate / longs / shorts over the full
pull — so the narration quotes it instead of extrapolating from the five recent trades.

    python3 -m pytest senpi-portfolio/tests/test_closed_stats.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import portfolio  # noqa: E402

SKILL = os.path.join(HERE, "..", "SKILL.md")


def _row(coin, size, pnl, side="long"):
    # the real discovery_get_trader_history shape: strings, the side labelled in `type` ("Close Long" /
    # "Close Short"), and an UNSIGNED at-close `szi` — a closed short carries a positive size too
    return {"coin": coin, "type": "Close " + side.capitalize(), "szi": str(size), "realizedPnl": str(pnl),
            "closeTime": 1789380778, "entryPx": "1", "exitPx": "1"}


# A closed short exactly as the API sends it (values altered): the side is in `type`, `szi` is positive.
REAL_CLOSED_SHORT = {
    "closedOrderId": "545000000000", "coin": "ENA", "coinDisplayName": "ENA", "entryPx": "0.1423",
    "exitPx": "0.14266", "leverage": {"type": "", "value": 5}, "maxLeverage": 5, "openTime": 1789476612,
    "closeTime": 1789477190, "szi": "1877", "realizedPnl": "-0.67572", "marginUsed": "53.40065",
    "type": "Close Short", "totalFills": "2", "totalFees": "0.498496", "totalBuilderFees": "0.267434",
    "totalHyperliquidFees": "0.231062",
}


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def mcp_call(self, name, **kw):
        assert name == "discovery_get_trader_history"
        return {"success": True, "data": {"closedPositions": self.rows}}


# 20 closed trades: 9 winners, 10 losers, 1 flat; 13 longs, 7 shorts — and the five most recent are all
# losing longs, which is exactly the sample a narration must not turn into a win rate.
ROWS = ([_row("ZEC", 0.35, -3.815), _row("PONS", 12, -4.95), _row("LIT", 3, -0.82), _row("BTC", 0.001, -0.59),
         _row("ETH", 0.1, -1.1)]
        + [_row("SOL", 1, 2.0)] * 4 + [_row("HYPE", 2, 1.5, side="short")] * 5
        + [_row("DOGE", 50, -0.4, side="short")] * 2 + [_row("ARB", 5, -0.7)] * 3
        + [_row("LINK", 1, 0.0)])


def test_the_record_is_over_the_whole_pull_not_the_recent_sample():
    meta = {}
    closed = portfolio.fetch_closed(_Client(ROWS), "0xwallet", meta)
    assert closed["trade_count"] == 20
    assert closed["winners"] == 9
    assert closed["losers"] == 10          # the flat LINK close is neither
    assert closed["win_rate_pct"] == 45.0
    assert closed["longs"] == 13 and closed["shorts"] == 7
    assert len(closed["recent"]) == portfolio.CLOSED_HISTORY_CAP
    assert all(r["direction"] == "long" and r["realized_pnl"] < 0 for r in closed["recent"])
    assert closed["realized_pnl"] == round(sum(float(r["realizedPnl"]) for r in ROWS), 2)
    assert not meta.get("warnings")


def test_a_failed_read_prints_null_counts_not_zero():
    class _Down:
        def mcp_call(self, name, **kw):
            raise RuntimeError("timeout")
    meta = {}
    closed = portfolio.fetch_closed(_Down(), "0xwallet", meta)
    assert closed["trade_count"] is None
    assert closed["winners"] is None and closed["win_rate_pct"] is None and closed["shorts"] is None
    assert meta["warnings"]


def test_an_empty_history_has_no_win_rate():
    closed = portfolio.fetch_closed(_Client([]), "0xwallet", {})
    assert closed["trade_count"] == 0 and closed["winners"] == 0 and closed["win_rate_pct"] is None


def test_skill_says_quote_the_record_and_route_other_templates_through_discover():
    text = open(SKILL, encoding="utf-8").read()
    for needle in ("Numbers come from the engine; other strategies come from discover",
                   "from memory"):
        assert needle in text, needle


def test_malformed_rows_are_not_in_the_denominator():
    # two losing trades plus two rows the loop cannot read: the record is 0 of 2, never 0 of 4
    meta = {}
    closed = portfolio.fetch_closed(_Client(ROWS[:2] + ["junk", None]), "0x" + "b" * 40, meta)
    assert closed["trade_count"] == 2 and closed["losers"] == 2 and closed["winners"] == 0
    assert closed["win_rate_pct"] == 0.0


def test_a_closed_short_carries_a_positive_size_and_still_reads_as_a_short():
    # the side lives in `type`; the at-close size is unsigned, so a sign test would call every short a
    # long. The label is matched case-insensitively and is enough on its own — no size needed.
    lower = _row("ETH", 1, 1.0, side="short")
    lower["type"] = "close SHORT"
    label_only = _row("HYPE", 1, 1.0, side="short")
    del label_only["szi"]
    closed = portfolio.fetch_closed(_Client([REAL_CLOSED_SHORT, lower, label_only, _row("SOL", 1, 2.0)]),
                                    "0x" + "d" * 40, {})
    assert closed["shorts"] == 3 and closed["longs"] == 1 and closed["unknown_side"] == 0
    assert [r["direction"] for r in closed["recent"]] == ["short", "short", "short", "long"]
    assert closed["recent"][0]["asset"] == "ENA" and closed["recent"][0]["realized_pnl"] == -0.68


def test_without_a_side_label_the_side_is_unknown_whatever_the_size_or_pnl_suggests():
    # Only a label decides the side. Each unlabelled row below would read as a side under a guess, and
    # each guess can be wrong: a signed size, the real feed's unsigned size (positive on a short too),
    # PnL against the price move (a long up 100 -> 100.2 that fees turn into a -0.05 loss reads "short"),
    # and a buy / sell side (on a closed row that can be the closing fill, the opposite side).
    signed, unsigned, fee_negative_long, closing_fill = (_row("ETH", -1, 1.0), _row("BTC", 1, 2.0),
                                                         _row("SOL", 0, -0.05), _row("ARB", 1, 1.0))
    fee_negative_long["entryPx"], fee_negative_long["exitPx"] = "100", "100.2"
    closing_fill["side"] = "sell"
    for r in (signed, unsigned, fee_negative_long, closing_fill):
        del r["type"]
    rows = [signed, unsigned, fee_negative_long, closing_fill, _row("HYPE", 1, 1.0, side="short")]
    closed = portfolio.fetch_closed(_Client(rows), "0x" + "c" * 40, {})
    assert closed["longs"] == 0 and closed["shorts"] == 1 and closed["unknown_side"] == 4
    # an unknown side still counts in the record
    assert closed["trade_count"] == 5 and closed["winners"] == 4 and closed["losers"] == 1
    assert [r["direction"] for r in closed["recent"]] == [None, None, None, None, "short"]
