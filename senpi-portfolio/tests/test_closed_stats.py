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


def _row(coin, szi, pnl):
    # the real discovery_get_trader_history shape: strings, signed szi (>0 closed a long)
    return {"coin": coin, "szi": str(szi), "realizedPnl": str(pnl), "closeTime": 1789380778,
            "entryPx": "1", "exitPx": "1"}


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
        + [_row("SOL", 1, 2.0)] * 4 + [_row("HYPE", -2, 1.5)] * 5
        + [_row("DOGE", -50, -0.4)] * 2 + [_row("ARB", 5, -0.7)] * 3
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


def test_a_missing_size_is_neither_long_nor_short():
    row = _row("BTC", 1, 2.0)
    del row["szi"]
    closed = portfolio.fetch_closed(_Client([row, _row("ETH", -1, 1.0)]), "0x" + "c" * 40, {})
    assert closed["longs"] == 0 and closed["shorts"] == 1 and closed["unknown_side"] == 1
    assert closed["recent"][0]["direction"] is None and closed["recent"][1]["direction"] == "short"
