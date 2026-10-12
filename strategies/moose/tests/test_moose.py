"""Moose: the textbook crossover, pinned where a textbook build goes wrong.

1. Signals come off CLOSED candles only. The feed's last row is the bar in progress; an EMA read
   off it changes every tick, so a cross could appear and vanish inside one bar.
2. A cross is fresh for `maxBarsSinceSignal` bars and then it is history, not a signal.
3. The daily trend filter is a hard gate: no long below the 50-day SMA, no short above it.
4. Leverage comes from the coin's ATR so the fixed ROE stop sits >= `atrStopMultiple` ATRs from
   entry in price — calm coin at the cap, wild coin at the floor, and the stop distance is on the
   signal either way.
5. One cross is one signal: the (coin, side, bar) key is remembered across ticks.
6. The runtime config and the scanner agree on the numbers that must not drift apart.

Run: python3 -B -m pytest strategies/moose/tests -q
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
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "moose_main_signals")
INPUTS = dict(SIGNALS["inputs"])
H4 = 14400
D1 = 86400


# ── candle builders: the live shape (string o/h/l/c/v, int t in epoch ms), oldest first ──

def _bars(closes, step_seconds, end_open_seconds, spread=1.0):
    """Closed bars whose LAST bar opened at `end_open_seconds`; each bar's range is +-spread."""
    n = len(closes)
    out = []
    for i, c in enumerate(closes):
        t = int((end_open_seconds - (n - 1 - i) * step_seconds) * 1000)
        out.append({"t": t, "T": t + step_seconds * 1000 - 1, "s": "X", "i": "x",
                    "o": str(c), "c": str(c), "h": str(c + spread), "l": str(c - spread),
                    "v": "100.0", "n": 10})
    return out


def _flat_then_up(flat=100, up=1, start=100.0, step=1.0):
    """`flat` bars at `start`, then `up` rising bars — the EMA20/50 cross is on the FIRST rising bar."""
    return [start] * flat + [start + step * (i + 1) for i in range(up)]


def _flat_then_down(flat=100, down=1, start=100.0, step=1.0):
    return [start] * flat + [start - step * (i + 1) for i in range(down)]


# ── 1. closed candles only ───────────────────────────────────────────────────────────────

def test_the_bar_in_progress_is_dropped_and_order_is_by_time():
    now = 1_800_000_000.0
    bars = _bars([1, 2, 3], H4, now - 100)            # last bar opened 100s ago: still open
    closed = scoring.closed_candles(list(reversed(bars)), H4, now)
    assert [c["c"] for c in closed] == ["1", "2"]
    assert scoring.closed_candles([{"o": "1"}, {"t": "abc"}], H4, now) == []   # no readable t


def test_a_real_hyperliquid_candle_parses():
    """Captured from market_get_asset_data(BTC, 4h) on 2026-10-12, not hand-written."""
    real = {"t": 1791835200000, "T": 1791849599999, "s": "BTC", "i": "4h", "o": "82834.0",
            "c": "83046.0", "h": "83075.0", "l": "82833.0", "v": "601.89463", "n": 5798}
    assert scoring._close(real) == 83046.0 and scoring._high(real) == 83075.0 and scoring._low(real) == 82833.0
    assert scoring.closed_candles([real], H4, 1791835200 + H4 + 1) == [real]
    assert scoring.closed_candles([real], H4, 1791835200 + H4 - 1) == []


# ── 2. the cross, its freshness, and the alternate trigger ───────────────────────────────

def test_ema_matches_the_textbook_recursion():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    e = scoring.ema(vals, 3)
    assert e[0] == 2.0                                        # SMA seed of the first 3
    assert abs(e[1] - (4.0 * 0.5 + 2.0 * 0.5)) < 1e-12       # k = 2/(3+1)
    assert scoring.ema(vals, 6) == []


def test_a_bull_cross_on_the_last_closed_bar_is_fresh():
    sig = scoring.ema_cross(_flat_then_up(up=1), 20, 50, 2)
    assert sig["direction"] == "LONG" and sig["bars_ago"] == 0
    assert sig["ema_fast"] > sig["ema_slow"] and sig["spread_pct"] > 0


def test_a_cross_two_bars_old_is_still_fresh_and_three_is_not():
    assert scoring.ema_cross(_flat_then_up(up=3), 20, 50, 2)["bars_ago"] == 2
    assert scoring.ema_cross(_flat_then_up(up=4), 20, 50, 2) is None
    assert scoring.ema_cross(_flat_then_up(up=4), 20, 50, 3)["bars_ago"] == 3


