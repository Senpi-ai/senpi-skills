"""Ibex: the gold breakout, pinned where it differs from the engine's defaults.

1. The runtime inputs ARE the strategy: one market (xyz:GOLD), the Donchian trigger by default,
   a 55-bar channel, one slot, and the numbers the scanner and the DSL must agree on.
2. A held gold position is recognised whether the clearinghouse spells it `xyz:GOLD` or `GOLD`,
   and the candle read carries the HIP-3 dex — a miss on either is a double open on one market.
3. On gold's usual 4h volatility the ATR rule sizes at the 5x cap, and the stop that implies
   (2% of price) sits well outside gold's noise; a volatility spike drops to the floor.
4. The choice of channel length and filter is the measured one, not a round number: the
   docstring of test_the_measured_parameters_are_the_shipped_ones carries the replay summary.

Run: python3 -B -m pytest strategies/ibex/tests -q
"""
import os
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "ibex_main_signals")
INPUTS = dict(SIGNALS["inputs"])
H4 = 14400
D1 = 86400


def _bars(closes, step_seconds, end_open_seconds, spread=1.0):
    """Closed bars whose LAST bar opened at `end_open_seconds`, in the live shape (string o/h/l/c/v,
    int t in epoch ms)."""
    n = len(closes)
    out = []
    for i, c in enumerate(closes):
        t = int((end_open_seconds - (n - 1 - i) * step_seconds) * 1000)
        out.append({"t": t, "T": t + step_seconds * 1000 - 1, "s": "xyz:GOLD", "i": "x",
                    "o": str(c), "c": str(c), "h": str(c + spread), "l": str(c - spread),
                    "v": "100.0", "n": 10})
    return out


# ── 1. the inputs are the strategy ───────────────────────────────────────────────────────

def test_one_market_one_slot_breakout_by_default():
    assert INPUTS["allowedAssets"] == ["xyz:GOLD"] == MANIFEST["catalog"]["assets"]
    assert INPUTS["entryDetector"] == "donchian"
    assert INPUTS["donchianLookback"] == 55
    assert INPUTS["maxSlots"] == RUNTIME["strategy"]["slots"] == MANIFEST["catalog"]["max_slots"] == 1
    assert INPUTS["regimeSmaPeriod"] == 50


def test_the_stop_the_scanner_sizes_against_is_the_stop_the_dsl_enforces():
    pre = RUNTIME["exit"]["dsl_preset"]
    assert INPUTS["stopRoePct"] == pre["phase1"]["max_loss_pct"]
    assert pre["phase1"]["enabled"] is False
    assert INPUTS["leverageMax"] == MANIFEST["catalog"]["leverage_max"] == RUNTIME["strategy"]["default_leverage"]
    assert 1 <= INPUTS["leverageMin"] <= INPUTS["leverageMax"]
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] and INPUTS["marginPct"] * INPUTS["maxSlots"] <= 100


def test_the_ladder_only_ever_locks_a_gain_and_no_clock_cuts():
    pre = RUNTIME["exit"]["dsl_preset"]
    tiers = pre["phase2"]["tiers"]
    triggers = [t["trigger_pct"] for t in tiers]
    locks = [t["lock_hw_pct"] for t in tiers]
    assert triggers == sorted(triggers) and len(set(triggers)) == len(triggers)
    assert locks == sorted(locks) and all(0 < lk < 100 for lk in locks)
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False
    assert pre["phase1"]["max_loss_pct"] / INPUTS["leverageMin"] >= 3.0
    assert pre["phase1"]["max_loss_pct"] / INPUTS["leverageMax"] > 1.0


def test_the_tick_budget_and_the_gates():
    assert SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_halt_pct"] == 25                           # the wallet-wide hard stop
    assert rails["drawdown_reset_on_day_rollover"] is False


# ── 2. one market, recognised under either spelling, read on the right dex ───────────────

class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)

    def __len__(self):
        return len(self._log)


class _MCP:
    """A fresh 55-bar breakout on gold, above its 50-day SMA. `held` goes in the xyz section of the
    clearinghouse, spelled however the caller says."""

    def __init__(self, now, held=(), spread=0.3, withdrawable="1000"):
        self.now, self.held, self.spread, self.withdrawable, self.calls = now, held, spread, withdrawable, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            return {"success": True, "data": {
                "main": {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                         "withdrawable": self.withdrawable, "assetPositions": []},
                "xyz": {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                        "withdrawable": self.withdrawable,
                        "assetPositions": [{"position": {"coin": c, "szi": "0.5", "marginUsed": "400"}} for c in self.held]}}}
        if tool == "market_get_asset_data":
            last_open = self.now - H4 - 100
            closes = [4000.0] * 100 + [4000.0 + 2 * self.spread + 1.0]    # closes above the 55-bar high
            bars = _bars(closes, H4, last_open, spread=self.spread)
            live = _bars([3900.0], H4, last_open + H4, spread=self.spread)   # in progress: would flip everything
            daily = _bars([3800.0] * 60, D1, self.now - D1 - 100)
            return {"success": True, "data": {"asset": args["asset"], "candles": {"4h": bars + live, "1d": daily}}}
        return {}


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.state, self.dry_run = mcp, "0xibex", state, False
        self.scanner_name, self.interval_seconds = "ibex_main_signals", 300


