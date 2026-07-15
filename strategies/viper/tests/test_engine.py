"""Viper structure-engine tests. Pure/deterministic (swings, BOS/CHoCH, liquidity
sweep, FVG, scoring, sizing) + a scan() smoke run against a fake ctx (no network).
Run: python3 -m pytest strategies/viper/tests -q   OR   python3 strategies/viper/tests/test_engine.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402


def _c(h, l, c, v=1000):
    """One candle dict (open irrelevant to the structure math)."""
    return {"open": c, "high": h, "low": l, "close": c, "volume": v}


def _up(n, step=2, start=100.0):
    """Strictly monotone-up series → NO interior pivots (clean 'nothing happening')."""
    out, p = [], start
    for _ in range(n):
        out.append(_c(p + step + 1, p - 1, p + step))
        p += step
    return out


def _flat(n):
    return [_c(100, 99, 99.5) for _ in range(n)]


# Clean bullish setup: a confirmed swing high (idx5=110) + swing low (idx7=101);
# the last bar closes 114 (> swing high → BOS LONG), wicks to 100 (< swing low 101,
# closes back above → bullish liquidity sweep), and a bullish FVG sits at bars 8-9-10
# (high[8]=107 < low[10]=108). Hand-verified.
BOS = [
    _c(101, 99, 100),   # 0
    _c(102, 98, 99),    # 1
    _c(100, 95, 96),    # 2  swing low = 95
    _c(103, 97, 102),   # 3
    _c(106, 100, 105),  # 4
    _c(110, 104, 108),  # 5  swing high = 110
    _c(108, 102, 104),  # 6
    _c(106, 101, 103),  # 7  swing low = 101 (most recent)
    _c(107, 103, 106),  # 8
    _c(109, 105, 108),  # 9
    _c(112, 108, 111),  # 10 (low 108 opens a bullish FVG vs high[8]=107)
    _c(116, 100, 114),  # 11 last: close>swing-high AND wick<swing-low, closes back above
]

# Bullish bias (higher-high 108→113, higher-low 96→100) then the last close (96)
# breaks BELOW the most-recent swing low (100) → SHORT CHoCH (reversal). Hand-verified.
CHOCH = [
    _c(101, 99, 100),   # 0
    _c(103, 100, 102),  # 1
    _c(100, 96, 97),    # 2  swing low = 96
    _c(105, 99, 104),   # 3
    _c(108, 102, 107),  # 4  swing high = 108
    _c(106, 101, 103),  # 5
    _c(104, 100, 102),  # 6  swing low = 100 (higher low)
    _c(110, 104, 109),  # 7
    _c(113, 107, 112),  # 8  swing high = 113 (higher high, most recent)
    _c(111, 105, 107),  # 9
    _c(108, 103, 104),  # 10
    _c(106, 100, 102),  # 11
    _c(104, 98, 100),   # 12
    _c(101, 95, 96),    # 13 last: close 96 < swing low 100 → break down
]


def test_swings_finds_confirmed_pivots():
    piv = scoring.swings(BOS, left=2, right=2)
    highs = [p for p in piv if p["kind"] == "high"]
    lows = [p for p in piv if p["kind"] == "low"]
    assert any(p["idx"] == 5 and p["price"] == 110 for p in highs)      # swing high
    assert any(p["idx"] == 2 and p["price"] == 95 for p in lows)        # swing low
    assert any(p["idx"] == 7 and p["price"] == 101 for p in lows)       # most-recent low
    # the last `right` bars can never be confirmed pivots
    assert all(p["idx"] <= len(BOS) - 1 - 2 for p in piv)
    assert scoring.swings(_up(3), 2, 2) == []                           # too few bars → none
    assert scoring.swings(_up(20)) == []                                # monotone → no pivots


def test_structure_bos_and_choch():
    assert scoring.structure(BOS) == ("LONG", "BOS")
    assert scoring.structure(CHOCH) == ("SHORT", "CHoCH")
    assert scoring.structure(_flat(10)) == (None, "NONE")              # no pivots → no read
    assert scoring.structure([]) == (None, "NONE")                     # empty → safe


def test_liquidity_sweep_true_and_false():
    # bearish sweep: wick above a confirmed swing high (108), close back below
    swept_series = [_c(100, 98, 99), _c(101, 99, 100), _c(108, 103, 106),
                    _c(104, 100, 102), _c(103, 99, 101), _c(110, 104, 105)]
    assert scoring.liquidity_sweep(swept_series) == (True, "SHORT")
    # BOS series' last bar sweeps the swing low (100 < 101) and closes back above
    assert scoring.liquidity_sweep(BOS) == (True, "LONG")
    # clean monotone series: no swing to sweep
    assert scoring.liquidity_sweep(_up(12)) == (False, None)
    assert scoring.liquidity_sweep([]) == (False, None)


def test_fvg_true_and_false():
    bull = [_c(101, 99, 100), _c(105, 100, 103), _c(110, 106, 108)]     # high[0]=101 < low[2]=106
    bear = [_c(110, 106, 108), _c(104, 100, 102), _c(99, 95, 97)]       # low[0]=106 > high[2]=99
    none = [_c(105, 100, 102), _c(106, 101, 103), _c(107, 102, 104)]    # overlapping — no gap
    assert scoring.fvg(bull) == (True, "LONG")
    assert scoring.fvg(bear) == (True, "SHORT")
    assert scoring.fvg(none) == (False, None)
    assert scoring.fvg([_c(1, 1, 1)]) == (False, None)                  # < 3 bars → safe


def test_score_structure_directional_and_none():
    # clean BOS + aligned sweep + aligned FVG + agreeing 4h → LONG, high score
    th = scoring.score_structure(BOS, BOS, {"swingLeft": 2, "swingRight": 2})
    assert th is not None
    assert th["direction"] == "LONG"
    assert th["structure"] == "BOS"
    assert th["score"] >= 5                                             # 3 BOS +2 sweep +1 fvg +2 4h = 8
    joined = " ".join(th["reasons"])
    assert "sweep_aligned" in joined and "fvg_aligned" in joined and "4h_agrees" in joined
    # CHoCH short with agreeing 4h resolves a directional SHORT
    ths = scoring.score_structure(CHOCH, CHOCH, {})
    assert ths is not None and ths["direction"] == "SHORT" and ths["structure"] == "CHoCH"
    # no structure → None (not a zero-score signal)
    assert scoring.score_structure(_flat(10), _flat(10), {}) is None
    assert scoring.score_structure([], [], {}) is None


def test_score_structure_4h_opposes_penalizes():
    aligned = scoring.score_structure(BOS, BOS, {})["score"]
    opposed = scoring.score_structure(BOS, CHOCH, {})["score"]          # 4h is SHORT vs 1h LONG
    assert opposed == aligned - 4                                       # +2 agree flips to −2


def test_band_and_sizing_caps():
    inp = {"apexScore": 9, "goodScore": 7, "maxLeverage": 5, "maxMarginPct": 25,
           "leverageTiers": {"apex": 5, "good": 4, "base": 3},
           "marginPctTiers": {"apex": 30, "good": 10, "base": 7}}      # apex margin over cap
    assert scoring.band_for(9, inp) == "apex"
    assert scoring.band_for(7, inp) == "good"
    assert scoring.band_for(3, inp) == "base"
    lev, mgn = scoring.sizing_for("apex", inp, venue_max=3)
    assert lev == 3                                                    # clamped to venue max (< fleet 5)
    assert 0 < mgn <= 25                                               # margin capped to maxMarginPct
    lev2, _ = scoring.sizing_for("base", inp, venue_max=None)
    assert 1 <= lev2 <= 5


# ── scan() smoke against a fake ctx (no network) ──

class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)


class _MCP:
    def call_tool(self, tool, args):
        if tool == "market_list_instruments":
            return {"data": {"instruments": [
                {"name": "BTC", "context": {"dayNtlVlm": 5e8, "maxLeverage": 5}},
                {"name": "ETH", "context": {"dayNtlVlm": 4e8, "maxLeverage": 5}}]}}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"1h": BOS, "4h": BOS}}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"assetPositions": []}}
        return {}


class _Ctx:
    def __init__(self):
        self.wallet = "0xabc"
        self.state = _State()
        self.senpi_mcp = _MCP()


def test_scan_smoke_returns_valid_signals():
    import scan
    ctx = _Ctx()
    inputs = {"maxSlots": 6, "minScore": 0, "includeXyz": False,
              "universeVolFloorUsd": 1e6, "maxUniverse": 10}
    out = scan.scan(inputs, ctx)
    assert isinstance(out, list)
    assert len(out) >= 1                                               # BOS series clears minScore 0
    for s in out:
        assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
        assert 0 < s["marginPct"] <= 25
        assert 1 <= s["leverage"] <= 5
        assert s["direction"] in ("LONG", "SHORT")
        assert isinstance(s["data"].get("reasons"), list)
    assert ctx.state.last() is not None                               # state persisted
    assert "recent" in ctx.state.last()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok  %s" % fn.__name__)
    print("\nALL %d VIPER TESTS PASS" % len(fns))
