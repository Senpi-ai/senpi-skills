"""Weaver engine tests. Pure math + a scan() smoke run against a fake ctx (no network).
Run: python3 strategies/weaver/tests/test_engine.py"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402


def _range(n=60, mid=100.0, amp=2.0, half=2.0, vol=1000.0):
    return [{"h": mid + half, "l": mid - half, "c": mid + amp * math.sin(i * 2 * math.pi / 12),
             "v": vol} for i in range(n)]


def _trend(n=60, vol=1000.0):
    return [{"h": 100 + i * 0.5, "l": 98 + i * 0.5, "c": 99 + i * 0.5, "v": vol} for i in range(n)]


# ── THE TREND VETO — the gate that keeps a harvester solvent ──

def test_efficiency_ratio_separates_range_from_trend():
    assert scoring.efficiency_ratio(_range(), 48) < 0.1     # back and forth, ends where it began
    assert scoring.efficiency_ratio(_trend(), 48) > 0.9     # a straight line
    assert scoring.efficiency_ratio([], 48) == 1.0          # no data -> assume the worst, veto


def test_a_trending_market_is_never_gridded():
    """Averaging into a trend is the single failure mode that kills range traders."""
    at_low = _trend() + [{"h": 130, "l": 128, "c": 128.2, "v": 1000}]
    assert scoring.build_thesis("BTC", at_low, {}) is None
    # and it is a HARD gate, not a score input — loosening every other knob cannot rescue it
    assert scoring.build_thesis("BTC", at_low, {"minRangeWidthPct": 0, "edgeFraction": 0.5}) is None


def test_the_veto_is_phase_independent():
    """A |last - first| drift measure made the SAME band read differently at its floor and
    its ceiling depending on where the window happened to start. ER does not."""
    rng = _range()
    floor = rng + [{"h": 99, "l": 97.9, "c": 98.1, "v": 1000}]
    ceil = rng + [{"h": 102.1, "l": 101, "c": 101.9, "v": 1000}]
    lo_th, hi_th = scoring.build_thesis("BTC", floor, {}), scoring.build_thesis("BTC", ceil, {})
    assert lo_th and lo_th["direction"] == "LONG", lo_th
    assert hi_th and hi_th["direction"] == "SHORT", hi_th
    assert abs(lo_th["efficiency_ratio"] - hi_th["efficiency_ratio"]) < 0.25


# ── only the edges are tradeable ──

def test_mid_range_is_not_a_trade():
    assert scoring.build_thesis("BTC", _range() + [{"h": 101, "l": 99, "c": 100, "v": 1000}], {}) is None


def test_edge_fraction_controls_how_close_to_the_band_we_trade():
    at_edge = _range() + [{"h": 101.5, "l": 99.5, "c": 101.2, "v": 1000}]
    assert scoring.build_thesis("BTC", at_edge, {"edgeFraction": 0.05}) is None   # not close enough
    assert scoring.build_thesis("BTC", at_edge, {"edgeFraction": 0.35}) is not None


def test_too_narrow_a_band_has_nothing_to_harvest():
    tight = _range(amp=0.02, half=0.05)
    assert scoring.build_thesis("BTC", tight + [{"h": 100.05, "l": 99.95, "c": 99.96, "v": 1000}],
                                {"minRangeWidthPct": 1.5}) is None


def test_sizing_is_a_percent_and_clamped():
    inp = {"leverageTiers": {"apex": 3}, "marginPctTiers": {"apex": 9},
           "maxLeverage": 3, "maxMarginPct": 18}
    assert scoring.sizing_for("apex", inp) == (3, 9.0)
    assert scoring.sizing_for("apex", inp, venue_max=2)[0] == 2
    assert scoring.sizing_for("apex", {"leverageTiers": {"apex": 99}, "marginPctTiers": {"apex": 999},
                                       "maxLeverage": 3, "maxMarginPct": 18}) == (3, 18.0)


# ── scan() smoke ──

class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, trending=False): self.trending = trending
    def call_tool(self, tool, args):
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"assetPositions": []}}
        if tool == "market_list_instruments":
            return {"instruments": [
                {"name": "BTC", "context": {"dayNtlVlm": 9e8, "max_leverage": 20}},
                {"name": "DUST", "context": {"dayNtlVlm": 1e5, "max_leverage": 5}},
            ]}
        if tool == "market_get_asset_data":
            c = (_trend() + [{"h": 130, "l": 128, "c": 128.2, "v": 1000}]) if self.trending \
                else (_range() + [{"h": 99, "l": 97.9, "c": 98.1, "v": 1000}])
            return {"data": {"candles": {"1h": c}}}
        return {}


class _Ctx:
    def __init__(self, mcp): self.senpi_mcp, self.wallet, self.state = mcp, "0xweaver", _State()


_INPUTS = {"volFloorUsd": 2.5e7, "maxUniverse": 30, "maxSlots": 5,
           "recentSignalTtlSeconds": 21600, "minScore": 0,
           "leverageTiers": {"apex": 3, "good": 2, "base": 2},
           "marginPctTiers": {"apex": 9, "good": 7, "base": 5},
           "maxLeverage": 3, "maxMarginPct": 18}


def test_scan_harvests_a_range_and_skips_illiquid():
    ctx = _Ctx(_MCP(trending=False))
    out = scan.scan(dict(_INPUTS), ctx)
    assert out, "a contained band at its floor should produce an entry"
    assert "DUST" not in {s["asset"] for s in out}
    for s in out:
        assert 0 < s["marginPct"] <= 18 and 1 <= s["leverage"] <= 3
        assert s["data"]["efficiencyRatio"] < 0.35


def test_scan_opens_nothing_in_a_trending_market():
    ctx = _Ctx(_MCP(trending=True))
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
    print(f"\nALL {len(fns)} WEAVER TESTS PASS")