def test_a_bear_cross_and_a_flat_series():
    assert scoring.ema_cross(_flat_then_down(down=1), 20, 50, 2)["direction"] == "SHORT"
    assert scoring.ema_cross([100.0] * 120, 20, 50, 2) is None             # EMAs equal: no cross
    assert scoring.ema_cross([100.0] * 30, 20, 50, 2) is None              # too short to seed the slow EMA


def test_donchian_breakout_is_fresh_only_at_the_start_of_the_run():
    base = _bars([100.0] * 60, H4, 1_800_000_000, spread=1.0)             # channel 99..101
    out = base + _bars([103.0], H4, 1_800_000_000 + H4, spread=1.0)        # closes above 101
    sig = scoring.donchian_breakout(out, 20, 2)
    assert sig["direction"] == "LONG" and sig["bars_ago"] == 0 and sig["channel_high"] == 101.0
    assert abs(sig["breakout_pct"] - (103.0 - 101.0) / 103.0 * 100) < 1e-9
    run3 = out + _bars([105.0, 107.0], H4, 1_800_000_000 + 3 * H4, spread=1.0)
    assert scoring.donchian_breakout(run3, 20, 2)["bars_ago"] == 2
    run4 = run3 + _bars([109.0], H4, 1_800_000_000 + 4 * H4, spread=1.0)
    assert scoring.donchian_breakout(run4, 20, 2) is None                 # chasing a 4-bar run: no
    back_inside = out + _bars([100.5], H4, 1_800_000_000 + 2 * H4, spread=1.0)
    assert scoring.donchian_breakout(back_inside, 20, 2) is None          # closed back in the channel
    down = base + _bars([97.0], H4, 1_800_000_000 + H4, spread=1.0)
    assert scoring.donchian_breakout(down, 20, 2)["direction"] == "SHORT"


# ── 3. the daily trend gate ──────────────────────────────────────────────────────────────

def test_the_daily_sma_gate_blocks_a_long_in_a_bear_market():
    c4h = _bars(_flat_then_up(up=1), H4, 1_800_000_000)
    bull = _bars([90.0] * 60, D1, 1_800_000_000)       # SMA50 = 90 < price 101
    bear = _bars([120.0] * 60, D1, 1_800_000_000)      # SMA50 = 120 > price
    assert scoring.build_thesis("BTC", c4h, bull, INPUTS)["direction"] == "LONG"
    assert scoring.build_thesis("BTC", c4h, bear, INPUTS) is None
    assert scoring.build_thesis("BTC", c4h, bull[:40], INPUTS) is None      # 40 days: no regime read
    c4h_down = _bars(_flat_then_down(down=1), H4, 1_800_000_000)
    assert scoring.build_thesis("BTC", c4h_down, bear, INPUTS)["direction"] == "SHORT"
    assert scoring.build_thesis("BTC", c4h_down, bull, INPUTS) is None


def test_regime_reads_above_below_and_on_the_average():
    assert scoring.daily_regime([100.0] * 50, 50, 101.0) == ("BULL", 100.0)
    assert scoring.daily_regime([100.0] * 50, 50, 99.0) == ("BEAR", 100.0)
    assert scoring.daily_regime([100.0] * 50, 50, 100.0) == ("FLAT", 100.0)
    assert scoring.daily_regime([100.0] * 49, 50, 100.0) == (None, 0.0)


# ── 4. volatility sets the leverage ──────────────────────────────────────────────────────

def test_atr_of_a_constant_range_is_that_range():
    bars = _bars([100.0] * 30, H4, 1_800_000_000, spread=1.0)              # TR = 2 every bar
    assert abs(scoring.atr_pct(bars, 14) - 2.0) < 1e-9
    assert scoring.atr_pct(bars[:10], 14) == 0.0


def test_leverage_puts_the_stop_at_least_two_atrs_away_inside_the_range():
    # stop 10% ROE, 2 ATRs: calm coin (ATR 0.7%) -> 10/(2*0.7)=7.1 -> capped at 3
    assert scoring.leverage_for(0.7, 10, 2.0, 2, 3) == 3
    # ATR 1.8%: 10/3.6 = 2.78 -> 2 (3x would put the stop at 3.33% < 2 ATR = 3.6%)
    assert scoring.leverage_for(1.8, 10, 2.0, 2, 3) == 2
    # wild coin (ATR 6%): 10/12 = 0.83 -> floored at 2; the stop is then INSIDE two ATRs
    assert scoring.leverage_for(6.0, 10, 2.0, 2, 3) == 2
    assert scoring.leverage_for(0.0, 10, 2.0, 2, 3) == 2                  # unreadable ATR: the floor
    assert scoring.leverage_for(0.7, 10, 2.0, 1, 5) == 5
    assert scoring.leverage_for(1.8, 10, 2.0, 1, 5) == 2
    # the three majors at their 30-day median 4h ATR (measured 2026-10-12): 1.06 / 1.36 / 1.81% of price
    assert [scoring.leverage_for(a, 10, 2.0, 2, 5) for a in (1.06, 1.36, 1.81)] == [4, 3, 2]
    assert scoring.leverage_for(0.67, 10, 2.0, 2, 5) == 5                 # a calm BTC week: the cap


