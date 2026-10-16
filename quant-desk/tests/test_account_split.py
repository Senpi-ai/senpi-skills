"""The account value names its perps balance on a standard account (R1 dev E2E round 2, N6).

Live, on a standard-mode ("default") wallet the agent printed a spot table (≈$577) under a "~$1,041"
total — the total was right (perps $463.94 + spot $577.15) but the perps USDC was nowhere. The desk now
reads Hyperliquid's account mode (`userAbstraction`) and splits the value: standard = perps + spot, each
its own figure; unified / portfolio margin = spot holds the perps margin, counted once (never added)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import hl_api  # noqa: E402
import metrics  # noqa: E402
import render  # noqa: E402


def _book(av=1041.09, perps=463.94):
    return {"account_value": av, "account_value_perps": perps}


def test_a_standard_account_names_perps_and_spot_and_they_sum_to_the_value():
    for mode in ("default", "disabled"):
        b = _book()
        b.update(metrics.account_split(b, mode))
        assert b["account_mode"] == mode and b["account_value_spot"] == 577.15
        line = render.account_value_phrase(b)
        assert line == "Account value **$1,041** = perps **$464** + spot **$577**", line


def test_a_unified_or_portfolio_margin_account_never_adds_perps_to_spot():
    for mode, word in (("unifiedAccount", "unified account"), ("portfolioMargin", "portfolio margin")):
        b = _book(av=900.0, perps=900.0)
        b.update(metrics.account_split(b, mode))
        assert b["account_value_spot"] is None, "the spot side already holds the perps margin"
        line = render.account_value_phrase(b)
        assert line.startswith("Account value **$900**") and word in line and "counted once" in line
        assert "+ spot" not in line


def test_an_unread_mode_keeps_the_plain_value():
    b = _book()
    b.update(metrics.account_split(b, None))
    assert b["account_mode"] is None and b["account_value_spot"] is None
    assert render.account_value_phrase(b) == "Account value **$1,041** (perps equity $464)"


def test_a_standard_account_with_no_spot_has_no_split():
    b = _book(av=463.94, perps=463.94)
    b.update(metrics.account_split(b, "default"))
    assert b["account_value_spot"] is None
    assert render.account_value_phrase(b) == "Account value **$464**"


def test_the_trader_read_asks_hyperliquid_for_the_account_mode():
    fx = hl_api.HLFixture({"hl::userAbstraction::0xabc": "default"}, now_ms=1)
    assert fx._optional({"type": "userAbstraction", "user": "0xabc"}) == "default"
    src = open(os.path.join(HERE, "..", "scripts", "hl_api.py"), encoding="utf-8").read()
    assert '"userAbstraction": self._optional({"type": "userAbstraction", "user": addr})' in src


def test_the_skill_keeps_the_perps_line_in_any_balance_table():
    with open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8") as f:
        sk = " ".join(f.read().split())
    assert "any balance table you show carries the perps line" in sk
    assert "never a spot-only table under that total" in sk
