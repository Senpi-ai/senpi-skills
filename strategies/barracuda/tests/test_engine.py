"""Barracuda engine tests. Pure/deterministic scoring checks, plus scan() and
close_all.scan() smoke runs against a fake in-memory ctx (no network).
Run: python3 -m pytest strategies/barracuda/tests -q
  or plain: python3 strategies/barracuda/tests/test_engine.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402


# ── candle helpers ────────────────────────────────────────────────────
def _candle(c, o=None, v=100.0):
    o = c if o is None else o
    return {"o": o, "h": max(o, c), "l": min(o, c), "c": c, "v": v}


def _rising(n, start=100.0, step=1.0, spike=True):
    """n bullish candles trending up; last candle carries a 3x volume spike."""
    out = []
    for i in range(n):
        c = start + i * step
        vol = 300.0 if (spike and i == n - 1) else 100.0
        out.append(_candle(c, o=c - step, v=vol))
    return out


# ── (a) numeric + helpers ─────────────────────────────────────────────
def test_f_and_is_xyz_and_pct_change():
    assert scoring._f("12.5") == 12.5           # casts strings (HL candle values)
    assert scoring._f(None, 7.0) == 7.0
    assert scoring._f("nope", 3.0) == 3.0
    assert scoring.is_xyz("xyz:NVDA") is True
    assert scoring.is_xyz("BTC") is False
    assert scoring.pct_change(100, 101) == 1.0
    assert scoring.pct_change(0, 5) == 0.0       # non-positive base → 0, never a divide error
    assert scoring.pct_change(100, 99) == -1.0


def test_compute_rsi_monotonic_and_mixed():
    assert scoring.compute_rsi(_rising(20)) == 100.0           # only gains → 100
    mixed = [_candle(c) for c in
             [100, 101, 102, 103, 104, 105, 106, 107, 106, 105, 104, 103, 102, 101, 100]]
    assert scoring.compute_rsi(mixed) == 50.0                  # 7 up / 7 down → RSI 50
    assert scoring.compute_rsi(_rising(5)) is None             # not enough data


def test_score_candle_long_beats_short_on_breakout():
    s = scoring.score_candle(_rising(60), price=159.0)
    assert s is not None
    assert s["long_score"] > s["short_score"]                 # uptrend near-high scores LONG
    assert s["near_high_pct"] < scoring.NEAR_EXTREME_PCT       # current sits at the recent high
    assert s["vol_ratio"] > 1.5                                # the volume spike registered


def test_score_candle_handles_string_values():
    # Hyperliquid can serve o/h/l/c/v as strings — _f() must make scoring shape-proof.
    strs = [{"o": str(c - 1), "h": str(c), "l": str(c - 1), "c": str(c), "v": "100"}
            for c in range(100, 115)]
    s = scoring.score_candle(strs, price=114.0)
    assert s is not None and s["long_score"] >= s["short_score"]


def test_score_candle_multi_blends_1h_and_4h():
    blend = scoring.score_candle_multi(_rising(30), _rising(30), price=129.0)
    assert blend is not None and blend["long_score"] > 0
    # blend = 0.6*4h + 0.4*1h; identical inputs → equal to either single score
    single = scoring.score_candle(_rising(30), price=129.0)["long_score"]
    assert abs(blend["long_score"] - single) < 0.05


def test_score_price_change_direction_and_veto():
    assert scoring.score_price_change(1.0, 2.0)["long_score"] > 0     # 4h+1h up → LONG
    assert scoring.score_price_change(-1.0, -2.0)["short_score"] > 0  # 4h+1h down → SHORT
    assert scoring.score_price_change(-2.0, 2.0) is None              # 1h drop >1% vetoes the long
    assert scoring.score_price_change(0.5, 0.1) is None               # 4h below the floor → no signal


def test_score_funding_tiers_and_alignment():
    assert scoring.score_funding({"funding_annualized_pct": 12}, "LONG")["score"] == 5.0
    aligned = scoring.score_funding(
        {"funding_annualized_pct": 12, "funding_direction": "LONG"}, "LONG")
    assert aligned["score"] == 7.0                             # aligned crowd → +2 squeeze fuel
    assert scoring.score_funding({"funding_annualized_pct": 3}, "SHORT")["score"] == 3.0
    assert scoring.score_funding(None, "LONG") is None


def test_score_momentum_acceleration():
    # needs >= 5 candles: the last body is compared to the mean of the prior three
    accel = [_candle(100.1, o=100.0)] * 4 + [_candle(102.0, o=100.0)]  # big last body
    assert scoring.score_momentum_acceleration(accel) == 2.0
    stall = [_candle(102.0, o=100.0)] * 4 + [_candle(100.0, o=100.0)]  # tiny last body
    assert scoring.score_momentum_acceleration(stall) == -2.0
    assert scoring.score_momentum_acceleration(_rising(4)) == 0.0      # not enough data


def test_score_oi_velocity_clamped():
    assert scoring.score_oi_velocity(
        {"oi_trend": "BUILDING", "oi_acceleration": "INCREASING", "oi_change_pct_1h": 3}) == 3.0
    assert scoring.score_oi_velocity(
        {"oi_trend": "DECLINING", "oi_acceleration": "DECREASING"}) == -3.0
    assert scoring.score_oi_velocity(None) == 0.0


def test_score_size_buckets():
    assert scoring.score_size(10) == scoring.SIZE_MICRO_BONUS   # micro cap
    assert scoring.score_size(30) == scoring.SIZE_SMALL_BONUS   # small cap
    assert scoring.score_size(250) == -scoring.SIZE_LARGE_PENALTY
    assert scoring.score_size(100) == 0.0                       # mid cap neutral
    assert scoring.market_size_label(10) == "micro"
    assert scoring.market_size_label(250) == "large"


def test_detect_consolidation():
    tight = [_candle(c) for c in [100, 100.5, 100.2, 100.8, 100.3, 100.6, 100.4]]
    assert scoring.detect_consolidation(tight, lookback=6) is True
    wide = [_candle(c) for c in [100, 105, 95, 110, 90, 108, 101]]
    assert scoring.detect_consolidation(wide, lookback=6) is False


def test_score_fast_momentum_flags_spike():
    c15 = [_candle(100, v=100), _candle(100, v=100), _candle(100, v=100),
           _candle(103, v=1000)]                                # +3% with a real volume spike
    fm = scoring.score_fast_momentum(c15)
    assert fm is not None and fm["is_fast_track"] is True and fm["long_score"] > 0
    assert scoring.score_fast_momentum([_candle(100)] * 3) is None   # not enough data


def test_combine_score_weights_and_conviction():
    total = scoring.combine_score(cs=5, ps=3, fs=2, smart_money_score=3, accel_adj=1,
                                  oi_adj=2, fast_adj=0, size_score=2, regime_bonus=0)
    # 5+3+2 + 3*1.0 + 1 + 2*2.0 + 0 + 2*1.5 + 0 = 21
    assert total == 21.0
    assert scoring.combine_score(5, 3, 2, 3, 1, 2, 0, 2, 0, conflict_penalty=3.0) == 18.0
    assert scoring.conviction_tier(26) == "high"
    assert scoring.conviction_tier(20) == "medium"
    assert scoring.conviction_tier(19.9) == "low"


# ── (b) scan() smoke — fake ctx / MCP, no network ────────────────────
class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)


class _MCP:
    """Canned universe: MOON is a clean liquid LONG breakout held by 3 top traders."""
    def call_tool(self, tool, args):
        a = args or {}
        if tool == "leaderboard_get_top":
            return {"data": [{"top_markets": ["MOON"]},
                             {"top_markets": ["MOON"]},
                             {"top_markets": ["MOON"]}]}
        if tool == "market_get_prices":
            return {"prices": {"MOON": "115.0"}}               # map shape (symbol -> price)
        if tool == "market_get_funding_regime":
            return {"regime": "NEUTRAL", "long_funding_assets": 10, "short_funding_assets": 10}
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"assetPositions": []}, "xyz": {"assetPositions": []}}
        if tool == "market_get_asset_data":
            asset = a.get("asset")
            if asset == "BTC":                                  # flat → normal regime
                flat = [_candle(100)] * 3
                return {"candles": {"4h": flat, "1h": flat}}
            return {                                            # MOON — rising breakout
                "candles": {"15m": [_candle(115)] * 4,
                            "1h": _rising(60), "4h": _rising(15)},
                "asset_context": {"markPx": 159.0, "dayNtlVlm": 20_000_000, "funding": 0.0},
                "oi_velocity": {"oi_trend": "BUILDING", "oi_acceleration": "INCREASING",
                                "oi_change_pct_1h": 3.0},
            }
        return {}


class _EmptyMCP:
    def call_tool(self, tool, args):
        if tool == "market_get_prices":
            return {"prices": {}}
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"assetPositions": []}, "xyz": {"assetPositions": []}}
        return {}


class _Ctx:
    def __init__(self, mcp):
        self.wallet = "0xpumphunter"
        self.state = _State()
        self.senpi_mcp = mcp
        self.scanner_name = "pump_signals"
        self.interval_seconds = 60
        self.dry_run = False


_SMOKE_INPUTS = {
    "min_score": 5, "max_positions": 4, "margin_pct": 30, "default_leverage": 5,
    "xyz_max_leverage": 6, "min_volume_usd_m": 15.0, "max_scan_coins": 50,
    "parallel_workers": 2, "rsi_overbought": 100, "rsi_oversold": 0,
    "recent_signal_ttl_seconds": 180,
}

_SCHEMA_KEYS = {
    "total_score", "momentum_1h", "momentum_4h", "candle_score", "smart_money_score",
    "funding_score", "volume_usd_m", "funding_annualized_pct", "rsi", "acceleration",
    "oi_score", "oi_trend", "size_score", "market_size", "fast_track", "ch_15m", "conviction",
}


def test_scan_smoke_emits_valid_long_and_dedups():
    import scan
    ctx = _Ctx(_MCP())
    out = scan.scan(dict(_SMOKE_INPUTS), ctx)
    assert isinstance(out, list) and len(out) == 1
    s = out[0]
    # required contract keys present, sizing at the TOP level (never inside data{})
    assert {"asset", "direction", "marginPct", "leverage", "data"} <= set(s)
    assert s["asset"] == "MOON" and s["direction"] == "LONG"
    assert 0 < s["marginPct"] <= 100 and s["marginPct"] == 30
    assert s["leverage"] == 5                                   # non-xyz → base leverage
    # data{} matches the runtime.yaml signal_data_schema exactly (no unknown keys)
    assert set(s["data"].keys()) == _SCHEMA_KEYS
    assert s["data"]["total_score"] >= 5 and s["data"]["conviction"] in ("low", "medium", "high")
    assert ctx.state.last() is not None and "MOON" in ctx.state.last()["recent"]

    # second tick: MOON is within the recent-signal TTL → no re-emit (anti-churn)
    assert scan.scan(dict(_SMOKE_INPUTS), ctx) == []


class _XyzMCP(_MCP):
    """Same breakout, but the held name is an xyz: equity (parent returns the
    breakout candles for any non-BTC asset)."""
    def call_tool(self, tool, args=None):
        if tool == "leaderboard_get_top":
            return {"data": [{"top_markets": ["xyz:NVDA"]}] * 3}
        if tool == "market_get_prices":
            return {"prices": {"xyz:NVDA": "159.0"}}
        return super().call_tool(tool, args)


def test_scan_xyz_leverage_is_clamped():
    import scan
    ctx = _Ctx(_XyzMCP())
    inp = dict(_SMOKE_INPUTS)
    inp["default_leverage"] = 9           # request 9x; xyz venue cap is 6
    inp["rsi_overbought_xyz"] = 100       # don't let the canned RSI(=100) block the long
    out = scan.scan(inp, ctx)
    assert len(out) == 1 and out[0]["asset"] == "xyz:NVDA"
    assert out[0]["leverage"] == 6        # clamped to xyz_max_leverage


def test_scan_degrades_on_empty_universe():
    import scan
    ctx = _Ctx(_EmptyMCP())
    out = scan.scan(dict(_SMOKE_INPUTS), ctx)
    assert out == []                                           # nothing to scan → empty, no crash
    assert ctx.state.last() is not None                        # still persisted


# ── (c) close_all — flatten the book at the total-profit target ──────
class _CloseMCP:
    def __init__(self, upnl):
        self._upnl = upnl

    def call_tool(self, tool, args):
        if tool == "strategy_get_clearinghouse_state":
            u1, u2 = self._upnl
            return {
                "main": {"marginSummary": {"accountValue": 100.0},
                         "assetPositions": [
                             {"position": {"coin": "MOON", "szi": 10, "marginUsed": 20,
                                           "unrealizedPnl": u1}},
                             {"position": {"coin": "SUN", "szi": -5, "marginUsed": 20,
                                           "unrealizedPnl": u2}}]},
                "xyz": {"marginSummary": {"accountValue": 0.0}, "assetPositions": []},
            }
        return {}


def test_close_all_fires_at_target_and_holds_below():
    import close_all
    ctx = _Ctx(_CloseMCP((4.0, 2.0)))                          # +6 total on 100 account = +6%
    out = close_all.scan({"profit_pct_close_all": 5.0}, ctx)
    assert len(out) == 2                                       # both positions flattened
    by = {s["asset"]: s for s in out}
    assert by["MOON"]["direction"] == "LONG" and by["SUN"]["direction"] == "SHORT"
    assert by["MOON"]["data"]["total_upnl_pct"] == 6.0
    assert by["MOON"]["data"]["profit_target"] == 5.0

    # same book, higher target → below threshold → hold (no close)
    ctx2 = _Ctx(_CloseMCP((4.0, 2.0)))
    assert close_all.scan({"profit_pct_close_all": 10.0}, ctx2) == []

    # no positions → empty
    ctx3 = _Ctx(_EmptyMCP())
    assert close_all.scan({"profit_pct_close_all": 5.0}, ctx3) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} BARRACUDA TESTS PASS")
