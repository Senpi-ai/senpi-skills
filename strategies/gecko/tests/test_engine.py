"""Gecko engine + scanner tests. Pure/deterministic engine checks, plus a scan()
smoke run against a fake ctx (no network). Run:
    python3 -m pytest strategies/gecko/tests -q
or plain:
    python3 strategies/gecko/tests/test_engine.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402


def _synth_candles(n, step, start=100.0):
    """Monotone series (step>0 → higher-lows/bullish, step<0 → lower-highs/bearish)."""
    out, p = [], start
    for _ in range(n):
        out.append({"open": p, "high": p + abs(step), "low": p - abs(step) / 2,
                    "close": p + step, "volume": 1000})
        p += step
    return out


# ── pure engine ──

def test_build_thesis_direction_and_insufficient():
    # clean uptrend → LONG with positive confluence
    up1h, up4h = _synth_candles(12, 1.0), _synth_candles(6, 2.0)
    long_th = scoring.build_thesis("BTC", up1h[-3:], up1h, up4h, -0.01, (None, 0), {})
    assert long_th and long_th["direction"] == "LONG" and long_th["score"] > 0
    # clean downtrend → SHORT with positive confluence
    dn1h, dn4h = _synth_candles(12, -1.0), _synth_candles(6, -2.0)
    short_th = scoring.build_thesis("BTC", dn1h[-3:], dn1h, dn4h, 0.02, (None, 0), {})
    assert short_th and short_th["direction"] == "SHORT" and short_th["score"] > 0
    # insufficient candle history → None (len(c1h) < 8 or len(c4h) < 4)
    assert scoring.build_thesis("X", [], up1h[:4], up4h[:2], 0, (None, 0), {}) is None


def test_band_and_sizing_caps():
    inp = {"apexScore": 12, "goodScore": 10,
           "leverageTiers": {"apex": 5, "good": 4, "base": 3},
           "marginPctTiers": {"apex": 20, "good": 15, "base": 10},
           "maxLeverage": 5, "maxMarginPct": 25}
    assert scoring.band_for(13, inp) == "apex"
    assert scoring.band_for(10, inp) == "good"
    assert scoring.band_for(6, inp) == "base"
    # apex sizes to the fleet caps
    lev, mgn = scoring.sizing_for("apex", inp)
    assert lev == 5 and mgn == 20
    # a venue leverage cap clamps below the fleet max
    lev2, _ = scoring.sizing_for("apex", inp, venue_max=3)
    assert lev2 == 3
    # marginPct hard-capped at maxMarginPct even if a tier over-asks
    _, mgn3 = scoring.sizing_for("apex", {**inp, "marginPctTiers": {"apex": 40}})
    assert 0 < mgn3 <= 25
    # leverage floored at 1 even for a degenerate tier
    lev4, _ = scoring.sizing_for("base", {**inp, "leverageTiers": {"base": 0}})
    assert lev4 >= 1


# ── scan() against a fake ctx (no network) ──

class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, candles, held=None, funding=-0.01):
        self.candles = candles
        self.held = held                # coin string currently held (bare or prefixed), or None
        self.funding = funding
        self.calls = []
    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"15m": self.candles[-3:], "1h": self.candles,
                                         "4h": self.candles}, "funding": self.funding}}
        if tool == "strategy_get_clearinghouse_state":
            aps = [{"position": {"coin": self.held, "szi": 1.0}}] if self.held else []
            return {"data": {"assetPositions": aps}}
        return {}


class _Ctx:
    def __init__(self, candles, held=None, funding=-0.01):
        self.wallet = "0xabc"
        self.state = _State()
        self.senpi_mcp = _MCP(candles, held, funding)


def test_scan_honors_asset_and_prefix():
    import scan
    up = _synth_candles(14, 1.0)
    ctx = _Ctx(up)
    out = scan.scan({"asset": "xyz:TSLA", "minScore": 0}, ctx)
    assert len(out) == 1
    assert out[0]["asset"] == "xyz:TSLA"                  # emits THAT asset, prefix preserved
    assert out[0]["direction"] == "LONG"
    # the asset_data read derived the xyz dex from the prefix
    md_calls = [a for (t, a) in ctx.senpi_mcp.calls if t == "market_get_asset_data"]
    assert md_calls and md_calls[0]["dex"] == "xyz" and md_calls[0]["asset"] == "xyz:TSLA"


def test_scan_emits_nothing_when_held():
    import scan
    up = _synth_candles(14, 1.0)
    # already holding the named asset (bare coin in clearinghouse) → no signal
    assert scan.scan({"asset": "xyz:TSLA", "minScore": 0}, _Ctx(up, held="TSLA")) == []
    # also holds when the clearinghouse reports the prefixed coin
    assert scan.scan({"asset": "xyz:TSLA", "minScore": 0}, _Ctx(up, held="xyz:TSLA")) == []
    # and for a main-dex name held bare
    assert scan.scan({"asset": "BTC", "minScore": 0}, _Ctx(up, held="BTC")) == []


def test_scan_smoke_returns_single_valid_signal():
    import scan
    up = _synth_candles(14, 1.0)
    ctx = _Ctx(up)                                       # empty clearinghouse, in-memory state
    out = scan.scan({"asset": "BTC", "minScore": 0}, ctx)
    assert len(out) == 1
    s = out[0]
    assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
    assert s["asset"] == "BTC"
    assert 0 < s["marginPct"] <= 25 and 1 <= s["leverage"] <= 5
    assert s["direction"] in ("LONG", "SHORT")
    assert ctx.state.last() is not None                  # state persisted
    assert ctx.state.last()["result"]["emitted"] is True


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} GECKO TESTS PASS")