def test_the_signal_carries_its_stop_distance_in_price():
    c4h = _bars(_flat_then_up(up=1), H4, 1_800_000_000, spread=0.3)        # ATR ~0.6% -> the 5x cap
    bull = _bars([90.0] * 60, D1, 1_800_000_000)
    th = scoring.build_thesis("BTC", c4h, bull, INPUTS)
    assert th["leverage"] == 5 and abs(th["stop_price_pct"] - 2.0) < 1e-3         # rounded to 4 dp on the signal
    wild = _bars(_flat_then_up(up=1), H4, 1_800_000_000, spread=3.0)       # ATR ~6% -> floor 2x
    th2 = scoring.build_thesis("BTC", wild, bull, INPUTS)
    assert th2["leverage"] == 2 and th2["stop_price_pct"] == 5.0 and th2["atr_pct"] > 2 * 5.0 / 2


# ── 5. scan(): the reads, the dedup, the gates ───────────────────────────────────────────

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
    """BTC: fresh bull cross above its daily SMA. ETH: flat, no cross. SOL: a bull cross but held."""

    def __init__(self, now, withdrawable="1000", held=("SOL",), eth_closes=None):
        self.now, self.withdrawable, self.held, self.calls = now, withdrawable, held, []
        self.eth_closes = eth_closes

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            return {"success": True, "data": {"main": {
                "marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                "withdrawable": self.withdrawable,
                "assetPositions": [{"position": {"coin": c, "szi": "1.5", "marginUsed": "50"}} for c in self.held],
            }, "xyz": {"marginSummary": {"accountValue": "1000"}, "withdrawable": self.withdrawable,
                       "assetPositions": []}}}
        if tool == "market_get_asset_data":
            last_open = self.now - H4 - 100                     # last CLOSED bar opened 4h+100s ago
            closes = {"BTC": _flat_then_up(up=1), "SOL": _flat_then_up(up=1),
                      "ETH": self.eth_closes or [100.0] * 101}[args["asset"]]
            bars = _bars(closes, H4, last_open, spread=0.3)
            # the bar IN PROGRESS: a crash that would flip the EMAs if it were counted
            live = _bars([60.0], H4, last_open + H4, spread=0.3)
            daily = _bars([90.0] * 60, D1, self.now - D1 - 100)
            return {"success": True, "data": {"asset": args["asset"],
                                              "candles": {"4h": bars + live, "1d": daily}}}
        return {}


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.state, self.dry_run = mcp, "0xmoose", state, False
        self.scanner_name, self.interval_seconds = "moose_main_signals", 300


def test_scan_emits_the_fresh_cross_once_and_only_off_closed_bars():
    now = time.time()
    mcp, st = _MCP(now), _State()
    out = scan.scan(dict(INPUTS), _Ctx(mcp, st))
    assert [(s["asset"], s["direction"]) for s in out] == [("BTC", "LONG")]
    s = out[0]
    assert s["marginPct"] == 30 and INPUTS["leverageMin"] <= s["leverage"] <= INPUTS["leverageMax"]
    assert s["data"]["detector"] == "ema_cross" and s["data"]["dailyRegime"] == "BULL"
    assert s["data"]["barsSinceSignal"] == 0 and s["data"]["heldAssets"] == ["SOL"]
    assert all(v is not None for v in s["data"].values())
    # SOL is held: it was never even fetched; ETH was read and found flat
    fetched = [a["asset"] for t, a in mcp.calls if t == "market_get_asset_data"]
    assert fetched == ["BTC", "ETH"]
    # the same cross on the next tick is not a second signal
    assert scan.scan(dict(INPUTS), _Ctx(_MCP(now), st)) == []
    assert any(k.startswith("BTC|LONG|") for k in st.last()["signaled"])


