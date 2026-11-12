"""Kinkajou: leveraged big tech when the Nasdaq is healthy, pinned.

1. The inputs ARE the strategy: the Nasdaq 100 reads the market and is bought after a washout; five
   single names bought on momentum when it is healthy; RSI(10) lines 30 / 79; 40-week trend; 13-week
   momentum; leverage per market; one slot. No Bitcoin (it failed the replay gate).
2. The DSL decides every exit: no clock cut is enabled, the ladder only ever locks a gain.
3. nasdaq_state(): washout first; healthy only above the 40-week average and under 79; else risk.
4. The scanner buys at the daily close only, reads the names only on a healthy close, once per close.

Run: python3 -B -m pytest strategies/kinkajou/tests -q
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
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "kinkajou_main_signals")
INPUTS = dict(SIGNALS["inputs"])
DSL = RUNTIME["exit"]["dsl_preset"]
D1, W1 = 86400, 7 * 86400
MIDNIGHT = 1794441600
NAMES = ["xyz:AAPL", "xyz:NVDA", "xyz:TSLA", "xyz:META", "xyz:AMZN"]


def test_the_inputs_are_the_strategy():
    assert INPUTS["indexAsset"] == "xyz:XYZ100" and INPUTS["singleNames"] == NAMES
    assert INPUTS["allowedAssets"] == ["xyz:XYZ100"] + NAMES == MANIFEST["catalog"]["assets"]
    assert "BTC" not in INPUTS["allowedAssets"]
    assert (INPUTS["rsiPeriod"], INPUTS["washoutRsi"], INPUTS["overheatRsi"]) == (10, 30, 79)
    assert (INPUTS["trendWeeks"], INPUTS["momentumWeeks"]) == (40, 13)
    assert INPUTS["leverageByAsset"] == {"xyz:XYZ100": 3, "xyz:AAPL": 2, "xyz:NVDA": 2, "xyz:TSLA": 1, "xyz:META": 2, "xyz:AMZN": 2}
    assert RUNTIME["strategy"]["default_leverage"] == 1 and INPUTS["leverageMax"] == MANIFEST["catalog"]["leverage_max"] == 3
    assert INPUTS["maxSlots"] == RUNTIME["strategy"]["slots"] == 1 and INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == 20


def test_the_dsl_decides_every_exit():
    assert INPUTS["stopRoePct"] == DSL["phase1"]["max_loss_pct"] == 30 and DSL["phase1"]["enabled"] is False
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert DSL[cut]["enabled"] is False
    assert [(t["trigger_pct"], t["lock_hw_pct"]) for t in DSL["phase2"]["tiers"]] == [(15, 30), (25, 45), (40, 60), (60, 75), (100, 85)]
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["daily_loss_limit_pct"] > INPUTS["stopRoePct"] * INPUTS["marginPct"] / 100
    assert rails["drawdown_reset_on_day_rollover"] is False


def _bars(closes, last_open, step):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * step) * 1000), "o": str(c), "h": str(c), "l": str(c),
             "c": str(c), "v": "1"} for i, c in enumerate(closes)]


CALM = [100.0 + (0.5 if i % 2 else -0.4) + 0.05 * i for i in range(90)]
CRASH = [100.0 + 0.2 * i for i in range(82)] + [116.2 * (1 - 0.03 * (k + 1)) for k in range(8)]
HOT = [100.0 * 1.02 ** i for i in range(90)]


def test_nasdaq_state():
    wk_low = _bars([90.0] * 40, MIDNIGHT - W1, W1)
    wk_high = _bars([200.0] * 40, MIDNIGHT - W1, W1)
    d = lambda c: _bars(c, MIDNIGHT - D1, D1)
    assert scoring.nasdaq_state(d(CRASH), wk_high, INPUTS)[0] == "washout"
    assert scoring.nasdaq_state(d(CALM), wk_low, INPUTS)[0] == "healthy"
    assert scoring.nasdaq_state(d(CALM), wk_high, INPUTS)[0] == "risk"          # under its trend
    assert scoring.nasdaq_state(d(HOT), _bars([10.0] * 40, MIDNIGHT - W1, W1), INPUTS)[0] == "risk"   # overheated
    assert scoring.nasdaq_state(d(CALM), _bars([90.0] * 39, MIDNIGHT - W1, W1), INPUTS)[0] is None


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
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.state, self.dry_run, self.wallet = mcp, (state if state is not None else _State()), False, "0xabc"


class _MCP:
    """index daily series + weekly level; each name: (latest close) against a rising weekly series."""

    def __init__(self, index_daily, index_week_level, name_last):
        self.index_daily, self.level, self.name_last, self.calls = index_daily, index_week_level, name_last, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": "1000", "assetPositions": []}
            return {"data": {"main": sec, "xyz": sec}}
        a = args["asset"]
        if a == "xyz:XYZ100":
            daily, weekly = self.index_daily, [self.level] * 53
        else:
            daily, weekly = [self.name_last.get(a, 90.0)] * 90, [100.0 + i for i in range(53)]
        return {"data": {"candles": {"1d": _bars(daily, MIDNIGHT - D1, D1) + _bars([daily[-1]], MIDNIGHT, D1),
                                     "1w": _bars(weekly, MIDNIGHT - 3 * D1 - W1, W1)}}}


def _run(monkeypatch, mcp, state=None):
    monkeypatch.setattr(scan.time, "time", lambda: MIDNIGHT + 300)
    return scan.scan(dict(INPUTS), _Ctx(mcp, state))


def test_healthy_buys_the_strongest_name(monkeypatch):
    mcp = _MCP(CALM, 90.0, {"xyz:META": 170.0, "xyz:TSLA": 200.0})
    out = _run(monkeypatch, mcp)
    assert [(s["asset"], s["leverage"], s["marginPct"], s["data"]["state"]) for s in out] == [("xyz:TSLA", 1, 20, "healthy")]
    assert set(out[0]["data"]) <= set(SIGNALS["signal_data_schema"])


def test_washout_buys_the_index_without_reading_the_names(monkeypatch):
    mcp = _MCP(CRASH, 200.0, {})
    out = _run(monkeypatch, mcp)
    assert [(s["asset"], s["leverage"]) for s in out] == [("xyz:XYZ100", 3)]
    assert [a["asset"] for t, a in mcp.calls if t == "market_get_asset_data"] == ["xyz:XYZ100"]


def test_risk_means_cash_and_one_entry_per_close(monkeypatch):
    assert _run(monkeypatch, _MCP(CALM, 200.0, {"xyz:META": 170.0})) == []
    state = _State()
    assert len(_run(monkeypatch, _MCP(CALM, 90.0, {"xyz:META": 170.0}), state)) == 1
    assert _run(monkeypatch, _MCP(CALM, 90.0, {"xyz:META": 170.0}), state) == []


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026), US daily bars Jan 2010 - Nov 2026 (QQQ, AAPL, NVDA, TSLA, META, AMZN),
    entry at the signal close, the stop on the wick, rungs arming on the close, each market charged its
    Hyperliquid taker fee both sides plus its measured spread: 84 trades, 79% winners, profit factor
    2.56, blocks 2.93 / 5.60 / 3.31 / 1.49, worst drawdown 27% at 20% margin; 1,458 neighbouring cells
    all above 1.0, worst 1.73 (Jun 2021 - Nov 2026 alone: all above 1.0, worst 1.00). Legs uneven
    (AAPL 0.55, NVDA 0.79); with Bitcoin: 7 of 1,458 lose, worst 0.83 (left out)."""
    assert (INPUTS["washoutRsi"], INPUTS["overheatRsi"], INPUTS["stopRoePct"]) == (30, 79, 30)
