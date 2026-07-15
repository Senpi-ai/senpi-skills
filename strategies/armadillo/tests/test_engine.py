"""Armadillo engine tests. Pure/deterministic thesis + LOW-cap sizing checks, plus
a scan() smoke run against a fake ctx (no network). Run:
  python3 -m pytest strategies/armadillo/tests -q
or plain `python3 strategies/armadillo/tests/test_engine.py`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402


def _synth_candles(n, step, start=100.0):
    """Monotone series (step>0 → higher-lows/bullish, step<0 → bearish)."""
    out, p = [], start
    for _ in range(n):
        out.append({"open": p, "high": p + abs(step), "low": p - abs(step) / 2,
                    "close": p + step, "volume": 1000})
        p += step
    return out


# ── the thesis scorer (bison-ported): clean trend → thesis, thin history → None ──

def test_build_thesis_clean_trend_and_insufficient():
    up1h, up4h = _synth_candles(12, 1.0), _synth_candles(6, 2.0)
    th = scoring.build_thesis("BTC", up1h[-3:], up1h, up4h, funding=-0.01, sm=(None, 0), inputs={})
    assert th and th["direction"] == "LONG" and th["score"] > 0
    # a bearish series resolves SHORT (direction waterfall, opposite sign)
    dn1h, dn4h = _synth_candles(12, -1.0), _synth_candles(6, -2.0)
    ths = scoring.build_thesis("ETH", dn1h[-3:], dn1h, dn4h, funding=0.01, sm=(None, 0), inputs={})
    assert ths and ths["direction"] == "SHORT"
    # insufficient candles → None (len(c1h) < 8 or len(c4h) < 4)
    assert scoring.build_thesis("X", [], up1h[:4], up4h[:2], 0, (None, 0), {}) is None
    assert scoring.build_thesis("Y", up1h, up1h, up4h[:3], 0, (None, 0), {}) is None


# ── band + LOW-cap sizing: leverage ≤2, marginPct ≤10 even for an APEX score ──

def test_band_and_sizing_enforce_low_caps():
    inp = {"apexScore": 13, "goodScore": 12,
           "leverageTiers": {"apex": 2, "good": 2, "base": 1},
           "marginPctTiers": {"apex": 8, "good": 6, "base": 4},
           "maxLeverage": 2, "maxMarginPct": 10}
    assert scoring.band_for(15, inp) == "apex"      # only the strongest earn apex
    assert scoring.band_for(12, inp) == "good"
    assert scoring.band_for(9, inp) == "base"       # a mediocre score is only 'base'

    # apex band still tops out at the LOW caps
    lev, mgn = scoring.sizing_for("apex", inp)
    assert lev == 2 and 0 < mgn <= 10
    lev_b, mgn_b = scoring.sizing_for("base", inp)
    assert lev_b == 1 and 0 < mgn_b <= 10

    # a config that TRIED to size big is still hard-clamped to the ceilings
    hot = {"leverageTiers": {"apex": 50}, "marginPctTiers": {"apex": 99},
           "maxLeverage": 2, "maxMarginPct": 10}
    lev2, mgn2 = scoring.sizing_for("apex", hot)
    assert lev2 == 2 and mgn2 == 10                 # clamped, never overshoots

    # a venue max BELOW the fleet cap wins (defensive against thin-book leverage)
    lev3, _ = scoring.sizing_for("apex", inp, venue_max=1)
    assert lev3 == 1


# ── scan() smoke against a fake ctx (no network) ──

class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, up):
        self.up = up
    def call_tool(self, tool, args):
        if tool == "market_list_instruments":
            return {"data": {"instruments": [
                {"name": "BTC", "context": {"dayNtlVlm": 5e8, "maxLeverage": 5}},
                {"name": "ETH", "context": {"dayNtlVlm": 4e8, "maxLeverage": 5}}]}}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"15m": self.up[-3:], "1h": self.up, "4h": self.up},
                             "funding": -0.01}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"assetPositions": []}}
        return {}


class _Ctx:
    def __init__(self, up):
        self.wallet = "0xabc"
        self.state = _State()
        self.senpi_mcp = _MCP(up)


def test_scan_high_minscore_gate_rejects_mediocre():
    """The clean synthetic uptrend scores ~8 — below the HIGH minScore floor (11),
    so the gate emits NOTHING. (Proves the high bar; state still persists.)"""
    import scan
    ctx = _Ctx(_synth_candles(14, 1.0))
    inputs = {"maxSlots": 4, "minScore": 11, "includeXyz": False,
              "universeVolFloorUsd": 1e6, "maxUniverse": 10}
    out = scan.scan(inputs, ctx)
    assert out == []                                # mediocre score rejected by high gate
    assert ctx.state.last() is not None             # state still persisted


def test_scan_smoke_emits_low_capped_signal():
    """With the gate dropped, the same setup emits — and every emitted signal
    obeys the LOW caps: leverage∈[1,2], marginPct∈(0,10], valid direction."""
    import scan
    ctx = _Ctx(_synth_candles(14, 1.0))
    inputs = {"maxSlots": 4, "minScore": 0, "includeXyz": False,
              "universeVolFloorUsd": 1e6, "maxUniverse": 10}
    out = scan.scan(inputs, ctx)
    assert isinstance(out, list) and len(out) >= 1   # at least one open on a clean trend
    for s in out:
        assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
        assert 1 <= s["leverage"] <= 2               # LOW leverage cap
        assert 0 < s["marginPct"] <= 10              # LOW margin cap
        assert s["direction"] in ("LONG", "SHORT")
        assert s["data"]["band"] in ("apex", "good", "base")
    assert ctx.state.last() is not None              # state persisted


def test_scan_fails_closed_on_bad_clearinghouse():
    """A capital-preservation book must fail CLOSED: an unreadable clearinghouse
    yields NO opens (never trades on a bad read)."""
    import scan

    class _BadMCP(_MCP):
        def call_tool(self, tool, args):
            if tool == "strategy_get_clearinghouse_state":
                return {"data": "garbage"}           # not a dict → _held returns None
            return super().call_tool(tool, args)

    ctx = _Ctx(_synth_candles(14, 1.0))
    ctx.senpi_mcp = _BadMCP(_synth_candles(14, 1.0))
    assert scan.scan({"maxSlots": 4, "minScore": 0}, ctx) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} ARMADILLO TESTS PASS")
