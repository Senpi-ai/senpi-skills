"""Puffer engine tests. Pure math + a scan() smoke run against a fake ctx (no network).
Run: python3 strategies/puffer/tests/test_engine.py"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402


def _wide(n=60, amp=4.0, vol=1000.0):
    return [{"h": 106, "l": 94, "c": 100 + amp * math.sin(i / 2), "v": vol} for i in range(n)]


def _calm(n=30, amp=0.08, vol=1000.0):
    return [{"h": 100.3, "l": 99.7, "c": 100 + amp * math.sin(i), "v": vol} for i in range(n)]


def _coiled():
    return _wide() + _calm()


# ── the squeeze is measured against the asset's OWN history ──

def test_squeeze_is_relative_not_absolute():
    """An absolute band width is meaningless across a universe: 0.3% is dead calm for an
    alt and a violent day for an index. Same shape at two price scales must read alike."""
    coiled = _coiled()
    r_small = scoring.squeeze_ratio(coiled, {})
    scaled = [{"h": c["h"] * 1000, "l": c["l"] * 1000, "c": c["c"] * 1000, "v": c["v"]}
              for c in coiled]
    r_big = scoring.squeeze_ratio(scaled, {})
    assert r_small is not None and r_big is not None
    assert abs(r_small - r_big) < 1e-6, (r_small, r_big)
    assert r_small < 0.72, "a genuine contraction must register as coiled"
    # a market that never contracted is not a setup
    assert scoring.squeeze_ratio(_wide(120), {}) > 0.72


def test_lookback_must_span_a_regime():
    """With too short a lookback the median is computed INSIDE the calm stretch, so the
    contraction becomes invisible — the bug found while building this."""
    coiled = _coiled()
    assert scoring.squeeze_ratio(coiled, {"squeezeLookback": 60}) < 0.72
    assert scoring.squeeze_ratio(coiled, {"squeezeLookback": 12}) > 0.72   # blind to the regime


# ── trade the RELEASE, never the coil ──

def test_no_trade_while_still_coiled():
    assert scoring.build_thesis("BTC", _coiled(), {}) is None


def test_release_fires_in_the_direction_of_the_break():
    up = _coiled() + [{"h": 102.5, "l": 100.2, "c": 102.2, "v": 4000}]
    dn = _coiled() + [{"h": 99.8, "l": 97.5, "c": 97.8, "v": 4000}]
    tu, td = scoring.build_thesis("BTC", up, {}), scoring.build_thesis("BTC", dn, {})
    assert tu and tu["direction"] == "LONG" and tu["squeeze_ratio"] < 0.72
    assert td and td["direction"] == "SHORT"


def test_release_without_a_prior_coil_is_not_our_setup():
    """Breaking out of an already-wide market is somebody else's strategy."""
    never = _wide(120) + [{"h": 112, "l": 108, "c": 111, "v": 4000}]
    assert scoring.build_thesis("BTC", never, {}) is None


def test_release_needs_participation_and_must_clear_fees():
    quiet_vol = _coiled() + [{"h": 102.5, "l": 100.2, "c": 102.2, "v": 200}]
    assert scoring.build_thesis("BTC", quiet_vol, {"minVolSurge": 1.4}) is None
    dead = [{"h": 100.001, "l": 99.999, "c": 100, "v": 1000} for _ in range(95)] + \
           [{"h": 100.002, "l": 100.0, "c": 100.0015, "v": 4000}]
    assert scoring.clears_fees(dead, {})[0] is False
    assert scoring.build_thesis("BTC", dead, {}) is None


def test_sizing_is_a_percent_and_clamped():
    inp = {"leverageTiers": {"apex": 5}, "marginPctTiers": {"apex": 12},
           "maxLeverage": 5, "maxMarginPct": 22}
    lev, mgn = scoring.sizing_for("apex", inp)
    assert (lev, mgn) == (5, 12.0) and 0 < mgn <= 100
    assert scoring.sizing_for("apex", inp, venue_max=3)[0] == 3
    assert scoring.sizing_for("apex", {"leverageTiers": {"apex": 99},
                                       "marginPctTiers": {"apex": 999},
                                       "maxLeverage": 5, "maxMarginPct": 22}) == (5, 22.0)


# ── scan() smoke ──

class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, released=True): self.released = released
    def call_tool(self, tool, args):
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"assetPositions": []}}
        if tool == "market_list_instruments":
            return {"instruments": [
                {"name": "BTC", "context": {"dayNtlVlm": 9e8, "max_leverage": 20}},
                {"name": "DUST", "context": {"dayNtlVlm": 1e5, "max_leverage": 5}},
            ]}
        if tool == "market_get_asset_data":
            c = _coiled() + ([{"h": 102.5, "l": 100.2, "c": 102.2, "v": 4000}] if self.released else [])
            return {"data": {"candles": {"1h": c}}}
        return {}


class _Ctx:
    def __init__(self, mcp): self.senpi_mcp, self.wallet, self.state = mcp, "0xpuffer", _State()


_INPUTS = {"volFloorUsd": 2.5e7, "maxUniverse": 30, "maxSlots": 5,
           "recentSignalTtlSeconds": 21600, "minScore": 0,
           "leverageTiers": {"apex": 5, "good": 4, "base": 3},
           "marginPctTiers": {"apex": 12, "good": 9, "base": 6},
           "maxLeverage": 5, "maxMarginPct": 22}


def test_scan_emits_on_release_and_skips_illiquid():
    ctx = _Ctx(_MCP(released=True))
    out = scan.scan(dict(_INPUTS), ctx)
    assert out, "a coiled market that just released should produce an entry"
    assert "DUST" not in {s["asset"] for s in out}
    for s in out:
        assert 0 < s["marginPct"] <= 22 and 1 <= s["leverage"] <= 5
        assert s["data"]["squeezeRatio"] < 0.72


def test_scan_is_silent_while_everything_is_still_coiled():
    ctx = _Ctx(_MCP(released=False))
    assert scan.scan(dict(_INPUTS), ctx) == []


def test_scan_degrades_when_clearinghouse_unreadable():
    class _Broken:
        def call_tool(self, tool, args):
            if tool == "strategy_get_clearinghouse_state":
                raise RuntimeError("boom")
            return {}
    assert scan.scan(dict(_INPUTS), _Ctx(_Broken())) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} PUFFER TESTS PASS")
