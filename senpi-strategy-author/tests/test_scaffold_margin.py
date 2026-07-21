#!/usr/bin/env python3
"""Guards the marginPct PERCENT-not-FRACTION contract in the author scaffold + validator.

`marginPct` is a PERCENT in (0,100] (Runtime 3.0 sizes `(marginPct/100) × withdrawable`). The v2
convention was a FRACTION (0.10); the scaffold doc carried that stale form, so every from-scratch
build that copied it shipped 100× undersized — sub-$10 notional, every order rejected, funds-but-
never-trades. These tests lock (a) the validator that now flags it and (b) the doc that must never
teach it again.

    python3 -m pytest senpi-strategy-author/tests/
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import validate_strategy as vs  # noqa: E402

_DOC = os.path.join(HERE, "..", "references", "creating-a-strategy.md")


def test_offenders_flags_fraction_passes_percent():
    off = vs.margin_fraction_offenders
    assert off({"scanners": [{"inputs": {"marginPct": 0.10}}]}) == [("scanners[0].inputs.marginPct", 0.10)]
    assert off({"strategy": {"margin_pct": 0.2}}) == [("strategy.margin_pct", 0.2)]
    assert off({"inputs": {"marginPctBase": 0.15, "marginPctCap": 25}}) == [("inputs.marginPctBase", 0.15)]
    # legit percents + non-margin keys never flag
    assert off({"strategy": {"margin_pct": 20}, "inputs": {"marginPctBase": 18, "marginPctCap": 25}}) == []
    assert off({"inputs": {"minScore": 0.5, "leverage": 0.5}}) == []


def test_scaffold_doc_teaches_percent_not_fraction():
    """Regression on the copy-source: creating-a-strategy.md must NEVER assign a marginPct/margin_pct a
    value <= 1 (the fraction slip that was the root cause). Percent forms like `marginPct: 10` pass."""
    text = open(_DOC, encoding="utf-8").read()
    bad = re.findall(r"margin_?pct[\"']?\s*[:,]\s*0*\.\d+", text, re.I)
    assert not bad, f"fraction-form marginPct still in creating-a-strategy.md (must be a PERCENT): {bad}"


def test_candle_key_bug_flags_long_without_short():
    b = vs.candle_key_bug
    # the reported bug: candle close read by the long key with no `c` access anywhere
    assert b('[c.get("close", 0) for c in candles if c.get("close") is not None]') == [("close", "c")]
    assert b('hi = row["high"]') == [("high", "h")]
    # working idioms are NOT flagged
    assert b('float(c.get("close", c.get("c", 0)) or 0)') == []   # fallback keeps a `c` access
    assert b('float(candle["c"])') == []                          # direct short key
    # volume is excluded — scanners read a `volume` field from market/leaderboard rows, not candles
    assert b('vol = market["volume"]') == []
    assert b('n = m.get("avg_volume_6h", 0)') == []


def test_scaffold_doc_documents_candle_c_key():
    """The scaffold must state the candle schema so agents don't guess `close` (the root cause)."""
    text = open(_DOC, encoding="utf-8").read()
    assert 'candle["c"]' in text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
