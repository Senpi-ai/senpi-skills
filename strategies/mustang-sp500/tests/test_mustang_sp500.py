"""Mustang S&P: Mustang's rule on xyz:SP500, timed by the Nasdaq 100's trend, pinned.

Everything but the traded market must equal Mustang's — the regime market (xyz:XYZ100) and its
40-week average, the RSI lines, the leverage, the sizing, the stop and the ladder. The scanner reads
the regime off xyz:XYZ100 (daily + weekly) and the RSI off xyz:SP500 (daily), both on the xyz dex.

Run: python3 -B -m pytest strategies/mustang-sp500/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))
import scan  # noqa: E402

STRATS = os.path.join(HERE, "..", "..")
D1, W1 = 86400, 7 * 86400
MIDNIGHT = 1794441600


def _load(pkg):
    rt = yaml.safe_load(open(os.path.join(STRATS, pkg, "main", "runtime.yaml"), encoding="utf-8"))
    man = yaml.safe_load(open(os.path.join(STRATS, pkg, "strategy.yaml"), encoding="utf-8"))
    return rt, man, dict(next(s for s in rt["scanners"] if s["type"] == "external_scanner")["inputs"])


RT, MAN, INPUTS = _load("mustang-sp500")
PRT, PMAN, PINPUTS = _load("mustang")


def test_the_sp500_traded_the_nasdaq_trend_as_the_regime():
    assert INPUTS["allowedAssets"] == ["xyz:SP500"] == MAN["catalog"]["assets"]
    assert INPUTS["regimeAsset"] == "xyz:XYZ100"
    assert MAN["catalog"]["varies"] == "mustang"


def test_everything_else_is_mustangs():
    assert {k: v for k, v in INPUTS.items() if k != "allowedAssets"} == {k: v for k, v in PINPUTS.items() if k != "allowedAssets"}
    assert RT["exit"] == PRT["exit"]
    assert RT["strategy"]["slots"] == PRT["strategy"]["slots"] == 1
    assert RT["strategy"]["margin_pct"] == PRT["strategy"]["margin_pct"]
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert RT["exit"]["dsl_preset"][cut]["enabled"] is False


def _bars(closes, last_open, step):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * step) * 1000), "o": str(c), "h": str(c), "l": str(c),
             "c": str(c), "v": "1"} for i, c in enumerate(closes)]


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
    def __init__(self, mcp):
        self.senpi_mcp, self.state, self.dry_run, self.wallet = mcp, _State(), False, "0xabc"


class _MCP:
    def __init__(self):
        self.calls = []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": "1000", "assetPositions": []}
            return {"data": {"main": sec, "xyz": sec}}
        daily = [100.0 + (0.5 if i % 2 else -0.4) + 0.05 * i for i in range(90)]
        out = {"1d": _bars(daily, MIDNIGHT - D1, D1) + _bars([daily[-1]], MIDNIGHT, D1)}
        if "1w" in args["candle_intervals"]:
            out["1w"] = _bars([90.0] * 53, MIDNIGHT - 3 * D1 - W1, W1)
        return {"data": {"candles": out}}


def test_the_scanner_reads_the_regime_and_the_traded_market(monkeypatch):
    monkeypatch.setattr(scan.time, "time", lambda: MIDNIGHT + 300)
    mcp = _MCP()
    out = scan.scan(dict(INPUTS), _Ctx(mcp))
    assert [(s["asset"], s["direction"], s["leverage"]) for s in out] == [("xyz:SP500", "LONG", 3)]
    reads = [(a["asset"], tuple(a["candle_intervals"]), a["dex"]) for t, a in mcp.calls if t == "market_get_asset_data"]
    assert reads == [("xyz:XYZ100", ("1d", "1w"), "xyz"), ("xyz:SP500", ("1d",), "xyz")]


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026), US daily bars Mar 2010 - Nov 2026, SPY traded with QQQ's 40-week regime,
    Mustang's exits, 0.18% fees per round trip: 46 trades, 78% winners, profit factor 2.99 (2.78 on
    the bar extreme), blocks 2.73 / 5.98 / 2.81 / 2.37, worst drawdown 21% at 30% margin. 1,458
    neighbouring cells all above 1.0, the worst 1.96."""
    assert (INPUTS["regimeWeeks"], INPUTS["overheatRsi"], INPUTS["dipRsi"], INPUTS["stopRoePct"]) == (40, 79, 30, 30)
