"""Gannet: the daily dip cascade, pinned.

1. The runtime inputs ARE the strategy: the three markets in cascade order, RSI(10) under 30 on
   the daily close, one slot, 1x on the 3x chip fund and 3x on the indices, and the numbers the
   scanner and the DSL must agree on.
2. The DSL decides every exit: no clock cut is enabled, the ladder only ever locks a gain.
3. The RSI is Wilder's and reads the CLOSED daily bars only.
4. The scanner buys the FIRST washed-out market in order, once per daily close, only inside the
   hour after the close, never a market it already holds (under either spelling of the name), and
   reads HIP-3 markets on the xyz dex.
5. The measured parameters are the shipped ones; the docstring of
   test_the_measured_parameters_are_the_shipped_ones carries the replay summary.

Run: python3 -B -m pytest strategies/gannet/tests -q
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
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "gannet_main_signals")
INPUTS = dict(SIGNALS["inputs"])
DSL = RUNTIME["exit"]["dsl_preset"]
D1 = 86400
MIDNIGHT = 1794441600          # a UTC midnight (2026-11-12 00:00), so `now % 86400` is the time since the close


# ── 1. the inputs are the strategy ───────────────────────────────────────────────────────

def test_three_markets_in_cascade_order_one_slot():
    assert INPUTS["allowedAssets"] == ["xyz:SOXL", "xyz:XYZ100", "xyz:SP500"] == MANIFEST["catalog"]["assets"]
    assert all(a.startswith("xyz:") for a in INPUTS["allowedAssets"])      # HIP-3 names carry the prefix
    assert INPUTS["maxSlots"] == RUNTIME["strategy"]["slots"] == MANIFEST["catalog"]["max_slots"] == 1
    assert INPUTS["rsiPeriod"] == 10 and INPUTS["oversoldRsi"] == 30
    assert MANIFEST["catalog"]["direction"] == "long_only"


def test_leverage_per_market_and_the_sizing():
    lev = INPUTS["leverageByAsset"]
    assert lev == {"xyz:SOXL": 1, "xyz:XYZ100": 3, "xyz:SP500": 3}
    assert max(lev.values()) == INPUTS["leverageMax"] == MANIFEST["catalog"]["leverage_max"]
    # min_budget sizes the LOWEST leverage the recipe uses; default_leverage carries it
    assert RUNTIME["strategy"]["default_leverage"] == min(lev.values()) == INPUTS["leverageDefault"]
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == 30
    assert 1.5 <= INPUTS["marginPct"] and INPUTS["marginPct"] * INPUTS["maxSlots"] <= 100


def test_the_stop_the_scanner_reports_is_the_stop_the_dsl_enforces():
    assert INPUTS["stopRoePct"] == DSL["phase1"]["max_loss_pct"] == 21
    assert DSL["phase1"]["enabled"] is False


# ── 2. the DSL decides every exit ────────────────────────────────────────────────────────

def test_no_clock_closes_anything():
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert DSL[cut]["enabled"] is False, f"{cut} must stay off — the DSL decides every exit"


def test_the_ladder_only_ever_locks_a_gain():
    tiers = DSL["phase2"]["tiers"]
    triggers = [t["trigger_pct"] for t in tiers]
    locks = [t["lock_hw_pct"] for t in tiers]
    assert [(t["trigger_pct"], t["lock_hw_pct"]) for t in tiers] == [(6, 40), (12, 60), (18, 75), (30, 85)]
    assert triggers == sorted(triggers) and len(set(triggers)) == len(triggers)
    assert locks == sorted(locks) and all(0 < lk < 100 for lk in locks)
    # the first locked floor is a real profit on the index (3x): 6 ROE = 2% of price, 40% kept = 0.8% of price
    assert triggers[0] / 3 * locks[0] / 100 >= 0.8


def test_the_tick_budget_and_the_gates():
    assert SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]
    assert INPUTS["entryWindowSeconds"] > SIGNALS["interval_seconds"] * 3   # several ticks to act on a close
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_halt_pct"] == 25
    assert rails["drawdown_reset_on_day_rollover"] is False
    one_stop = DSL["phase1"]["max_loss_pct"] * INPUTS["marginPct"] / 100      # % of the account
    assert rails["daily_loss_limit_pct"] > one_stop                            # one stop never halts the day


# ── 3. the indicator ─────────────────────────────────────────────────────────────────────

def test_rsi_is_wilders():
    # textbook check: a strictly rising series reads 100, a strictly falling one 0
    assert scoring.rsi_wilder([float(i) for i in range(1, 30)], 10)[-1] == 100.0
    assert scoring.rsi_wilder([float(30 - i) for i in range(30)], 10)[-1] == 0.0
    # worked by hand, period 2: changes +1 -1 +1 +1. Seed ag = al = 0.5 -> 50; then Wilder smoothing
    # ag = (0.5 + 1)/2 = 0.75, al = 0.25 -> RS 3 -> 75; ag = 0.875, al = 0.125 -> RS 7 -> 87.5
    assert scoring.rsi_wilder([1.0, 2.0, 1.0, 2.0, 3.0], 2) == [None, None, 50.0, 75.0, 87.5]


def _bars(closes, last_open):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * D1) * 1000), "T": int((last_open - (n - 1 - i) * D1 + D1) * 1000) - 1,
             "s": "x", "i": "1d", "o": str(c), "h": str(c * 1.01), "l": str(c * 0.99), "c": str(c),
             "v": "1", "n": 1} for i, c in enumerate(closes)]


def _washed_out(n=90):
    """A calm drift up, then a hard eight-day fall: RSI(10) ends deep under 30."""
    up = [100.0 + 0.2 * i for i in range(n - 8)]
    return up + [up[-1] * (1 - 0.03 * (k + 1)) for k in range(8)]


def _calm(n=90):
    return [100.0 + (0.5 if i % 2 else -0.4) + 0.05 * i for i in range(n)]


def test_closed_candles_drop_the_bar_in_progress():
    rows = _bars(_calm(5), MIDNIGHT)          # the last bar OPENED at midnight: in progress
    closed = scoring.closed_candles(rows, D1, MIDNIGHT + 600)
    assert len(closed) == 4 and closed[-1]["t"] == (MIDNIGHT - D1) * 1000


def test_thesis_only_under_the_line():
    th = scoring.build_thesis("xyz:XYZ100", _bars(_washed_out(), MIDNIGHT - D1), INPUTS)
    assert th and th["direction"] == "LONG" and th["rsi"] < 30 and th["leverage"] == 3
    assert th["stop_price_pct"] == 7.0
    assert scoring.build_thesis("xyz:XYZ100", _bars(_calm(), MIDNIGHT - D1), INPUTS) is None
    soxl = scoring.build_thesis("xyz:SOXL", _bars(_washed_out(), MIDNIGHT - D1), INPUTS)
    assert soxl["leverage"] == 1 and soxl["stop_price_pct"] == 21.0
    assert scoring.leverage_for("SOXL", INPUTS) == 1                    # bare spelling still matches


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
        self.senpi_mcp, self.state, self.dry_run, self.wallet = mcp, state, dry_run, "0xabc"


class _MCP:
    """Daily candles per market; `held` goes in the xyz section, spelled however the caller says."""

    def __init__(self, series, held=(), withdrawable="1000"):
        self.series, self.held, self.withdrawable, self.calls = series, held, withdrawable, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            return {"success": True, "data": {
                "main": {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                         "withdrawable": self.withdrawable, "assetPositions": []},
                "xyz": {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                        "withdrawable": self.withdrawable,
                        "assetPositions": [{"position": {"coin": c, "szi": "1", "marginUsed": "300"}} for c in self.held]}}}
        if tool == "market_get_asset_data":
            closes = self.series.get(args["asset"], _calm())
            # the closed bars end yesterday; today's bar (opened at MIDNIGHT) is in progress
            rows = _bars(closes, MIDNIGHT - D1) + _bars([closes[-1]], MIDNIGHT)
            return {"success": True, "data": {"candles": {"1d": rows}}}
        raise AssertionError(f"unexpected tool {tool}")


def _run(monkeypatch, mcp, at=MIDNIGHT + 300, state=None, dry_run=False, inputs=None):
    monkeypatch.setattr(scan.time, "time", lambda: at)
    return scan.scan(dict(inputs or INPUTS), _Ctx(mcp, state if state is not None else _State(), dry_run))


def test_the_cascade_buys_the_first_washed_out_market(monkeypatch):
    mcp = _MCP({"xyz:XYZ100": _washed_out(), "xyz:SP500": _washed_out()})
    out = _run(monkeypatch, mcp)
    assert [s["asset"] for s in out] == ["xyz:XYZ100"]           # SOXL calm, so the Nasdaq is next in line
    s = out[0]
    assert s["direction"] == "LONG" and s["leverage"] == 3 and s["marginPct"] == 30
    assert set(s["data"]) <= set(SIGNALS["signal_data_schema"])
    mcp2 = _MCP({"xyz:SOXL": _washed_out(), "xyz:XYZ100": _washed_out()})
    out2 = _run(monkeypatch, mcp2)
    assert [(s["asset"], s["leverage"]) for s in out2] == [("xyz:SOXL", 1)]


def test_hip3_reads_carry_the_xyz_dex(monkeypatch):
    mcp = _MCP({})
    _run(monkeypatch, mcp)
    reads = [a for t, a in mcp.calls if t == "market_get_asset_data"]
    assert [a["asset"] for a in reads] == INPUTS["allowedAssets"]
    assert all(a["dex"] == "xyz" and a["candle_intervals"] == ["1d"] for a in reads)


def test_one_entry_per_market_per_close(monkeypatch):
    state = _State()
    mcp = _MCP({"xyz:SP500": _washed_out()})
    assert len(_run(monkeypatch, mcp, state=state)) == 1
    assert _run(monkeypatch, mcp, at=MIDNIGHT + 900, state=state) == []        # same close, already sent


def test_a_held_market_is_not_bought_again_under_either_spelling(monkeypatch):
    for spelling in ("xyz:SP500", "SP500"):
        mcp = _MCP({"xyz:SP500": _washed_out()}, held=(spelling,))
        assert _run(monkeypatch, mcp) == []                                   # one slot, and it is full
    duo = dict(INPUTS, maxSlots=2)
    mcp = _MCP({"xyz:SP500": _washed_out(), "xyz:XYZ100": _washed_out()}, held=("SP500",))
    assert [s["asset"] for s in _run(monkeypatch, mcp, inputs=duo)] == ["xyz:XYZ100"]


def test_outside_the_entry_window_it_reads_the_account_and_waits(monkeypatch):
    mcp = _MCP({"xyz:SOXL": _washed_out()})
    assert _run(monkeypatch, mcp, at=MIDNIGHT + 2 * 3600) == []
    assert [t for t, _ in mcp.calls] == ["strategy_get_clearinghouse_state"]
    # validation (dry run) still exercises the candle path at any hour
    mcp2 = _MCP({})
    _run(monkeypatch, mcp2, at=MIDNIGHT + 2 * 3600, dry_run=True)
    assert sum(1 for t, _ in mcp2.calls if t == "market_get_asset_data") == 3


def test_the_free_margin_gate(monkeypatch):
    mcp = _MCP({"xyz:SOXL": _washed_out()}, withdrawable="30")    # 30% of $30 at 1x = $9 < $12
    assert _run(monkeypatch, mcp) == []


def test_unreadable_payloads_emit_nothing(monkeypatch):
    class _Broken(_MCP):
        def call_tool(self, tool, args):
            if tool == "market_get_asset_data":
                return "upstream says no"
            return super().call_tool(tool, args)
    assert _run(monkeypatch, _Broken({})) == []


def test_a_market_the_cascade_cannot_read_stops_it_there(monkeypatch):
    """SOXL comes first. If it cannot be read, it may have qualified, so the Nasdaq behind it must not
    take the slot; nothing is marked sent, and the next tick resumes the cascade in order."""
    class _SoxlDown(_MCP):
        def call_tool(self, tool, args):
            if tool == "market_get_asset_data" and args["asset"] == "xyz:SOXL":
                raise RuntimeError("upstream timeout")
            return super().call_tool(tool, args)
    state = _State()
    assert _run(monkeypatch, _SoxlDown({"xyz:XYZ100": _washed_out()}), state=state) == []
    again = _run(monkeypatch, _MCP({"xyz:XYZ100": _washed_out()}), at=MIDNIGHT + 600, state=state)
    assert [s["asset"] for s in again] == ["xyz:XYZ100"]


def test_a_missing_close_earlier_in_the_order_stops_the_cascade(monkeypatch):
    class _SoxlStale(_MCP):
        def call_tool(self, tool, args):
            out = super().call_tool(tool, args)
            if tool == "market_get_asset_data" and args["asset"] == "xyz:SOXL":
                rows = out["data"]["candles"]["1d"]
                out["data"]["candles"]["1d"] = [r for r in rows if r["t"] != (MIDNIGHT - D1) * 1000]
            return out
    assert _run(monkeypatch, _SoxlStale({"xyz:SOXL": _washed_out(), "xyz:XYZ100": _washed_out()})) == []


def test_a_read_failure_behind_the_winner_does_not_block_it(monkeypatch):
    class _Sp500Down(_MCP):
        def call_tool(self, tool, args):
            if tool == "market_get_asset_data" and args["asset"] == "xyz:SP500":
                return {"success": False}
            return super().call_tool(tool, args)
    assert [s["asset"] for s in _run(monkeypatch, _Sp500Down({"xyz:XYZ100": _washed_out()}))] == ["xyz:XYZ100"]


def test_a_second_slot_is_projected_off_the_free_margin_left_by_the_first(monkeypatch):
    """The runtime opens a scanner's signals one at a time (intake dispatches each separately, one
    consumer), each as marginPct of the free margin left after the ones before it. At $85 free and
    15% a slot, the first is $12.75 and the second 15% of $72.25 = $10.84: under the $12 floor at 1x,
    so the scanner must not emit it."""
    duo_1x = dict(INPUTS, maxSlots=2, marginPct=15, leverageByAsset={"xyz:SOXL": 1, "xyz:XYZ100": 1, "xyz:SP500": 1})
    mcp = _MCP({"xyz:SOXL": _washed_out(), "xyz:XYZ100": _washed_out()}, withdrawable="85")
    assert [s["asset"] for s in _run(monkeypatch, mcp, inputs=duo_1x)] == ["xyz:SOXL"]
    mcp = _MCP({"xyz:SOXL": _washed_out(), "xyz:XYZ100": _washed_out()}, withdrawable="100")
    assert [s["asset"] for s in _run(monkeypatch, mcp, inputs=duo_1x)] == ["xyz:SOXL", "xyz:XYZ100"]   # $15, then $12.75


# ── 5. the measured parameters ───────────────────────────────────────────────────────────

def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026), entry at the signal close, the stop on the wick, rungs arming on the
    close (and on the bar extreme as the upper bound), each market charged its Hyperliquid taker fee
    both sides, its measured spread and funding at its 180-day average rate per hour held:

    US daily bars Mar 2010 - Nov 2026 (SOXL; QQQ for XYZ100; SPY for SP500), RSI(10) < 30, stop
    21 ROE, ladder 6/40 12/60 18/75 30/85: 91 trades, 79% winners, profit factor 2.24; 2010-14
    1.80, 2014-18 2.64, 2018-22 1.70, 2022-26 2.90. Every one of 324 neighbouring cells above 1.0,
    the worst 1.43 (1.32 with funding stressed to 11% a year). Hyperliquid's own candles (Oct 2025 -
    Nov 2026): 14 trades, profit factor 2.57. The short leg lost almost everywhere (median 0.65).
    """
    assert (INPUTS["rsiPeriod"], INPUTS["oversoldRsi"], INPUTS["stopRoePct"]) == (10, 30, 21)
    assert INPUTS["allowedAssets"][0] == "xyz:SOXL"
