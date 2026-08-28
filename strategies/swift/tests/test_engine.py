"""Swift engine tests. Pure math + a scan() smoke run against a fake ctx (no network).
Run: python3 strategies/swift/tests/test_engine.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402


def _tape(n=40, spread=1.0, close=100.0, vol=1000.0):
    """n flat-ish candles with a given high/low spread — the volatility dial."""
    return [{"h": close + spread, "l": close - spread, "c": close, "v": vol} for _ in range(n)]


def _trend(n=40, step=0.25, spread=0.8, start=100.0, vol=1000.0):
    """A realistic entry, not a cartoon one. A straight line pins RSI at 100 and is
    correctly refused as late, and a flat volume profile fails the participation guard —
    so the fixture breathes (pullbacks) and puts a volume surge on the trigger candle."""
    out, c = [], start
    for i in range(n):
        c += step * (-0.9 if i % 3 == 2 else 1.0)      # two up, one back
        out.append({"h": c + spread, "l": c - spread, "c": c, "v": vol})
    out[-1]["v"] = vol * 2.0                            # participation behind the trigger
    return out


# ── THE FEE GATE — the reason this template exists ──

def test_fee_gate_rejects_a_market_too_quiet_to_pay_for_itself():
    inp = {"takerFeeBps": 4.5, "makerFeeBps": 1.5, "feeCoverMultiple": 3.0,
           "atrCaptureFraction": 0.5}
    rt = scoring.roundtrip_fee_pct(inp)
    assert abs(rt - 0.06) < 1e-9, rt                     # (1.5 + 4.5) bps = 0.06% of price
    # a market whose whole range is thinner than the fees can never be worth trading
    dead, detail = scoring.clears_fees(_tape(spread=0.005), inp)
    assert dead is False, detail
    # a genuinely moving market clears it
    live, detail2 = scoring.clears_fees(_tape(spread=1.0), inp)
    assert live is True, detail2
    assert detail2["expected_capture_pct"] > detail2["required_pct"]


def test_fee_gate_scales_with_the_configured_cost():
    """Fees are inputs, not constants — a worse schedule must reject more setups."""
    tape = _tape(spread=0.08)
    cheap = {"takerFeeBps": 1.0, "makerFeeBps": 0.5, "feeCoverMultiple": 3.0}
    dear = {"takerFeeBps": 20.0, "makerFeeBps": 10.0, "feeCoverMultiple": 3.0}
    assert scoring.clears_fees(tape, cheap)[0] is True
    assert scoring.clears_fees(tape, dear)[0] is False


def test_thesis_is_gated_on_fees_before_anything_else():
    """A textbook-perfect momentum setup on a dead tape must still be refused."""
    fast, slow = _trend(step=0.0008, spread=0.0004), _trend(step=0.0008, spread=0.0004)
    assert scoring.build_thesis("BTC", fast, slow, {}) is None


def test_thesis_requires_both_timeframes_to_agree():
    up_fast, down_slow = _trend(step=0.3), _trend(step=-0.3, start=140.0)
    assert scoring.build_thesis("BTC", up_fast, down_slow, {}) is None      # 5m up, 15m down
    th = scoring.build_thesis("BTC", _trend(step=0.3), _trend(step=0.3), {})
    assert th and th["direction"] == "LONG"
    dn = scoring.build_thesis("BTC", _trend(step=-0.3, start=140), _trend(step=-0.3, start=140), {})
    assert dn and dn["direction"] == "SHORT"


def test_thesis_refuses_an_extended_entry_and_a_no_volume_move():
    hot = _trend(step=1.2, spread=0.5)                    # straight up -> RSI pinned
    assert scoring.build_thesis("BTC", hot, hot, {"rsiMaxLong": 60}) is None
    quiet_vol = _trend(step=0.3)
    for c in quiet_vol[-1:]:
        c["v"] = 1.0                                      # last candle has no participation
    assert scoring.build_thesis("BTC", quiet_vol, _trend(step=0.3), {"minVolSurge": 5.0}) is None


def test_sizing_is_a_percent_and_clamped():
    inp = {"leverageTiers": {"apex": 4, "good": 3, "base": 2},
           "marginPctTiers": {"apex": 8, "good": 6, "base": 4},
           "maxLeverage": 4, "maxMarginPct": 20}
    lev, mgn = scoring.sizing_for("apex", inp)
    assert (lev, mgn) == (4, 8.0)
    assert 0 < mgn <= 100, "marginPct is a PERCENT in (0,100], never a fraction"
    assert scoring.sizing_for("apex", inp, venue_max=2)[0] == 2          # venue cap wins
    over = scoring.sizing_for("apex", {"leverageTiers": {"apex": 99}, "marginPctTiers": {"apex": 999},
                                       "maxLeverage": 4, "maxMarginPct": 20})
    assert over == (4, 20.0)                                             # clamps, never overshoots


# ── scan() smoke against a fake ctx ──

class _State:
    def __init__(self): self._log = []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, spread=1.0): self.spread = spread
    def call_tool(self, tool, args):
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"assetPositions": []}}
        if tool == "market_list_instruments":
            return {"instruments": [
                {"name": "BTC", "context": {"dayNtlVlm": 9e8, "max_leverage": 20}},
                {"name": "ETH", "context": {"dayNtlVlm": 5e8, "max_leverage": 20}},
                {"name": "DUST", "context": {"dayNtlVlm": 1e5, "max_leverage": 5}},
            ]}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"5m": _trend(step=0.3, spread=self.spread),
                                         "15m": _trend(step=0.3, spread=self.spread)}}}
        return {}


class _Ctx:
    def __init__(self, mcp): self.senpi_mcp, self.wallet, self.state = mcp, "0xswift", _State()


_INPUTS = {"volFloorUsd": 5e7, "maxUniverse": 20, "maxSlots": 4, "recentSignalTtlSeconds": 1800,
           "minScore": 0, "leverageTiers": {"apex": 4, "good": 3, "base": 2},
           "marginPctTiers": {"apex": 8, "good": 6, "base": 4},
           "maxLeverage": 4, "maxMarginPct": 20}


def test_scan_emits_valid_signals_and_skips_illiquid_names():
    ctx = _Ctx(_MCP())
    out = scan.scan(dict(_INPUTS), ctx)
    assert out, "a trending, liquid, fee-clearing tape should produce entries"
    assert {"DUST"}.isdisjoint({s["asset"] for s in out}), "sub-floor liquidity must be skipped"
    for s in out:
        assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
        assert s["direction"] in ("LONG", "SHORT")
        assert 0 < s["marginPct"] <= 20 and 1 <= s["leverage"] <= 4
        assert s["data"]["expectedCapturePct"] > s["data"]["requiredPct"]
    assert ctx.state.last() is not None


def test_scan_opens_nothing_when_the_tape_cannot_cover_fees():
    """The headline guarantee: a quiet market produces NO trades, not cheap ones."""
    ctx = _Ctx(_MCP(spread=0.0005))
    assert scan.scan(dict(_INPUTS), ctx) == []
    assert ctx.state.last()["result"]["priced_out"] > 0


def test_scan_degrades_when_clearinghouse_unreadable():
    class _Broken:
        def call_tool(self, tool, args):
            if tool == "strategy_get_clearinghouse_state":
                raise RuntimeError("boom")
            return {}
    assert scan.scan(dict(_INPUTS), _Ctx(_Broken())) == []      # no crash, no blind opens


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} SWIFT TESTS PASS")
