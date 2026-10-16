"""lookbackBars is the one knob that can silently kill this scanner.

`ratio_zscore` returns None when it has fewer than `lookback` bars, and a None z is
indistinguishable from "no signal" — the scanner just goes quiet forever. The 1h feed
(`market_get_asset_data`, candle_intervals ["1h"]) returns ~169 candles / 7 days, so a
lookback set anywhere near that silences chameleon with no error and no log line.

Also binds runtime.yaml to scoring's fallback: inputs are passed straight through as
entry_cfg, so runtime.yaml is what reaches a deployed wallet and the constant is only a
fallback. A value changed in one place and not the other is a config that does not exist.

Run: python3 -m pytest strategies/chameleon/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scoring  # noqa: E402

FEED_CANDLES = 169   # measured live 2026-09-18, market_get_asset_data ETH 1h
HEADROOM = 24        # a lookback this close to the feed is one API change from silence


def _runtime_lookback():
    rt = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
    scanner = next(s for s in rt["scanners"] if s.get("inputs", {}).get("lookbackBars"))
    return int(scanner["inputs"]["lookbackBars"])


def test_lookback_fits_the_candle_feed_with_room_to_spare():
    lb = _runtime_lookback()
    assert lb <= FEED_CANDLES - HEADROOM, (
        f"lookbackBars={lb} against a ~{FEED_CANDLES}-candle feed: ratio_zscore would return "
        "None and the scanner would go silent without logging anything")


def test_runtime_yaml_and_the_fallback_agree():
    assert _runtime_lookback() == scoring.DEFAULT_LOOKBACK_BARS


def test_a_lookback_longer_than_the_data_is_silence_not_a_signal():
    """The failure mode the headroom protects against, made explicit."""
    closes_a = [100.0 + i for i in range(60)]
    closes_b = [100.0] * 60
    assert scoring.ratio_zscore(closes_a, closes_b, 61) is None     # more bars than exist
    assert scoring.ratio_zscore(closes_a, closes_b, 48) is not None  # enough


def test_the_shipped_lookback_actually_computes_on_a_real_length_feed():
    lb = _runtime_lookback()
    closes_a = [100.0 + (i % 7) for i in range(FEED_CANDLES)]
    closes_b = [100.0 + (i % 5) for i in range(FEED_CANDLES)]
    assert scoring.ratio_zscore(closes_a, closes_b, lb) is not None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} CHAMELEON LOOKBACK TESTS PASS")