def test_a_fresh_gold_break_is_emitted_once_with_the_xyz_dex():
    now = time.time()
    mcp, st = _MCP(now), _State()
    out = scan.scan(dict(INPUTS), _Ctx(mcp, st))
    assert [(s["asset"], s["direction"]) for s in out] == [("xyz:GOLD", "LONG")]
    assert [a["dex"] for t, a in mcp.calls if t == "market_get_asset_data"] == ["xyz"]
    s = out[0]
    assert s["marginPct"] == INPUTS["marginPct"] and s["data"]["detector"] == "donchian"
    assert s["data"]["channelHigh"] == 4000.0 + 0.3 and s["data"]["barsSinceSignal"] == 0
    assert scan.scan(dict(INPUTS), _Ctx(_MCP(now), st)) == []          # the same break, next tick


def test_a_held_gold_position_blocks_a_second_open_under_either_spelling():
    now = time.time()
    for spelling in ("xyz:GOLD", "GOLD"):
        assert scan.scan(dict(INPUTS), _Ctx(_MCP(now, held=(spelling,)), _State())) == [], spelling


def test_a_break_against_the_daily_trend_is_not_taken():
    now = time.time()

    class _Bear(_MCP):
        def call_tool(self, tool, args):
            r = super().call_tool(tool, args)
            if tool == "market_get_asset_data":
                r["data"]["candles"]["1d"] = _bars([4500.0] * 60, D1, self.now - D1 - 100)   # SMA50 above price
            return r

    assert scan.scan(dict(INPUTS), _Ctx(_Bear(now), _State())) == []


# ── 3. gold's volatility sizes at the cap; a spike drops to the floor ────────────────────

def test_golds_usual_volatility_sizes_at_the_cap_and_a_spike_at_the_floor():
    # 30-day median 4h ATR of xyz:GOLD measured 2026-10-12: 0.57% of price (p90 0.72%)
    assert scoring.leverage_for(0.57, INPUTS["stopRoePct"], INPUTS["atrStopMultiple"],
                                INPUTS["leverageMin"], INPUTS["leverageMax"]) == 5
    assert scoring.leverage_for(0.72, 15, 2.0, 3, 5) == 5
    assert INPUTS["stopRoePct"] / 5 >= 2.0 * 0.72                       # the 3% stop clears 2 ATRs even at p90
    assert scoring.leverage_for(3.0, 15, 2.0, 3, 5) == 3                 # a 3% ATR spike: the floor
    out = scan.scan(dict(INPUTS), _Ctx(_MCP(time.time(), spread=0.3), _State()))
    assert out and out[0]["leverage"] == 5 and abs(out[0]["data"]["stopPricePct"] - 3.0) < 1e-3


# ── 4. the shipped numbers are the measured ones ─────────────────────────────────────────

def test_the_measured_parameters_are_the_shipped_ones():
    """Replay, 2026-10-12, on 1,766 closed 4h xyz:GOLD candles from the exchange's public candle
    endpoint (2025-12-22 to 2026-10-12), DSL-style exits (this ladder, this stop, ATR leverage
    3-5x), 0.18% round-trip fees, one position at a time:

        channel 55 bars + daily SMA50:  12 trades, 7 winners, profit factor 2.02, +16.2% of price
        channel 20 / 30 / 40 / 70 / 100 with SMA50: profit factor 1.50 / 1.68 / 1.79 / 1.95 / 2.93
        no daily filter (channel 55): 19 trades, profit factor 1.68
        both sides positive (LONG 1.21, SHORT 3.24); both halves of the window positive (2.58, 1.65)
        EMA 20/50 cross + SMA50 (the alternate detector): 8 trades, profit factor 1.31
        15m / 30m / 1h / 4h Donchian-10 fades, any ladder: profit factor 0.50-1.00, all negative
        prior-UTC-day breakout on 1h, filtered or not: profit factor 0.42-0.69

    Twelve trades is a small sample; what the grid shows is a family that clears fees at every
    slow setting and a family that does not at any fast one. Change these numbers with a new
    replay, not by hand."""
    assert INPUTS["donchianLookback"] == 55
    assert INPUTS["regimeSmaPeriod"] == 50
    assert INPUTS["entryDetector"] == "donchian"
    assert INPUTS["stopRoePct"] == 15 and (INPUTS["leverageMin"], INPUTS["leverageMax"]) == (3, 5)
    tiers = RUNTIME["exit"]["dsl_preset"]["phase2"]["tiers"]
    assert [t["trigger_pct"] for t in tiers] == [12, 20, 30, 45, 75]
    assert [t["lock_hw_pct"] for t in tiers] == [40, 45, 60, 70, 85]
