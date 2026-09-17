"""The build guide states the time units an author compares against the clock.

`market_get_asset_data` candle `t`/`T` are epoch milliseconds, while `time.time()` and
`discovery_get_trader_history` `openTime`/`closeTime` are epoch seconds. Mix them up and nothing
errors: compare a candle's `t` to `time.time()` and no candle ever closes, so the scanner never emits;
divide a seconds `closeTime` by 1000 and every age, cooldown and daily-loss window reads wrong.
"""
import os

REFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references")


def _read(name):
    return open(os.path.join(REFS, name), encoding="utf-8").read()


def test_candle_times_are_documented_as_milliseconds_with_the_closed_candle_check():
    text = _read("creating-a-strategy.md")
    assert "**Candle times are epoch milliseconds; `time.time()` is seconds.**" in text
    assert "`t` is the candle's open time and `T` its close time, both in ms" in text
    assert '`_f(c["t"]) / 1000 + candle_seconds <= now`' in text
    assert "Comparing `t` to `time.time()` directly means no candle ever closes" in text


def test_trader_history_times_stay_documented_as_seconds():
    text = _read("risk-gates.md")
    assert "`discovery_get_trader_history.openTime`/`closeTime`" in text
    assert "are **Unix epoch seconds**" in text
