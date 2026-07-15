"""RAM engine tests. Pure/deterministic scorer + safe-haven tilt + sizing caps,
plus a scan() smoke run against a fake ctx (no network). Run:
  python3 -m pytest strategies/ram/tests -q
  python3 strategies/ram/tests/test_engine.py
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


# ── thesis: direction + insufficient-history guard ──

def test_build_thesis_uptrend_is_long_downtrend_is_short():
    up1h, up4h = _synth_candles(14, 1.0), _synth_candles(6, 2.0)
    th = scoring.build_thesis("GOLD", up1h, up4h, funding=-0.01, sm=(None, 0),
                              risk_off=False, inputs={})
    assert th and th["direction"] == "LONG" and th["score"] > 0

    dn1h, dn4h = _synth_candles(14, -1.0), _synth_candles(6, -2.0)
    th_s = scoring.build_thesis("GOLD", dn1h, dn4h, funding=0.01, sm=(None, 0),
                                risk_off=False, inputs={})
    assert th_s and th_s["direction"] == "SHORT" and th_s["score"] > 0


def test_build_thesis_none_on_insufficient_candles():
    up = _synth_candles(14, 1.0)
    # < 8 1h candles → None
    assert scoring.build_thesis("GOLD", up[:4], up, 0, (None, 0), False, {}) is None
    # < 4 4h candles → None
    assert scoring.build_thesis("GOLD", up, up[:3], 0, (None, 0), False, {}) is None


# ── safe-haven tilt: adds to LONG when risk_off, never to SHORT ──

def test_safe_haven_tilt_adds_to_long_only():
    up1h, up4h = _synth_candles(14, 1.0), _synth_candles(6, 2.0)
    base = scoring.build_thesis("GOLD", up1h, up4h, -0.01, (None, 0), False,
                                {"safeHavenBonusLong": 2})
    tilted = scoring.build_thesis("GOLD", up1h, up4h, -0.01, (None, 0), True,
                                  {"safeHavenBonusLong": 2})
    assert tilted["direction"] == "LONG"
    assert tilted["score"] == base["score"] + 2
    assert any("safe_haven" in r for r in tilted["reasons"])
    assert not any("safe_haven" in r for r in base["reasons"])

    # a SHORT is never given the safe-haven tilt
    dn1h, dn4h = _synth_candles(14, -1.0), _synth_candles(6, -2.0)
    s_base = scoring.build_thesis("GOLD", dn1h, dn4h, 0.01, (None, 0), False,
                                  {"safeHavenBonusLong": 2})
    s_tilt = scoring.build_thesis("GOLD", dn1h, dn4h, 0.01, (None, 0), True,
                                  {"safeHavenBonusLong": 2})
    assert s_base["direction"] == "SHORT" and s_tilt["score"] == s_base["score"]


# ── risk-off derivation (pure, tolerant, categorical-only) ──

def test_risk_off_from_regime_categorical_and_safe_default():
    assert scoring.risk_off_from_regime({"regime": "risk_off"}) is True
    assert scoring.risk_off_from_regime({"regime": "RISK-OFF (extreme)"}) is True
    assert scoring.risk_off_from_regime({"label": "defensive"}) is True
    assert scoring.risk_off_from_regime({"is_risk_off": True}) is True
    assert scoring.risk_off_from_regime({"data": {"regime": "flight_to_safety"}}) is True
    # risk-on / neutral / unknown / mis-shaped → False (safe default, tilt off)
    assert scoring.risk_off_from_regime({"regime": "risk_on"}) is False
    assert scoring.risk_off_from_regime({"regime": "neutral"}) is False
    assert scoring.risk_off_from_regime({"avg_funding": -0.05}) is False   # never inferred from a number
    assert scoring.risk_off_from_regime({}) is False
    assert scoring.risk_off_from_regime(None) is False
    assert scoring.risk_off_from_regime("nope") is False


# ── band + sizing caps ──

def test_band_for_thresholds():
    inp = {"apexScore": 9, "goodScore": 7}
    assert scoring.band_for(9, inp) == "apex"
    assert scoring.band_for(7, inp) == "good"
    assert scoring.band_for(6, inp) == "base"


def test_sizing_caps_and_venue_clamp():
    inp = {"leverageTiers": {"apex": 5, "good": 4, "base": 3},
           "marginPctTiers": {"apex": 20, "good": 15, "base": 10},
           "maxLeverage": 5, "maxMarginPct": 25}
    lev, mgn = scoring.sizing_for("apex", inp)
    assert lev == 5 and 0 < mgn <= 25 and mgn == 20
    lev2, mgn2 = scoring.sizing_for("base", inp)
    assert lev2 == 3 and mgn2 == 10
    # venue max clamps leverage below the fleet cap
    lev3, _ = scoring.sizing_for("apex", inp, venue_max=3)
    assert lev3 == 3
    # marginPct is hard-capped at maxMarginPct
    _, mgn4 = scoring.sizing_for("apex", {"marginPctTiers": {"apex": 40}, "maxMarginPct": 25})
    assert mgn4 == 25


# ── scan() smoke against a fake ctx (no network) ──

class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, candles, held=False, regime=None):
        self.candles = candles
        self.held = held
        self.regime = regime

    def call_tool(self, tool, args):
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"1h": self.candles, "4h": self.candles},
                             "asset_context": {"markPx": 2400.0}, "funding": -0.01}}
        if tool == "strategy_get_clearinghouse_state":
            if self.held:
                return {"data": {"assetPositions": [
                    {"position": {"coin": "GOLD", "szi": 1.5}}]}}
            return {"data": {"assetPositions": []}}
        if tool == "market_get_funding_regime":
            return {"data": self.regime} if self.regime is not None else {}
        return {}


class _Ctx:
    def __init__(self, candles, held=False, regime=None):
        self.wallet = "0xram"
        self.state = _State()
        self.senpi_mcp = _MCP(candles, held=held, regime=regime)


_INPUTS = {"asset": "xyz:GOLD", "minScore": 5, "apexScore": 9, "goodScore": 7,
           "leverageTiers": {"apex": 5, "good": 4, "base": 3},
           "marginPctTiers": {"apex": 20, "good": 15, "base": 10},
           "maxLeverage": 5, "maxMarginPct": 25, "recentSignalTtlSeconds": 3600}


def test_scan_smoke_returns_one_valid_gold_signal():
    import scan
    ctx = _Ctx(_synth_candles(14, 1.0), held=False, regime={"regime": "risk_off"})
    out = scan.scan(dict(_INPUTS), ctx)
    assert isinstance(out, list) and len(out) == 1
    s = out[0]
    assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
    assert s["asset"] == "xyz:GOLD"                    # emitted WITH the xyz: prefix
    assert s["direction"] in ("LONG", "SHORT")
    assert 0 < s["marginPct"] <= 25
    assert 1 <= s["leverage"] <= 5
    d = s["data"]
    assert set(("score", "leverage", "direction", "band", "reasons")) <= set(d)
    assert ctx.state.last() is not None                # state persisted


def test_scan_emits_nothing_when_gold_already_held():
    import scan
    ctx = _Ctx(_synth_candles(14, 1.0), held=True, regime={"regime": "risk_off"})
    out = scan.scan(dict(_INPUTS), ctx)
    assert out == []                                   # single slot — no second open


def test_scan_holds_below_min_score():
    import scan
    ctx = _Ctx(_synth_candles(14, 1.0), held=False)
    inp = dict(_INPUTS); inp["minScore"] = 99          # unreachable floor
    out = scan.scan(inp, ctx)
    assert out == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} RAM TESTS PASS")
