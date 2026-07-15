"""Ant funding-harvester tests. Pure/deterministic + a scan() smoke against a fake
ctx. Funding uses the REAL market_get_funding_history row shape (pangolin-proven:
{asset, funding_direction, annualized_pct, persistence_hours, trend}).
Run: python3 strategies/ant/tests/test_engine.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402


def _c(seq):
    """closes → OHLC candles (high=close+.5, low=close-.5)."""
    return [{"open": p, "high": p + 0.5, "low": p - 0.5, "close": p, "volume": 1000} for p in seq]


def _blowoff(n=14):
    """Rip up then roll over: elevated RSI, non-bullish structure, negative late momentum."""
    up = [100 + 3 * i for i in range(n - 4)]
    top = up[-1]
    roll = [top - 2, top - 4, top - 6, top - 8]
    return _c(up + roll)


def _gentle_up(n=14):
    """Clean, actively-rising uptrend → 'still ripping' (must NOT be shorted)."""
    return _c([100 + 1.5 * i for i in range(n)])


def _fund(direction="SHORT", apr=44.0, persist=8.0, trend="STABLE", asset="BTC"):
    """One market_get_funding_history row (the pangolin/live shape)."""
    return {"asset": asset, "funding_direction": direction, "annualized_pct": apr,
            "persistence_hours": persist, "trend": trend}


def test_funding_signal_gates():
    inp = {"targetApr": 30, "minPersistHours": 6}
    fs = scoring.funding_signal(_fund(), inp)                              # SHORT collects, rich, persistent
    assert fs and fs[0] == 44 and fs[1] == 8
    assert scoring.funding_signal(_fund(direction="LONG"), inp) is None    # shorts would PAY → not for ant
    assert scoring.funding_signal(_fund(apr=20), inp) is None              # below target APR
    assert scoring.funding_signal(_fund(persist=3), inp) is None           # one-hour spike, not persistent
    assert scoring.funding_signal(_fund(trend="DECAYING"), inp) is None    # funding drying up
    assert scoring.funding_signal(None, inp) is None                       # fails closed
    assert scoring.funding_signal({}, inp) is None


def test_exhaustion_and_still_ripping():
    ex, _, ripping = scoring.exhaustion_score(_blowoff(), _blowoff(8))
    assert ex >= 1 and ripping is False           # tired crowd → shortable
    _, _, ripping2 = scoring.exhaustion_score(_gentle_up(), _gentle_up(8))
    assert ripping2 is True                        # still charging → do NOT short


def test_build_signal_gates():
    inp = {"targetApr": 30, "minPersistHours": 6, "minExhaustion": 1}
    # qualifying: harvestable SHORT funding + exhausted crowd → SHORT
    th = scoring.build_signal("BTC", _fund(), 8e8, _blowoff(), _blowoff(8), inp)
    assert th and th["direction"] == "SHORT" and th["score"] > 0 and th["apr"] > 30
    # LONG-collecting funding (shorts pay) → None
    assert scoring.build_signal("BTC", _fund(direction="LONG"), 8e8, _blowoff(), _blowoff(8), inp) is None
    # below target APR → None
    assert scoring.build_signal("BTC", _fund(apr=20), 8e8, _blowoff(), _blowoff(8), inp) is None
    # still ripping (fresh strength) → None even with rich funding
    assert scoring.build_signal("BTC", _fund(), 8e8, _gentle_up(), _gentle_up(8), inp) is None
    # no funding row → None (fails closed)
    assert scoring.build_signal("BTC", None, 8e8, _blowoff(), _blowoff(8), inp) is None


def test_sizing_is_low_leverage_and_capped():
    lev, mgn = scoring.sizing_for("apex", {"maxLeverage": 4, "maxMarginPct": 20}, venue_max=10)
    assert 1 <= lev <= 4 and 0 < mgn <= 20         # funding shorts stay low-lev
    lev2, _ = scoring.sizing_for("apex", {"maxLeverage": 4}, venue_max=2)
    assert lev2 == 2                                # venue cap wins


# ── scan() smoke against a fake ctx ──

class _State:
    def __init__(self): self._l = []
    def last(self): return self._l[-1] if self._l else None
    def append(self, d): self._l.append(d)


class _MCP:
    def call_tool(self, tool, args):
        if tool == "market_list_instruments":
            return {"data": {"instruments": [
                {"name": "BTC", "context": {"dayNtlVlm": 9e8, "maxLeverage": 10}},
                {"name": "ETH", "context": {"dayNtlVlm": 8e8, "maxLeverage": 10}}]}}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"1h": _blowoff(), "4h": _blowoff(8)},
                             "asset_context": {"openInterest": 6e8, "markPx": 1.0}}}
        if tool == "market_get_funding_history":            # real shape: data.data list of rows
            return {"data": {"data": [
                {"asset": "BTC", "funding_direction": "SHORT", "annualized_pct": 44.0,
                 "persistence_hours": 8.0, "trend": "STABLE"},
                {"asset": "ETH", "funding_direction": "SHORT", "annualized_pct": 44.0,
                 "persistence_hours": 8.0, "trend": "STABLE"}]}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"assetPositions": []}}
        return {}


class _Ctx:
    wallet = "0xabc"
    def __init__(self): self.state = _State(); self.senpi_mcp = _MCP()


def test_scan_smoke_shorts_only_and_valid():
    import scan
    ctx = _Ctx()
    out = scan.scan({"maxSlots": 5, "targetApr": 30, "minPersistHours": 6, "shortlistByVol": 20,
                     "topOiCount": 10, "universeVolFloorUsd": 1e6, "maxLeverage": 4}, ctx)
    assert isinstance(out, list) and out, "smoke should emit at least one short"
    for s in out:
        assert s["direction"] == "SHORT"
        assert 0 < s["marginPct"] <= 20 and 1 <= s["leverage"] <= 4
        assert s["data"]["fundingApr"] > 30
    assert ctx.state.last() is not None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} ANT TESTS PASS")
