"""wolf fidelity — regime tally + directional scoring (pure scoring.py).

Exercises the ported v2 logic on synthetic candle/trend snapshots:
  - tally_regime declares RISK_ON / RISK_OFF / NEUTRAL by net cross-asset votes
    against regimeThreshold (no single asset flips the book).
  - score_directional gates 4h backbone + 1h confirm + 24h momentum + RSI room,
    in the mandated direction.
Imports the risk_on book's scoring.py (shared verbatim by both books)."""

import importlib.util
import os

_SCORING = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "risk_on", "scanners", "scoring.py",
)
_spec = importlib.util.spec_from_file_location("wolf_scoring", _SCORING)
scoring = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scoring)


_PROBES = [
    {"label": "equities", "risk_on_when": "BULLISH"},
    {"label": "oil", "risk_on_when": "BEARISH"},
    {"label": "gold", "risk_on_when": "BEARISH"},
    {"label": "btc", "risk_on_when": "BULLISH"},
    {"label": "dollar", "risk_on_when": "BEARISH"},
]


def _up_candles(n=8):
    # monotone rising -> trend_structure BULLISH
    closes = [100 + 2 * i for i in range(n)]
    return [{"high": c + 1, "low": c - 1, "close": c} for c in closes]


def _down_candles(n=8):
    closes = [100 - 2 * i for i in range(n)]
    return [{"high": c + 1, "low": c - 1, "close": c} for c in closes]


def test_tally_full_risk_on():
    # equities BULLISH(on), oil BEARISH(on), gold BEARISH(on), btc BULLISH(on), dollar BEARISH(on)
    trends = {"equities": "BULLISH", "oil": "BEARISH", "gold": "BEARISH",
              "btc": "BULLISH", "dollar": "BEARISH"}
    r = scoring.tally_regime(trends, _PROBES, threshold=2)
    assert r["regime"] == "RISK_ON" and r["net"] == 5


def test_tally_full_risk_off():
    trends = {"equities": "BEARISH", "oil": "BULLISH", "gold": "BULLISH",
              "btc": "BEARISH", "dollar": "BULLISH"}
    r = scoring.tally_regime(trends, _PROBES, threshold=2)
    assert r["regime"] == "RISK_OFF" and r["net"] == -5


def test_single_asset_cannot_flip_book():
    # one risk_on vote, rest neutral -> net +1 < threshold 2 -> NEUTRAL (no single asset flips)
    trends = {"equities": "BULLISH", "oil": "NEUTRAL", "gold": "NEUTRAL",
              "btc": "NEUTRAL", "dollar": "NEUTRAL"}
    r = scoring.tally_regime(trends, _PROBES, threshold=2)
    assert r["regime"] == "NEUTRAL" and r["net"] == 1


def test_no_data_abstains():
    trends = {"equities": "no_data", "oil": "no_data", "gold": "BULLISH",
              "btc": "BULLISH", "dollar": "no_data"}
    r = scoring.tally_regime(trends, _PROBES, threshold=2)
    # gold BULLISH = risk_off vote (-1); btc BULLISH = risk_on (+1) -> net 0 -> NEUTRAL
    assert r["regime"] == "NEUTRAL" and r["net"] == 0


def test_score_long_requires_bullish_4h():
    inputs = {"momThresholdPct": 1.0, "rsiOverbought": 78, "rsiOversold": 22}
    # 4h bearish but want LONG -> None (regime is the green light; the name must turn up)
    th = scoring.score_directional("BTC", _up_candles(), _down_candles(),
                                   {"markPx": 120, "prevDayPx": 100}, "LONG", inputs)
    assert th is None


def test_score_long_clean_uptrend():
    inputs = {"momThresholdPct": 1.0, "rsiOverbought": 78, "rsiOversold": 22}
    th = scoring.score_directional("BTC", _up_candles(), _up_candles(),
                                   {"markPx": 120, "prevDayPx": 100}, "LONG", inputs)
    assert th is not None
    assert th["direction"] == "LONG"
    assert th["score"] >= 5            # base 2 + 1h confirm 2 + mom 2 + rsi room 1


def test_score_short_clean_downtrend():
    inputs = {"momThresholdPct": 1.0, "rsiOverbought": 78, "rsiOversold": 22}
    th = scoring.score_directional("SOL", _down_candles(), _down_candles(),
                                   {"markPx": 80, "prevDayPx": 100}, "SHORT", inputs)
    assert th is not None
    assert th["direction"] == "SHORT"
    assert th["score"] >= 5