def test_scan_emits_nothing_when_the_wallet_cannot_be_read_or_is_full():
    now = time.time()

    class _Broken(_MCP):
        def call_tool(self, tool, args):
            if tool == "strategy_get_clearinghouse_state":
                raise RuntimeError("boom")
            return super().call_tool(tool, args)

    assert scan.scan(dict(INPUTS), _Ctx(_Broken(now), _State())) == []
    assert scan.scan(dict(INPUTS), _Ctx(_MCP(now, held=("BTC", "ETH", "SOL")), _State())) == []
    assert scan.scan(dict(INPUTS), _Ctx(_MCP(now, withdrawable="5"), _State())) == []   # 30% x 3x of $5 < $12


def test_a_string_payload_does_not_kill_the_tick():
    now = time.time()

    class _Stringy(_MCP):
        def call_tool(self, tool, args):
            if tool == "market_get_asset_data" and args["asset"] == "BTC":
                return "upstream says hello"
            return super().call_tool(tool, args)

    assert scan.scan(dict(INPUTS), _Ctx(_Stringy(now), _State())) == []    # ETH flat, SOL held, BTC unreadable


def test_scan_works_without_state_history():
    out = scan.scan(dict(INPUTS), _Ctx(_MCP(time.time()), state=None))
    assert [s["asset"] for s in out] == ["BTC"]


def test_the_donchian_detector_emits_through_the_same_path():
    now = time.time()
    inputs = dict(INPUTS, entryDetector="donchian")
    out = scan.scan(inputs, _Ctx(_MCP(now), _State()))
    assert [s["asset"] for s in out] == ["BTC"]
    assert out[0]["data"]["detector"] == "donchian" and "channelHigh" in out[0]["data"]
    assert "emaFast" not in out[0]["data"]


def test_every_emitted_key_is_declared_and_every_declared_key_is_emitted():
    schema = SIGNALS["signal_data_schema"]
    required = {k for k, v in schema.items() if v.get("required", True)}
    seen = set()
    for detector in ("ema_cross", "donchian"):
        out = scan.scan(dict(INPUTS, entryDetector=detector), _Ctx(_MCP(time.time()), _State()))
        for s in out:
            keys = set(s["data"])
            assert keys <= set(schema), keys - set(schema)
            assert required <= keys, required - keys
            seen |= keys
    assert seen == set(schema), set(schema) - seen


# ── 6. the config and the scanner agree ──────────────────────────────────────────────────

def test_the_stop_the_scanner_sizes_against_is_the_stop_the_dsl_enforces():
    assert INPUTS["stopRoePct"] == RUNTIME["exit"]["dsl_preset"]["phase1"]["max_loss_pct"]
    assert RUNTIME["exit"]["dsl_preset"]["phase1"]["enabled"] is False


def test_leverage_bounds_match_the_catalog_and_the_runtime_default():
    assert INPUTS["leverageMax"] == MANIFEST["catalog"]["leverage_max"] == RUNTIME["strategy"]["default_leverage"]
    assert 1 <= INPUTS["leverageMin"] <= INPUTS["leverageMax"]
    assert INPUTS["maxSlots"] == RUNTIME["strategy"]["slots"] == MANIFEST["catalog"]["max_slots"]
    assert INPUTS["allowedAssets"] == MANIFEST["catalog"]["assets"]
    assert INPUTS["maxSlots"] * INPUTS["marginPct"] <= 100


def test_the_ladder_only_ever_locks_a_gain_and_no_clock_cuts():
    pre = RUNTIME["exit"]["dsl_preset"]
    tiers = pre["phase2"]["tiers"]
    triggers = [t["trigger_pct"] for t in tiers]
    assert triggers == sorted(triggers) and len(set(triggers)) == len(triggers)
    for t in tiers:
        assert 0 < t["lock_hw_pct"] < 100
    locks = [t["lock_hw_pct"] for t in tiers]
    assert locks == sorted(locks)
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False
    # the stop in price is widest at the floor and narrowest at the cap, and the cap is only used
    # when two ATRs fit inside it (scoring.leverage_for) — so the narrow stop is never inside noise
    assert pre["phase1"]["max_loss_pct"] / INPUTS["leverageMin"] >= 3.0
    assert pre["phase1"]["max_loss_pct"] / INPUTS["leverageMax"] > 1.0    # clears the validator's wick band


def test_the_tick_budget_sits_below_the_cadence_and_the_cap_above_the_slots():
    assert SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_halt_pct"] == 25                           # the wallet-wide hard stop
    assert rails["drawdown_reset_on_day_rollover"] is False
