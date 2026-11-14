"""Ocelot: tech vs the trend basket, long only, pinned.

1. The inputs ARE the strategy: the Nasdaq 100 decides and is bought when it leads; the chip fund is
   bought after a crash; the four-market basket is read only; RSI(10) lines 30 / 79; leverage per
   market; one slot; the stop the scanner and the DSL agree on.
2. The DSL decides every exit: no clock cut is enabled, the ladder only ever locks a gain.
3. decide(): crash first; tech only above the basket's average RSI and under the overheat line, and
   only with every basket reading present; otherwise cash. Nothing is ever shorted.
4. The scanner buys at the daily close only, once per close, never while holding.

Run: python3 -B -m pytest strategies/ocelot/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))
import scan  # noqa: E402
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "ocelot_main_signals")
INPUTS = dict(SIGNALS["inputs"])
DSL = RUNTIME["exit"]["dsl_preset"]
D1 = 86400
MIDNIGHT = 1794441600


def test_the_inputs_are_the_strategy():
    assert INPUTS["allowedAssets"] == ["xyz:XYZ100", "xyz:SOXL"] == MANIFEST["catalog"]["assets"]
    assert (INPUTS["techAsset"], INPUTS["crashAsset"]) == ("xyz:XYZ100", "xyz:SOXL")
    assert INPUTS["basketAssets"] == ["xyz:GOLD", "xyz:CL", "xyz:EUR", "xyz:JPY"]
    assert (INPUTS["rsiPeriod"], INPUTS["crashRsi"], INPUTS["overheatRsi"]) == (10, 30, 79)
    assert INPUTS["leverageByAsset"] == {"xyz:XYZ100": 3, "xyz:SOXL": 1}
    assert RUNTIME["strategy"]["default_leverage"] == 1 and INPUTS["leverageMax"] == MANIFEST["catalog"]["leverage_max"] == 3
    assert INPUTS["maxSlots"] == RUNTIME["strategy"]["slots"] == 1
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == 20
    assert MANIFEST["catalog"]["direction"] == "long_only"


def test_the_dsl_decides_every_exit():
    assert INPUTS["stopRoePct"] == DSL["phase1"]["max_loss_pct"] == 30 and DSL["phase1"]["enabled"] is False
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert DSL[cut]["enabled"] is False
    assert [(t["trigger_pct"], t["lock_hw_pct"]) for t in DSL["phase2"]["tiers"]] == [(15, 30), (25, 45), (40, 60), (60, 75), (100, 85)]
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["daily_loss_limit_pct"] > INPUTS["stopRoePct"] * INPUTS["marginPct"] / 100
    assert rails["drawdown_reset_on_day_rollover"] is False


def test_decide():
    assert scoring.decide(25.0, [], INPUTS) == ("crash", None)
    assert scoring.decide(60.0, [50.0, 40.0, 55.0, 45.0], INPUTS) == ("tech", 47.5)
    assert scoring.decide(45.0, [50.0, 40.0, 55.0, 45.0], INPUTS)[0] is None        # tech lags the basket
    assert scoring.decide(85.0, [20.0, 20.0, 20.0, 20.0], INPUTS)[0] is None        # overheated
    assert scoring.decide(60.0, [50.0, None, 55.0, 45.0], INPUTS)[0] is None        # a missing basket reading
    assert scoring.decide(None, [50.0] * 4, INPUTS)[0] is None


def _bars(closes, last_open):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * D1) * 1000), "o": str(c), "h": str(c), "l": str(c),
             "c": str(c), "v": "1"} for i, c in enumerate(closes)]


def _calm(n=90):
    return [100.0 + (0.5 if i % 2 else -0.4) + 0.05 * i for i in range(n)]


def _falling(n=90):
    return [100.0 - 0.2 * i + (0.3 if i % 2 else -0.3) for i in range(n)]


def _crash(n=90):
    up = [100.0 + 0.2 * i for i in range(n - 8)]
    return up + [up[-1] * (1 - 0.03 * (k + 1)) for k in range(8)]


class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)

    def __len__(self):
        return len(self._log)


class _Ctx:
    def __init__(self, mcp, state=None, dry_run=False):
        self.senpi_mcp, self.state, self.dry_run, self.wallet = mcp, (state if state is not None else _State()), dry_run, "0xabc"


class _MCP:
    def __init__(self, series, held=()):
        self.series, self.held, self.calls = series, held, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": "1000", "assetPositions": []}
            pos = [{"position": {"coin": c, "szi": "1", "marginUsed": "200"}} for c in self.held]
            return {"data": {"main": sec, "xyz": dict(sec, assetPositions=pos)}}
        closes = self.series.get(args["asset"], _falling())
        return {"data": {"candles": {"1d": _bars(closes, MIDNIGHT - D1) + _bars([closes[-1]], MIDNIGHT)}}}


def _run(monkeypatch, mcp, at=MIDNIGHT + 300, **kw):
    monkeypatch.setattr(scan.time, "time", lambda: at)
    return scan.scan(dict(INPUTS), _Ctx(mcp, **kw))


def test_tech_leads_buys_the_nasdaq_at_3x(monkeypatch):
    mcp = _MCP({"xyz:XYZ100": _calm()})                  # basket markets falling: their RSIs are low
    out = _run(monkeypatch, mcp)
    assert [(s["asset"], s["leverage"], s["marginPct"], s["data"]["leg"]) for s in out] == [("xyz:XYZ100", 3, 20, "tech")]
    assert set(out[0]["data"]) <= set(SIGNALS["signal_data_schema"])
    reads = [a for t, a in mcp.calls if t == "market_get_asset_data"]
    assert [a["asset"] for a in reads] == ["xyz:XYZ100"] + INPUTS["basketAssets"] and all(a["dex"] == "xyz" for a in reads)


def test_a_crash_buys_the_chip_fund_without_reading_the_basket(monkeypatch):
    mcp = _MCP({"xyz:XYZ100": _crash()})
    out = _run(monkeypatch, mcp)
    assert [(s["asset"], s["leverage"], s["data"]["leg"]) for s in out] == [("xyz:SOXL", 1, "crash")]
    assert [a["asset"] for t, a in mcp.calls if t == "market_get_asset_data"] == ["xyz:XYZ100"]


def test_tech_lagging_means_cash_and_one_entry_per_close(monkeypatch):
    assert _run(monkeypatch, _MCP({"xyz:XYZ100": _falling(), **{b: _calm() for b in INPUTS["basketAssets"]}})) == []
    state = _State()
    assert len(_run(monkeypatch, _MCP({"xyz:XYZ100": _calm()}), state=state)) == 1
    assert _run(monkeypatch, _MCP({"xyz:XYZ100": _calm()}), at=MIDNIGHT + 900, state=state) == []
    assert _run(monkeypatch, _MCP({"xyz:XYZ100": _calm()}, held=("XYZ100",))) == []
    mcp = _MCP({"xyz:XYZ100": _calm()})
    assert _run(monkeypatch, mcp, at=MIDNIGHT + 2 * 3600) == []
    assert [t for t, _ in mcp.calls] == ["strategy_get_clearinghouse_state"]



def _missing_newest_close(series_feed, assets):
    """The feed with the newest closed row removed for `assets`, as after a dropped row."""
    class _Stale(_MCP):
        def call_tool(self, tool, args):
            out = super().call_tool(tool, args)
            if tool == "market_get_asset_data" and args["asset"] in assets:
                rows = out["data"]["candles"]["1d"]
                out["data"]["candles"]["1d"] = [r for r in rows if r["t"] != (MIDNIGHT - D1) * 1000]
            return out
    return _Stale(series_feed)


def test_a_stale_basket_market_blocks_a_tech_entry(monkeypatch):
    """Review #976: only the Nasdaq's close was freshness-checked, so a basket market missing its
    newest close still produced an RSI and could authorize the 3x Nasdaq entry off yesterday's yardstick."""
    assert [s["asset"] for s in _run(monkeypatch, _MCP({"xyz:XYZ100": _calm()}))] == ["xyz:XYZ100"]   # all current: tech leads
    for coin in INPUTS["basketAssets"]:
        assert _run(monkeypatch, _missing_newest_close({"xyz:XYZ100": _calm()}, {coin})) == [], coin


def test_a_crash_still_buys_the_chip_fund_with_a_stale_basket(monkeypatch):
    out = _run(monkeypatch, _missing_newest_close({"xyz:XYZ100": _crash()}, set(INPUTS["basketAssets"])))
    assert [s["asset"] for s in out] == ["xyz:SOXL"]

def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026), US daily bars Jan 2010 - Nov 2026 (QQQ, SOXL; basket GLD, USO, FXE, 1/FXY),
    entry at the signal close, the stop on the wick, rungs arming on the close, each traded market
    charged its Hyperliquid taker fee both sides, its measured spread and funding at its 180-day
    average rate: long only, 81 trades, profit factor 2.15 (2.22 on the bar extreme); Nasdaq 2.24, chip
    fund 2.03; blocks 3.02 / 4.20 / 2.62 / 1.36; worst drawdown 31% at 20% margin. 648 neighbouring
    cells all above 1.0, worst 1.50 (1.21 stressed). The always-in switch: 492 of 648 above 1, worst 0.70."""
    assert (INPUTS["rsiPeriod"], INPUTS["crashRsi"], INPUTS["overheatRsi"], INPUTS["stopRoePct"]) == (10, 30, 79, 30)
