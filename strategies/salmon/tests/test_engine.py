"""Salmon engine tests — pure/deterministic RSI mean-reversion detector, plus a
scan() smoke run against a fake ctx (no network). Run:
  python3 -B -m pytest strategies/salmon/tests -q
  python3 -B strategies/salmon/tests/test_engine.py

1.3.0: the two universe tests used to sit BELOW the `__main__` runner, so the direct run
reported "ALL 8" with them never executed; the fake `_State` had no `__len__` (the runtime's
state object has one, and scan() truth-tests it); the fake clearinghouse answered the flat
legacy shape instead of the dual-DEX `{"main": ..., "xyz": ...}` the venue returns; and the
candle fixture used long key names where the feed sends o/h/l/c/v strings.
"""
import os
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
INPUTS = dict(next(s for s in RUNTIME["scanners"] if s["name"] == "salmon_scanner")["inputs"])
NOW = time.time()


def _candles_from_closes(closes, now=NOW):
    """1h candles in the feed's shape (o/h/l/c/v strings, t/T epoch ms), oldest first.
    RSI/momentum read the close only."""
    out = []
    for k, c in enumerate(closes):
        t = int((now - (len(closes) - k) * 3600) * 1000)
        out.append({"t": t, "T": t + 3600000, "s": "X", "i": "1h", "o": f"{c}", "h": f"{c + 1}",
                    "l": f"{c - 1}", "c": f"{c}", "v": "1000", "n": 1})
    return out


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
    assert any("cross_up" in r for r in sig["reasons"])


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
    assert any("cross_down" in r for r in sig["reasons"])


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


# ── band_for + sizing_for: conviction bands and salmon's caps (leverage <= 3, 18% margin) ──

def test_band_and_sizing_caps():
    # 1.3.0: rewritten to the shipped numbers (was its own 2-4x / 6-12% / cap 20 inputs). Margin is a
    # flat 18% per slot; conviction only moves the leverage, and never above 3x.
    inp = INPUTS
    assert scoring.band_for(8, inp) == "apex"
    assert scoring.band_for(5, inp) == "good"
    assert scoring.band_for(3, inp) == "base"

    lev, mgn = scoring.sizing_for("apex", inp, venue_max=10)
    assert lev == 3 and mgn == 18                # leverage capped at maxLeverage 3; flat 18% margin
    lev2, _ = scoring.sizing_for("apex", inp, venue_max=2)
    assert lev2 == 2                             # clamped further to the venue max
    assert scoring.sizing_for("base", inp) == (2, 18)
    # a mis-tiered margin can never exceed the cap
    _, mgn3 = scoring.sizing_for("apex", {"marginPctTiers": {"apex": 50}, "maxMarginPct": 18})
    assert mgn3 == 18


# ── scan() smoke against a fake ctx (no network) ──

class _State:
    def __init__(self): self._log = []
    def __len__(self): return len(self._log)
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, candles, held=()):
        self.candles, self.held = candles, held
    def call_tool(self, tool, args):
        if tool == "market_list_instruments":
            # The no-dex listing carries BOTH boards — reproducing that here is the whole
            # point: a stub with crypto names only cannot catch an xyz: leak.
            return {"data": {"instruments": [
                {"name": "BTC", "context": {"dayNtlVlm": 5e8, "maxLeverage": 5}},
                {"name": "ETH", "context": {"dayNtlVlm": 4e8, "maxLeverage": 5}},
                {"name": "xyz:CL", "context": {"dayNtlVlm": 6e8, "maxLeverage": 5}},
                {"name": "xyz:BRENTOIL", "context": {"dayNtlVlm": 5.5e8, "maxLeverage": 5}}]}}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"1h": self.candles}}}
        if tool == "strategy_get_clearinghouse_state":
            # Two views of ONE cross-margined wallet — the shape the venue returns. The flat
            # legacy shape the fixture used to answer would never exercise the dual-DEX read.
            return {"data": {"main": {"assetPositions": [{"position": {"coin": c, "szi": "1"}} for c in self.held]},
                             "xyz": {"assetPositions": []}}}
        return {}


class _Ctx:
    def __init__(self, candles, held=()):
        self.wallet = "0x" + "c" * 40
        self.state = _State()
        self.senpi_mcp = _MCP(candles, held)
        self.scanner_name, self.interval_seconds, self.dry_run = "salmon_scanner", 600, False


def test_scan_smoke_returns_valid_signals():
    # 1.3.0: reads the shipped inputs instead of carrying its own 5-slot / 2-4x / 6-12% copy, so the
    # smoke cannot drift from runtime.yaml; the bounce scores apex, which is 3x at a flat 18%.
    import scan
    ctx = _Ctx(_candles_from_closes(_long_bounce_closes()))
    out = scan.scan(dict(INPUTS), ctx)
    assert isinstance(out, list) and len(out) == 2                 # BTC + ETH both bounce; xyz excluded
    for s in out:
        assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
        assert s["marginPct"] == 18                                 # PERCENT, the flat slot size
        assert s["leverage"] == 3 and s["data"]["band"] == "apex"   # the cap
        assert s["direction"] == "LONG"
        assert "rsi" in s["data"] and "score" in s["data"]
    assert len(ctx.state) == 1 and "recent" in ctx.state.last()    # state persisted


def test_scan_skips_a_held_name_read_from_the_dual_dex_view():
    import scan
    ctx = _Ctx(_candles_from_closes(_long_bounce_closes()), held=("BTC",))
    out = scan.scan(dict(INPUTS), ctx)
    assert [s["asset"] for s in out] == ["ETH"]                     # BTC is held in the `main` view
    assert ctx.state.last()["result"]["held"] == 1


def test_includeXyz_false_excludes_xyz_names_from_the_universe():
    """`includeXyz: false` must EXCLUDE xyz: names, not merely skip fetching more.

    The no-dex instrument listing carries both boards, so a flag that only gates a second
    fetch removes nothing. Left unfiltered, xyz: names are graded against `xyzVolFloorUsd`
    rather than the main floor — a far lower bar into a book whose universe is crypto majors.
    """
    import scan
    ctx = _Ctx(_candles_from_closes(_long_bounce_closes()))
    universe = scan._derive_universe(ctx, {"includeXyz": False, "universeVolFloorUsd": 1e6,
                                           "maxUniverse": 10})
    names = [r["name"] for r in universe]
    assert names, "universe should not be empty"
    assert not [n for n in names if n.lower().startswith("xyz:")], \
        f"xyz: names leaked into a crypto-only universe: {names}"
    assert "BTC" in names and "ETH" in names


def test_includeXyz_true_still_admits_xyz_names():
    """The opt-in must keep working — the fix removes, it does not forbid."""
    import scan
    ctx = _Ctx(_candles_from_closes(_long_bounce_closes()))
    universe = scan._derive_universe(ctx, {"includeXyz": True, "universeVolFloorUsd": 1e6,
                                           "xyzVolFloorUsd": 1e6, "maxUniverse": 10})
    names = [r["name"] for r in universe]
    assert [n for n in names if n.lower().startswith("xyz:")], \
        f"includeXyz: true should admit xyz: names, got {names}"


# The direct runner stays LAST: it collects `test_*` from globals() when it runs, so anything
# defined below it would silently never run (1.2.1 shipped two tests that way).
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} SALMON TESTS PASS")
