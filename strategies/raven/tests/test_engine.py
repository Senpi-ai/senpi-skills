"""Raven engine + self-calibration tests. Pure/deterministic; plus a scan() smoke
run against a fake ctx (no network). Run: python3 -m pytest strategies/raven/tests -q
or plain `python3 strategies/raven/tests/test_engine.py`."""
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


def test_track_record_basic():
    # newest-first: two losers then wins — loss_streak counts from the newest end
    closed = [{"realizedPnl": -5}, {"realizedPnl": -3}, {"realizedPnl": 10}, {"realizedPnl": 4}]
    s = scoring.track_record(closed, 40)
    assert s["n"] == 4
    assert s["win_rate"] == 0.5
    assert round(s["profit_factor"], 3) == round(14 / 8, 3)
    assert s["loss_streak"] == 2
    assert s["sum_pnl"] == 6


def test_track_record_tolerant_keys_and_empty():
    assert scoring.track_record([{"realized_profit_and_loss": 7}], 40)["n"] == 1
    assert scoring.track_record([{"nope": 1}], 40)["n"] == 0     # unparseable → n=0 (caller holds)
    assert scoring.track_record([], 40)["n"] == 0


def test_adapt_cold_tightens_hot_loosens_and_holds():
    inp = {"initialMinScore": 8, "maxMinScore": 12, "minTrades": 8}
    st = {"current_min_score": 8, "size_scale": 1.0}
    # cold: low win-rate → floor up, size down
    cold = {"n": 10, "win_rate": 0.2, "profit_factor": 0.6, "loss_streak": 1}
    m, sc, _ = scoring.adapt(cold, st, inp)
    assert m > 8 and sc < 1.0
    # hot: strong → floor down (already at floor, stays), size up
    hot = {"n": 10, "win_rate": 0.7, "profit_factor": 2.0, "loss_streak": 0}
    m2, sc2, _ = scoring.adapt(hot, {"current_min_score": 10, "size_scale": 1.0}, inp)
    assert m2 < 10 and sc2 > 1.0
    # insufficient trades → hold exactly
    m3, sc3, note = scoring.adapt({"n": 3}, st, inp)
    assert m3 == 8 and sc3 == 1.0 and "hold" in note
    # cold streak alone forces tighten even with ok win-rate
    streaky = {"n": 10, "win_rate": 0.5, "profit_factor": 1.2, "loss_streak": 5}
    m4, sc4, _ = scoring.adapt(streaky, st, inp)
    assert m4 > 8


def test_adapt_respects_bounds():
    inp = {"initialMinScore": 8, "maxMinScore": 12, "minTrades": 8}
    hot = {"n": 20, "win_rate": 0.9, "profit_factor": 5, "loss_streak": 0}
    # already at ceilings — must clamp, not overshoot
    m, sc, _ = scoring.adapt(hot, {"current_min_score": 8, "size_scale": 1.5}, inp)
    assert m == 8 and sc == 1.5


def test_build_thesis_direction_and_insufficient():
    up1h, up4h = _synth_candles(12, 1.0), _synth_candles(6, 2.0)
    th = scoring.build_thesis("BTC", up1h[-3:], up1h, up4h, funding=-0.01, sm=(None, 0), inputs={})
    assert th and th["direction"] == "LONG" and th["score"] > 0
    assert scoring.build_thesis("X", [], up1h[:4], up4h[:2], 0, (None, 0), {}) is None  # too few candles


def test_sizing_caps():
    lev, mgn = scoring.sizing_for("apex", 1.5, {"maxLeverage": 5, "maxMarginPct": 25}, venue_max=3)
    assert lev == 3            # clamped to venue max
    assert 0 < mgn <= 25       # margin capped


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
        if tool == "discovery_get_trader_history":
            return {"data": {"closedPositions": [{"realizedPnl": 5}, {"realizedPnl": -2}]}}
        return {}


class _Ctx:
    def __init__(self, up):
        self.wallet = "0xabc"
        self.state = _State()
        self.senpi_mcp = _MCP(up)


def test_scan_smoke_returns_valid_signals():
    import scan
    up = _synth_candles(14, 1.0)
    ctx = _Ctx(up)
    inputs = {"maxSlots": 6, "initialMinScore": 0, "minTrades": 8, "includeXyz": False,
              "universeVolFloorUsd": 1e6, "maxUniverse": 10}
    out = scan.scan(inputs, ctx)
    assert isinstance(out, list)
    for s in out:
        assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
        assert 0 < s["marginPct"] <= 25 and 1 <= s["leverage"] <= 5
        assert s["direction"] in ("LONG", "SHORT")
    assert ctx.state.last() is not None            # state persisted


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} RAVEN TESTS PASS")
