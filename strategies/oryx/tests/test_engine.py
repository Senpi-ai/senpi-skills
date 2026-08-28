"""Oryx engine tests. Pure math + a scan() smoke run against a fake ctx (no network).
Run: python3 strategies/oryx/tests/test_engine.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402


def _range(n=20, mid=100.0, half=0.5, vol=1000.0):
    """A settling opening range — oscillates so RSI has both gains and losses."""
    out = []
    for i in range(n):
        c = mid + (half * 0.4 if i % 2 else -half * 0.4)
        out.append({"h": mid + half, "l": mid - half, "c": c, "v": vol})
    return out


def _breakout(n=14, start=100.6, step=0.18, half=0.45, vol=3000.0):
    """Break upward WITH pullbacks — a vertical gap pins RSI at 100 and is correctly
    refused as extended, so a realistic fixture has to breathe."""
    out, c = [], start
    for i in range(n):
        c += step * (-0.8 if i % 3 == 2 else 1.0)
        out.append({"h": c + half, "l": c - half, "c": c, "v": vol})
    return out


def _breakdown(n=14, start=99.4, step=0.12, half=0.45, vol=3000.0):
    """Break downward WITH bounces — the mirror of _breakout."""
    out, c = [], start
    for i in range(n):
        c -= step * (-0.9 if i % 3 == 2 else 1.0)
        out.append({"h": c + half, "l": c - half, "c": c, "v": vol})
    return out


# ── the venue-naming guard: the silent-failure class this template must not repeat ──

def test_xyz_prefix_is_always_reattached():
    assert scan._venue_name("GOLD") == "xyz:GOLD"          # a bare token must never leak
    assert scan._venue_name("xyz:GOLD") == "xyz:GOLD"      # already-prefixed passes through
    assert scan._venue_name("XYZ:GOLD") == "xyz:GOLD"      # prefix canonicalised to lowercase
    assert scan._venue_name("kPEPE") == "xyz:kPEPE"        # TOKEN case preserved — names are case-sensitive
    assert scan._bare("xyz:BRENTOIL") == "BRENTOIL"        # bare form is for dedup only


# ── opening-range breakout ──

def test_opening_range_and_break_direction():
    candles = _range() + _breakout()
    hi, lo = scoring.opening_range(candles, 12)
    assert hi > lo and hi <= 100.5 + 1e-9
    th = scoring.build_thesis("xyz:GOLD", candles, {})
    assert th and th["direction"] == "LONG", th
    dth = scoring.build_thesis("xyz:GOLD", _range() + _breakdown(), {})
    assert dth and dth["direction"] == "SHORT", dth


def test_inside_the_range_is_not_a_trade():
    assert scoring.build_thesis("xyz:GOLD", _range(40), {}) is None


def test_break_without_volume_is_a_fakeout():
    quiet = _range() + _breakout(vol=900.0)               # break, but on BELOW-average volume
    assert scoring.build_thesis("xyz:GOLD", quiet, {"minVolSurge": 1.3}) is None


def test_chasing_a_runaway_break_is_refused():
    candles = _range() + _breakout()
    assert scoring.build_thesis("xyz:GOLD", candles, {"maxChasePct": 0.01}) is None
    assert scoring.build_thesis("xyz:GOLD", candles, {"maxChasePct": 50}) is not None


def test_rsi_guard_is_loose_because_breaks_are_supposed_to_be_extreme():
    """A breakout system must not reuse momentum RSI bounds. At a genuine break RSI IS
    extreme; an 80/20 guard refuses nearly every real breakdown and duplicates
    maxChasePct, which is the correct anti-chase control."""
    candles = _range() + _breakdown()
    assert scoring.build_thesis("xyz:GOLD", candles, {}) is not None          # ships loose
    assert scoring.build_thesis("xyz:GOLD", candles, {"rsiMinShort": 20}) is None  # momentum bounds kill it


def test_fee_gate_still_governs_on_the_thinner_xyz_venue():
    """Carried from Swift deliberately: XYZ books are thinner, so cost matters MORE."""
    dead = [{"h": 100.001, "l": 99.999, "c": 100, "v": 1000} for _ in range(20)] + \
           [{"h": 100.003, "l": 100.001, "c": 100.002, "v": 3000} for _ in range(14)]
    assert scoring.build_thesis("xyz:GOLD", dead, {}) is None
    assert scoring.clears_fees(dead, {})[0] is False


# ── scan() smoke ──

class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    """Returns a BARE token deliberately — the scanner must re-prefix it before use."""
    def __init__(self): self.asset_reads = []
    def call_tool(self, tool, args):
        if tool == "strategy_get_clearinghouse_state":
            return {"xyz": {"assetPositions": []}}
        if tool == "market_list_instruments":
            assert (args or {}).get("dex") == "xyz", "must read the xyz DEX"
            return {"instruments": [
                {"name": "GOLD", "context": {"dayNtlVlm": 5e7, "max_leverage": 10}},   # BARE on purpose
                {"name": "xyz:BRENTOIL", "context": {"dayNtlVlm": 2e7, "max_leverage": 10}},
                {"name": "THIN", "context": {"dayNtlVlm": 1e5, "max_leverage": 5}},
            ]}
        if tool == "market_get_asset_data":
            self.asset_reads.append({"asset": (args or {}).get("asset"), "dex": (args or {}).get("dex")})
            return {"data": {"candles": {"15m": _range() + _breakout()}}}
        return {}


class _Ctx:
    def __init__(self, mcp): self.senpi_mcp, self.wallet, self.state = mcp, "0xoryx", _State()


_INPUTS = {"volFloorUsd": 3e6, "maxUniverse": 20, "maxSlots": 4, "recentSignalTtlSeconds": 7200,
           "minScore": 0, "leverageTiers": {"apex": 4, "good": 3, "base": 2},
           "marginPctTiers": {"apex": 8, "good": 6, "base": 4},
           "maxLeverage": 4, "maxMarginPct": 20}


def test_scan_emits_prefixed_assets_and_reads_the_prefixed_name():
    mcp = _MCP()
    ctx = _Ctx(mcp)
    out = scan.scan(dict(_INPUTS), ctx)
    assert out, "a confirmed break on a liquid XYZ name should produce an entry"
    for s in out:
        assert s["asset"].startswith("xyz:"), f"emitted a bare token: {s['asset']}"
        assert 0 < s["marginPct"] <= 20 and 1 <= s["leverage"] <= 4
    # THE FIX: the bare GOLD row was addressed as xyz:GOLD on the market read, with dex set
    assert any(r["asset"] == "xyz:GOLD" and r["dex"] == "xyz" for r in mcp.asset_reads), mcp.asset_reads
    assert all(r["asset"].startswith("xyz:") for r in mcp.asset_reads), mcp.asset_reads
    assert {"THIN", "xyz:THIN"}.isdisjoint({s["asset"] for s in out})   # sub-floor skipped


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
    print(f"\nALL {len(fns)} ORYX TESTS PASS")
