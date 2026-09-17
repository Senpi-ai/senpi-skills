"""Barracuda refilled a freed slot within minutes, and paid for it.

All 4 slots reopened the moment an exit landed: 33% of opens came less than 15 minutes after a close
and 44% of entries less than 20 minutes after the previous one. Over 30 days its fees were 10.9% of
capital against a pre-fee result of about zero. The gate that reads like it should have stopped this,
`cooldown_seconds: 1200`, is the CONSECUTIVE-LOSS cooldown (risk-gates.md gate 3), not a pause between
entries, and the per-asset cooldown only stops the SAME name coming back.

The scanner now holds the book it saw last tick: when a name disappears, it stops emitting for
pause_after_close_seconds. The whole-book take-profit also exits as a fee-optimized limit with taker
fallback instead of a market order — it fired 105 times and closed 329 positions in the window.
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))


def test_a_close_starts_the_pause_and_it_expires():
    paused, at = scan._pause_after_close({"BTC"}, set(), 0.0, 1000.0, 1200)
    assert paused is True and at == 1000.0
    assert scan._pause_after_close(set(), set(), 1000.0, 1900.0, 1200)[0] is True    # 15m later
    assert scan._pause_after_close(set(), set(), 1000.0, 2300.0, 1200)[0] is False   # 21m later


def test_opening_a_name_is_not_a_close():
    assert scan._pause_after_close({"BTC"}, {"BTC", "ETH"}, 0.0, 1000.0, 1200) == (False, 0.0)


def test_the_first_tick_has_no_book_to_diff():
    assert scan._pause_after_close(None, {"BTC"}, 0.0, 1000.0, 1200) == (False, 0.0)


def test_the_pause_is_configured_at_twenty_minutes():
    inputs = next(s for s in RUNTIME["scanners"] if s["name"] == "pump_signals")["inputs"]
    assert inputs["pause_after_close_seconds"] == 1200


def test_the_whole_book_take_profit_exits_as_a_fee_optimized_limit():
    act = next(a for a in RUNTIME["actions"] if a["name"] == "close_all_action")
    assert act["params"]["order_type"] == "FEE_OPTIMIZED_LIMIT"
    assert act["params"]["fee_optimized_limit_options"]["ensure_execution_as_taker"] is True
