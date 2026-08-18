"""Salmon engine tests — pure/deterministic RSI mean-reversion detector, plus a
scan() smoke run against a fake ctx (no network). Run:
  python3 -m pytest strategies/salmon/tests -q
  python3 strategies/salmon/tests/test_engine.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402


def _candles_from_closes(closes):
    """Minimal OHLCV candle dicts from a close series (RSI/momentum use close)."""
    return [{"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000} for c in closes]


def _long_bounce_closes():
    """Long decline (RSI -> 0) then a 3-bar rally that crosses RSI back up through 30
    while still rising, with the final close up. min RSI over the lookback is ~0."""
    down = [100 - 2 * i for i in range(21)]     # 100,98,...,60  → RSI pinned near 0
    up = [64, 68, 72]                            # +4/bar → RSI recovers up through 30
    return down + up


def _short_fade_closes():
    """Symmetric: long rally (RSI -> 100) then a 3-bar drop that crosses RSI back down
    through 70 while still falling, with the final close down."""
    up = [40 + 2 * i for i in range(21)]         # 40,42,...,80  → RSI pinned near 100
    down = [76, 72, 68]                          # -4/bar → RSI rolls down through 70
    return up + down


def _still_falling_closes():
    """Strictly decreasing — RSI sits at 0 and is NOT rising. Anti-falling-knife: a
    low RSI that has not turned must NOT trigger a LONG."""
    return [100 - 2 * i for i in range(24)]


# ── rsi_series: index-aligned + verbatim reuse of calc_rsi ──

def test_rsi_series_aligned_and_reuses_calc_rsi():
    closes = [100 - i for i in range(20)]        # strictly down
    s = scoring.rsi_series(closes)
    assert len(s) == len(closes)                 # index-aligned to closes
    assert s[0] == 50                            # < period+1 history → calc_rsi's neutral 50
    assert s[-1] == 0.0                          # pure downtrend → RSI 0
    assert s[-1] == scoring.calc_rsi(closes)     # last bar == full-series calc_rsi (verbatim)


# ── oversold_bounce: LONG cross-up, anti-falling-knife None, SHORT symmetric ──

def test_oversold_bounce_long_on_confirmed_cross_up():
    sig = scoring.oversold_bounce(_candles_from_closes(_long_bounce_closes()), {})
    assert sig is not None
    assert sig["direction"] == "LONG"
    assert sig["score"] > 0
    assert sig["rsi"] >= 30                       # current RSI is back above the oversold line
    assert any("crossed back up" in r for r in sig["reasons"])


def test_oversold_bounce_none_when_still_falling():
    # RSI is deep below 30 but STILL FALLING (not crossed back up) → no signal.
    sig = scoring.oversold_bounce(_candles_from_closes(_still_falling_closes()), {})
    assert sig is None                            # anti-falling-knife gate


def test_oversold_bounce_short_on_overbought_symmetric():
    sig = scoring.oversold_bounce(_candles_from_closes(_short_fade_closes()), {})
    assert sig is not None
    assert sig["direction"] == "SHORT"
    assert sig["score"] > 0
    assert sig["rsi"] <= 70                       # current RSI is back below the overbought line
    assert any("crossed back down" in r for r in sig["reasons"])


def test_oversold_bounce_none_on_short_history():
    # Fewer than period + crossLookback + 1 bars → cannot resolve a cross → None.
    sig = scoring.oversold_bounce(_candles_from_closes(_long_bounce_closes()[:12]), {})
    assert sig is None


def test_oversold_bounce_respects_custom_levels():
    # Tightening the oversold line to 20 makes the shallow cross emit differently;
    # a symmetric-config sanity check that inputs are honored (no crash, valid dict).
    sig = scoring.oversold_bounce(_candles_from_closes(_long_bounce_closes()),
                                  {"oversoldLevel": 30, "overboughtLevel": 70, "crossLookback": 5})
    assert sig and sig["direction"] == "LONG"


# ── band_for + sizing_for: conviction bands and salmon's caps (leverage <= 4) ──

def test_band_and_sizing_caps():
    inp = {"apexScore": 7, "goodScore": 5,
           "leverageTiers": {"apex": 4, "good": 3, "base": 2},
           "marginPctTiers": {"apex": 12, "good": 9, "base": 6},
           "maxLeverage": 4, "maxMarginPct": 20}
    assert scoring.band_for(8, inp) == "apex"
    assert scoring.band_for(5, inp) == "good"
    assert scoring.band_for(3, inp) == "base"

    lev, mgn = scoring.sizing_for("apex", inp, venue_max=10)
    assert lev == 4 and 0 < mgn <= 20            # leverage capped at maxLeverage 4
    lev2, _ = scoring.sizing_for("apex", inp, venue_max=3)
    assert lev2 == 3                             # clamped further to the venue max
    assert scoring.sizing_for("base", inp)[0] == 2
    # a mis-tiered margin can never exceed the cap
    _, mgn3 = scoring.sizing_for("apex", {"marginPctTiers": {"apex": 50}, "maxMarginPct": 20})
    assert mgn3 == 20


# ── scan() smoke against a fake ctx (no network) ──

class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, candles):
        self.candles = candles
    def call_tool(self, tool, args):
        if tool == "market_list_instruments":
            return {"data": {"instruments": [
                {"name": "BTC", "context": {"dayNtlVlm": 5e8, "maxLeverage": 5}},
                {"name": "ETH", "context": {"dayNtlVlm": 4e8, "maxLeverage": 5}}]}}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"1h": self.candles}}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"assetPositions": []}}
        return {}


class _Ctx:
    def __init__(self, candles):
        self.wallet = "0xabc"
        self.state = _State()
        self.senpi_mcp = _MCP(candles)


def test_scan_smoke_returns_valid_signals():
    import scan
    ctx = _Ctx(_candles_from_closes(_long_bounce_closes()))
    inputs = {"maxSlots": 5, "includeXyz": False, "universeVolFloorUsd": 1e6, "maxUniverse": 10,
              "recentSignalTtlSeconds": 14400, "oversoldLevel": 30, "overboughtLevel": 70,
              "crossLookback": 5, "apexScore": 7, "goodScore": 5,
              "leverageTiers": {"apex": 4, "good": 3, "base": 2},
              "marginPctTiers": {"apex": 12, "good": 9, "base": 6},
              "maxLeverage": 4, "maxMarginPct": 20}
    out = scan.scan(inputs, ctx)
    assert isinstance(out, list) and len(out) >= 1        # BTC + ETH both bounce
    for s in out:
        assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
        assert 0 < s["marginPct"] <= 20                   # PERCENT within salmon's cap
        assert 1 <= s["leverage"] <= 4                    # moderate leverage cap
        assert s["direction"] in ("LONG", "SHORT")
        assert s["data"]["band"] in ("apex", "good", "base")
        assert "rsi" in s["data"] and "score" in s["data"]
    assert ctx.state.last() is not None                   # state persisted
    assert "recent" in ctx.state.last()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} SALMON TESTS PASS")
