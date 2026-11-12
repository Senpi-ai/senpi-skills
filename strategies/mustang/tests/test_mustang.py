"""Mustang: the Nasdaq 100 trend rider, pinned.

1. The runtime inputs ARE the strategy: one market (xyz:XYZ100), its own 40-week average as the
   regime, RSI(10) with an overheat line in an uptrend and a washout line in a downtrend, 3x, one
   slot, and the numbers the scanner and the DSL must agree on.
2. The DSL decides every exit: no clock cut is enabled, the ladder only ever locks a gain.
3. The regime reads CLOSED weekly candles and refuses to guess without 40 of them.
4. The scanner buys at the daily close only, once per close, never while it holds the market,
   never shorts, and reads the HIP-3 market on the xyz dex.

Run: python3 -B -m pytest strategies/mustang/tests -q
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
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "mustang_main_signals")
INPUTS = dict(SIGNALS["inputs"])
DSL = RUNTIME["exit"]["dsl_preset"]
D1, W1 = 86400, 7 * 86400
MIDNIGHT = 1794441600          # 2026-11-12 00:00 UTC


# ── 1. the inputs are the strategy ───────────────────────────────────────────────────────

def test_one_market_its_own_regime_one_slot_long_only():
    assert INPUTS["allowedAssets"] == ["xyz:XYZ100"] == MANIFEST["catalog"]["assets"]
    assert INPUTS["regimeAsset"] == "xyz:XYZ100"
    assert INPUTS["regimeWeeks"] == 40
    assert (INPUTS["rsiPeriod"], INPUTS["overheatRsi"], INPUTS["dipRsi"]) == (10, 79, 30)
    assert INPUTS["maxSlots"] == RUNTIME["strategy"]["slots"] == MANIFEST["catalog"]["max_slots"] == 1
    assert MANIFEST["catalog"]["direction"] == "long_only"


def test_sizing_and_the_stop_agree():
    assert INPUTS["leverage"] == RUNTIME["strategy"]["default_leverage"] == MANIFEST["catalog"]["leverage_max"] == 3
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == 30
    assert INPUTS["stopRoePct"] == DSL["phase1"]["max_loss_pct"] == 30
    assert DSL["phase1"]["enabled"] is False


# ── 2. the DSL decides every exit ────────────────────────────────────────────────────────

def test_no_clock_closes_anything():
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert DSL[cut]["enabled"] is False, f"{cut} must stay off — the DSL decides every exit"


def test_the_ladder_only_ever_locks_a_gain():
    tiers = [(t["trigger_pct"], t["lock_hw_pct"]) for t in DSL["phase2"]["tiers"]]
    assert tiers == [(15, 30), (25, 45), (40, 60), (60, 75), (100, 85)]
    assert [t for t, _ in tiers] == sorted(t for t, _ in tiers)
    assert all(0 < lk < 100 for _, lk in tiers)
    assert tiers[0][0] / INPUTS["leverage"] * tiers[0][1] / 100 >= 1.5      # first floor: +1.5% of price, a real profit


def test_the_rails():
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["daily_loss_limit_pct"] > INPUTS["stopRoePct"] * INPUTS["marginPct"] / 100
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False
    assert SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]


# ── 3. the regime and the thesis ─────────────────────────────────────────────────────────

def _bars(closes, last_open, step):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * step) * 1000), "o": str(c), "h": str(c), "l": str(c),
             "c": str(c), "v": "1"} for i, c in enumerate(closes)]


def test_the_regime_needs_forty_closed_weeks():
    weekly = _bars([100.0] * 39, MIDNIGHT - W1, W1)
    daily = _bars([110.0], MIDNIGHT - D1, D1)
    assert scoring.regime(daily, weekly, INPUTS) is None
    weekly = _bars([100.0] * 40, MIDNIGHT - W1, W1)
    assert scoring.regime(daily, weekly, INPUTS)["up"] is True
    assert scoring.regime(_bars([100.0], MIDNIGHT - D1, D1), weekly, INPUTS)["up"] is False   # at the average = not up


def _calm(n=90):
    return [100.0 + (0.5 if i % 2 else -0.4) + 0.05 * i for i in range(n)]


def _hot(n=90):
    return [100.0 * 1.02 ** i for i in range(n)]


def _washed(n=90):
    up = [100.0 + 0.2 * i for i in range(n - 8)]
    return up + [up[-1] * (1 - 0.03 * (k + 1)) for k in range(8)]


def test_uptrend_buys_unless_overheated_downtrend_buys_only_a_washout():
    up = {"up": True, "close": 110.0, "average": 100.0}
    down = {"up": False, "close": 90.0, "average": 100.0}
    d = lambda closes: _bars(closes, MIDNIGHT - D1, D1)
    th = scoring.build_thesis("xyz:XYZ100", d(_calm()), up, INPUTS)
    assert th and th["leg"] == "trend" and th["direction"] == "LONG" and th["leverage"] == 3
    assert th["stop_price_pct"] == 10.0
    assert scoring.build_thesis("xyz:XYZ100", d(_hot()), up, INPUTS) is None          # RSI 100: overheated
    assert scoring.build_thesis("xyz:XYZ100", d(_calm()), down, INPUTS) is None       # downtrend, no washout
    wash = scoring.build_thesis("xyz:XYZ100", d(_washed()), down, INPUTS)
    assert wash and wash["leg"] == "washout" and wash["rsi"] < 30


# ── 4. the scanner ───────────────────────────────────────────────────────────────────────

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
    def __init__(self, daily, weekly_level=100.0, held=(), withdrawable="1000"):
        self.daily, self.weekly_level, self.held, self.withdrawable, self.calls = daily, weekly_level, held, withdrawable, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": self.withdrawable, "assetPositions": []}
            xyz = dict(sec, assetPositions=[{"position": {"coin": c, "szi": "1", "marginUsed": "300"}} for c in self.held])
            return {"data": {"main": sec, "xyz": xyz}}
        out = {}
        if "1d" in args["candle_intervals"]:
            out["1d"] = _bars(self.daily, MIDNIGHT - D1, D1) + _bars([self.daily[-1]], MIDNIGHT, D1)
        if "1w" in args["candle_intervals"]:
            out["1w"] = _bars([self.weekly_level] * 53, MIDNIGHT - 3 * D1 - W1, W1)   # HL weeks open on Thursday
        return {"success": True, "data": {"candles": out}}


def _run(monkeypatch, mcp, at=MIDNIGHT + 300, **kw):
    monkeypatch.setattr(scan.time, "time", lambda: at)
    return scan.scan(dict(INPUTS), _Ctx(mcp, **kw))


def test_an_uptrend_close_is_one_long_on_the_xyz_dex(monkeypatch):
    mcp = _MCP(_calm(), weekly_level=90.0)                 # daily ~104 > 40-week average 90: uptrend
    out = _run(monkeypatch, mcp)
    assert len(out) == 1
    s = out[0]
    assert (s["asset"], s["direction"], s["leverage"], s["marginPct"]) == ("xyz:XYZ100", "LONG", 3, 30)
    assert s["data"]["leg"] == "trend" and s["data"]["regimeUp"] is True
    assert set(s["data"]) <= set(SIGNALS["signal_data_schema"])
    reads = [a for t, a in mcp.calls if t == "market_get_asset_data"]
    assert len(reads) == 1 and reads[0]["dex"] == "xyz" and set(reads[0]["candle_intervals"]) == {"1d", "1w"}


def test_never_twice_on_one_close_never_while_holding(monkeypatch):
    state = _State()
    mcp = _MCP(_calm(), weekly_level=90.0)
    assert len(_run(monkeypatch, mcp, state=state)) == 1
    assert _run(monkeypatch, mcp, at=MIDNIGHT + 900, state=state) == []
    for spelling in ("xyz:XYZ100", "XYZ100"):
        assert _run(monkeypatch, _MCP(_calm(), weekly_level=90.0, held=(spelling,))) == []


def test_a_downtrend_waits_for_the_washout(monkeypatch):
    assert _run(monkeypatch, _MCP(_calm(), weekly_level=150.0)) == []
    out = _run(monkeypatch, _MCP(_washed(), weekly_level=150.0))
    assert [s["data"]["leg"] for s in out] == ["washout"]


def test_outside_the_entry_window_it_reads_the_account_and_waits(monkeypatch):
    mcp = _MCP(_calm(), weekly_level=90.0)
    assert _run(monkeypatch, mcp, at=MIDNIGHT + 2 * 3600) == []
    assert [t for t, _ in mcp.calls] == ["strategy_get_clearinghouse_state"]
    mcp2 = _MCP(_calm(), weekly_level=90.0)
    _run(monkeypatch, mcp2, at=MIDNIGHT + 2 * 3600, dry_run=True)          # validation still reads candles
    assert any(t == "market_get_asset_data" for t, _ in mcp2.calls)


def test_the_free_margin_gate_and_unreadable_payloads(monkeypatch):
    assert _run(monkeypatch, _MCP(_calm(), weekly_level=90.0, withdrawable="10")) == []   # 30% x 3x x $10 = $9

    class _Broken(_MCP):
        def call_tool(self, tool, args):
            return "no" if tool == "market_get_asset_data" else super().call_tool(tool, args)
    assert _run(monkeypatch, _Broken(_calm())) == []


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026), US daily bars Mar 2010 - Nov 2026 (QQQ for XYZ100, the regime from QQQ's
    40-week average as the scanner computes it), entry at the signal close, the stop on the wick,
    rungs arming on the close, charged xyz:XYZ100's Hyperliquid taker fee both sides, its measured
    spread and funding at its 180-day average rate: 61 trades, 74% winners, profit factor 2.18 (1.93 on
    the bar extreme), worst drawdown 24% at 30% margin. 1,458 neighbouring cells all above 1.0, the
    worst 1.48 (1.05 with funding stressed to 11% a year). The downtrend short lost at 12 of 15 cells."""
    assert (INPUTS["regimeWeeks"], INPUTS["rsiPeriod"], INPUTS["overheatRsi"], INPUTS["dipRsi"], INPUTS["stopRoePct"]) == (40, 10, 79, 30, 30)
