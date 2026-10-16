"""The scan contract states that candle `t`/`T` are epoch milliseconds, and that the market-data cast
changes their type, not their unit. `time.time()` is seconds, so a scanner that compares `t` to it
unconverted never sees a closed candle and never emits."""
import os

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "scan-contract.md")


def test_candle_times_are_documented_as_epoch_milliseconds():
    text = open(REF, encoding="utf-8").read()
    assert "`t` (candle open) and `T` (candle close) are **epoch milliseconds**" in text
    assert "the cast changes their type, not their unit" in text
    assert '`_f(c["t"]) / 1000 + candle_seconds <= time.time()`' in text
