"""Vulture 2.0.0: the chart confirms the board — pinned.

Measured on 553 live Vulture signals (2026-08-21 to 2026-10-12, public candles, this package's own
exits, 0.18% fees): the board's pick alone was fee-neutral (+0.09% a trade); the 185 signals on the
wrong side of the 50-day average lost 0.83% each; with the three chart gates the 203 remaining made
+0.85% a trade at a 1.6 profit factor. These tests pin the gates, the fail-closed candle read, the
one-read-per-candidate budget, the 60-second cadence, and the 1.3.0 stop/leverage/volume-floor rules.

Run: python3 -B -m pytest strategies/vulture/tests -q
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
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "vulture_main_signals")
INPUTS = dict(SIGNALS["inputs"], quietStartUtc=0, quietEndUtc=0)   # quiet hours off so the clock cannot fail a test
NOW = time.time()


def _row(token, pct=20.0, traders=40, p4h=5.0, p1h=0.5, p15m=0.8, c15m=1.5, c1h=0.5, c4h=2.5, direction="LONG"):
    """One leaderboard_get_markets row in the documented shape; the defaults clear every board gate and score >= 10."""
    return {"token": token, "dex": "", "direction": direction, "pct_of_top_traders_gain": pct,
            "trader_count": traders, "token_price_change_pct_4h": p4h, "token_price_change_pct_1h": p1h,
            "token_price_change_pct_15m": p15m, "contribution_pct_change_15m": c15m,
            "contribution_pct_change_1h": c1h, "contribution_pct_change_4h": c4h}


def _inst(name, max_lev, vol):
    return {"name": name, "max_leverage": max_lev, "is_delisted": False, "context": {"dayNtlVlm": str(vol)}}


def _candles(price=10.0, trend="BULL", range_pos=0.9, days=60, hours=30, now=NOW):
    """A market_get_asset_data `candles` dict in the feed's shape (o/h/l/c strings, t/T epoch ms, last row in
    progress). `trend` BULL puts the closed daily closes below `price` (so price > SMA50), BEAR above.
    The in-progress daily row carries an absurd close so a test catches any reader that includes it.
    `range_pos` places `price` inside the closed hourly range for a LONG (1 = at the high)."""
    daily = []
    for k in range(days, 0, -1):                       # oldest first; opened k days ago, closed
        c = price * (0.7 if trend == "BULL" else 1.3) if trend != "FLAT" else price
        t = int((now - k * 86400) * 1000)
        daily.append({"t": t, "T": t + 86400000, "s": "X", "i": "1d", "o": f"{c}", "h": f"{c * 1.01}", "l": f"{c * 0.99}", "c": f"{c}", "v": "1", "n": 1})
    t = int((now - 3600) * 1000)                       # the day in progress: opened an hour ago
    daily.append({"t": t, "T": t + 86400000, "s": "X", "i": "1d", "o": f"{price}", "h": f"{price * 50}", "l": f"{price / 50}", "c": f"{price * 50}", "v": "1", "n": 1})
    span = price * 0.10
    lo = price - range_pos * span
    hi = lo + span
    hourly = []
    for k in range(hours, 0, -1):
        t = int((now - k * 3600) * 1000)
        hourly.append({"t": t, "T": t + 3600000, "s": "X", "i": "1h", "o": f"{lo}", "h": f"{hi}", "l": f"{lo}", "c": f"{lo}", "v": "1", "n": 1})
    t = int((now - 600) * 1000)                        # the hour in progress: its close is the last price
    hourly.append({"t": t, "T": t + 3600000, "s": "X", "i": "1h", "o": f"{price}", "h": f"{price}", "l": f"{price}", "c": f"{price}", "v": "1", "n": 1})
    return {"1d": daily, "1h": hourly}


# ── the config, as shipped ───────────────────────────────────────────────────────────────

def test_no_clock_cut_and_the_stop_is_outside_the_measured_bounce():
    pre = RUNTIME["exit"]["dsl_preset"]
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, cut
    assert pre["phase1"]["enabled"] is False
    stop_px = pre["phase1"]["max_loss_pct"] / INPUTS["maxLeverage"]
    assert stop_px >= 3.0                                  # 15% ROE at 5x = 3.0% of price; p75 bounce 1.38%
    tiers = pre["phase2"]["tiers"]
    assert tiers[0]["lock_hw_pct"] == 40 and tiers[0]["trigger_pct"] == 15
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


def test_leverage_is_five_everywhere_and_the_catalog_agrees():
    assert all(t["leverage"] == 5 for t in INPUTS["leverageTiers"])
    assert all(t["leverage"] == 5 for t in scan._DEFAULT_TIERS)
    assert INPUTS["maxLeverage"] == MANIFEST["catalog"]["leverage_max"] == RUNTIME["strategy"]["default_leverage"] == 5


def test_cadence_margin_gates_and_version():
    assert SIGNALS["interval_seconds"] == 60 and SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == 40
    assert RUNTIME["strategy"]["slots"] * INPUTS["marginPct"] <= 100
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False and rails["drawdown_halt_pct"] == 25
    assert INPUTS["minDayNtlVlmUsd"] == 1_000_000
    assert INPUTS["trendSmaDays"] == 50 and INPUTS["minPriceChange15mPct"] == 0.5 and INPUTS["minRangePosition"] == 0.5
    assert INPUTS["rangeLookbackHours"] == 24
    assert MANIFEST["version"] == "2.1.0"
    schema = SIGNALS["signal_data_schema"]
    assert {"trendRegime", "trendSma", "rangePos24h"} <= set(schema) and "persistenceHours" not in schema


# ── the chart gates (pure) ───────────────────────────────────────────────────────────────

def test_trend_regime_reads_closed_daily_candles_only():
    c = _candles(price=10.0, trend="BULL")
    regime, sma, n = scoring.trend_regime(c["1d"], 50, 10.0, NOW)
    assert regime == "BULL" and n == 60 and abs(sma - 7.0) < 1e-9     # the in-progress day (close 500) is excluded
    assert scoring.trend_regime(_candles(trend="BEAR")["1d"], 50, 10.0, NOW)[0] == "BEAR"
    assert scoring.trend_regime(_candles(days=20)["1d"], 50, 10.0, NOW)[0] is None     # a young listing: no verdict
    assert scoring.trend_regime(c["1d"], 50, 0.0, NOW)[0] is None
    assert scoring.trend_regime([{"t": "x"}, {}], 50, 10.0, NOW)[0] is None


def test_range_position_is_measured_in_the_trade_direction():
    c = _candles(price=10.0, range_pos=0.9)
    assert abs(scoring.range_position(c["1h"], 10.0, "LONG", 24, NOW) - 0.9) < 1e-6
    assert abs(scoring.range_position(c["1h"], 10.0, "SHORT", 24, NOW) - 0.1) < 1e-6
    assert scoring.range_position(_candles(hours=3)["1h"], 10.0, "LONG", 24, NOW) is None   # too few closed bars
    assert scoring.range_position(c["1h"], 0.0, "LONG", 24, NOW) is None


def test_confirm_candidate_applies_the_three_gates_in_order():
    base = {"asset": "HEMI", "direction": "LONG", "p15m": 0.8}
    ok, why, meta = scoring.confirm_candidate(base, _candles(), INPUTS, NOW)
    assert ok and why.startswith("CHART_OK") and meta["trend_regime"] == "BULL" and abs(meta["range_pos"] - 0.9) < 1e-6
    assert scoring.confirm_candidate(dict(base, p15m=0.2), _candles(), INPUTS, NOW)[1].startswith("PRICE_15M")
    assert scoring.confirm_candidate(dict(base, p15m=-0.8, direction="SHORT"), _candles(trend="BEAR", range_pos=0.1), INPUTS, NOW)[0]
    assert scoring.confirm_candidate(base, _candles(trend="BEAR"), INPUTS, NOW)[1].startswith("TREND_BEAR")
    assert scoring.confirm_candidate(dict(base, direction="SHORT", p15m=-0.8), _candles(trend="BULL"), INPUTS, NOW)[1].startswith("TREND_BULL")
    assert scoring.confirm_candidate(base, _candles(days=10), INPUTS, NOW)[1].startswith("TREND_UNREADABLE")
    assert scoring.confirm_candidate(base, _candles(range_pos=0.3), INPUTS, NOW)[1].startswith("RANGE_POS")
    assert scoring.confirm_candidate(base, {}, INPUTS, NOW)[1].startswith("NO_PRICE")
    assert scoring.confirm_candidate(base, {"1d": _candles()["1d"], "1h": []}, INPUTS, NOW)[1].startswith("RANGE_UNREADABLE")


# ── the venue cap and the volume floor (1.3.0) ───────────────────────────────────────────

def test_the_venue_cap_wins_over_the_tier_and_the_package_cap():
    assert scoring.clamp_leverage(5, 5, 3) == 3
    assert scoring.clamp_leverage(5, 5, 20) == 5
    assert scoring.clamp_leverage(7, 5, None) == 5                  # unreadable venue max: the package cap
    assert scoring.clamp_leverage(5, 5, "3.0") == 3                 # the field arrives as a string on some rows
    assert scoring.clamp_leverage(0.4, 5, 3) == 1                   # never below 1


def test_a_board_row_with_a_bad_trader_count_scores_instead_of_raising():
    # 2.1.0: the runtime no longer ships a `whitelist` input (the universe is derived), so the
    # scorer is handed the historical basket directly — score_market still takes the universe
    # it should match against, whatever produced it.
    assert scoring.score_market(_row("HEMI", traders="40.0"), None, {}, scoring.DEFAULT_WHITELIST, INPUTS["leverageTiers"], INPUTS) is not None
    assert scoring.score_market(_row("HEMI", traders=None), None, {}, scoring.DEFAULT_WHITELIST, INPUTS["leverageTiers"], INPUTS) is None   # 0 traders: the gate, not a crash


# ── scan(): one tick against a fake desk ─────────────────────────────────────────────────

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
    def __init__(self, markets, instruments, held=(), fail=(), candles=None):
        self.markets, self.instruments, self.held, self.fail, self.calls = markets, instruments, held, set(fail), []
        self.candles = candles or {}

    def call_tool(self, tool, args):
        self.calls.append((tool, args.get("asset")))
        if tool in self.fail:
            raise RuntimeError(f"{tool} down")
        if tool == "leaderboard_get_markets":
            return {"data": {"markets": self.markets}}
        if tool == "market_list_instruments":
            return {"success": True, "data": {"instruments": self.instruments, "count": len(self.instruments)}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"assetPositions": [{"position": {"coin": c, "szi": "1"}} for c in self.held]}}}
        if tool == "market_get_funding_regime":
            return {"data": {"regime": None}}
        if tool == "market_get_asset_data":
            if args["asset"] in self.fail:
                raise RuntimeError("asset data down")
            return {"success": True, "data": {"candles": self.candles.get(args["asset"], _candles())}}
        return {}


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.state, self.dry_run = mcp, "0xvulture", state, False
        self.scanner_name, self.interval_seconds = "vulture_main_signals", 60


def test_a_tick_clamps_to_the_venue_cap_skips_thin_names_and_survives_a_bad_row():
    markets = [_row("HEMI"), _row("LDO"), {"token": "WLD", "dex": "", "direction": "LONG", "pct_of_top_traders_gain": "lots"},
               _row("BTC"), _row("TAO", direction="SHORT", p4h=-6.0, p1h=-0.5, p15m=-0.8)]
    instruments = [_inst("HEMI", 3, 5_000_000), _inst("LDO", 5, 150_000), _inst("WLD", 5, 9_000_000), _inst("TAO", 5, 30_000_000)]
    mcp = _MCP(markets, instruments, candles={"TAO": _candles(trend="BEAR", range_pos=0.1)})
    out = scan.scan(dict(INPUTS), _Ctx(mcp, _State()))
    by = {s["asset"]: s for s in out}
    assert set(by) == {"HEMI", "TAO"}                                 # LDO under the floor; WLD malformed; BTC banned
    assert by["HEMI"]["leverage"] == 3 and by["HEMI"]["data"]["venueMaxLeverage"] == 3   # 5x asked, 3x is the cap
    assert by["TAO"]["leverage"] == 5 and by["TAO"]["direction"] == "SHORT" and by["TAO"]["data"]["trendRegime"] == "BEAR"
    assert by["HEMI"]["marginPct"] == 40 and by["HEMI"]["data"]["dayVolumeUsd"] == 5_000_000
    assert by["HEMI"]["data"]["trendRegime"] == "BULL" and abs(by["HEMI"]["data"]["rangePos24h"] - 0.9) < 1e-6
    assert any(r.startswith("CHART_OK") for r in by["HEMI"]["data"]["reasons"])
    schema = SIGNALS["signal_data_schema"]
    for s in out:
        assert set(s["data"]) <= set(schema) and all(v is not None for v in s["data"].values())
    reads = [t for t, _ in mcp.calls]
    assert reads.count("market_get_asset_data") == 2 and "market_get_funding_history" not in reads   # one candle read per board-gated name, no persistence read


def test_the_chart_rejects_a_board_gated_name_and_says_why(capsys):
    markets = [_row("HEMI"), _row("WLD"), _row("XPL", p15m=0.1), _row("MON")]
    instruments = [_inst(n, 5, 5_000_000) for n in ("HEMI", "WLD", "XPL", "MON")]
    candles = {"HEMI": _candles(trend="BEAR"), "WLD": _candles(range_pos=0.2), "MON": _candles(days=10)}
    state = _State()
    out = scan.scan(dict(INPUTS), _Ctx(_MCP(markets, instruments, candles=candles), state))
    assert out == []
    err = capsys.readouterr().err
    assert "SKIP HEMI LONG" in err and "TREND_BEAR" in err
    assert "SKIP WLD LONG" in err and "RANGE_POS" in err
    assert "SKIP XPL LONG" in err and "PRICE_15M" in err
    assert "SKIP MON LONG" in err and "TREND_UNREADABLE" in err
    assert state.last()["result"]["gate"] == "chart_rejected" and set(state.last()["result"]["rejected"]) == {"HEMI", "WLD", "XPL", "MON"}


def test_unreadable_candles_fail_closed_for_that_name_and_the_tick_survives(capsys):
    markets = [_row("HEMI"), _row("WLD")]
    instruments = [_inst("HEMI", 5, 5_000_000), _inst("WLD", 5, 5_000_000)]
    out = scan.scan(dict(INPUTS), _Ctx(_MCP(markets, instruments, fail=("HEMI",)), _State()))
    assert [s["asset"] for s in out] == ["WLD"]
    assert "SKIP HEMI LONG: candles unreadable" in capsys.readouterr().err


def test_an_instruments_outage_now_HOLDS_rather_than_trading_an_unvetted_board():
    """CONTRACT CHANGED IN 2.1.0, deliberately — this test asserted the opposite until the
    universe was derived.

    While the universe was a hard-coded whitelist, a failed `market_list_instruments` read cost
    only the venue leverage clamp and the volume floor, so degrading to the package cap and
    trading on was the right call: the names were already vetted by hand.

    Now that same read IS the universe. A failure means we do not know what is listed, how
    liquid it is, or what leverage the venue will accept — so trading the raw board on that
    basis would be entering unvetted names at an unclamped size. On a 60-second cadence a
    skipped tick costs nothing, and the next tick retries."""
    st = _State()
    out = scan.scan(dict(INPUTS), _Ctx(_MCP([_row("HEMI")], [], fail=("market_list_instruments",)), st))
    assert out == [], "an unreadable instrument list must not be traded through"
    # The REASON is pinned, not just the empty return. Deriving from an empty instrument map
    # would also return [] — via `empty_universe` — and the two demand opposite responses from
    # whoever is reading the logs: a broken venue read is an incident, while "nothing clears the
    # floor" is the market. A test that only asserts [] cannot tell them apart.
    assert st.last()["result"]["gate"] == "instruments_unreadable", (
        f"gate was {st.last()['result'].get('gate')!r} — held for the wrong stated reason")


def test_an_instruments_outage_falls_back_to_an_explicit_override_if_one_is_set():
    """The escape hatch that makes the fail-closed path acceptable: an operator who pins a
    basket keeps trading through an outage, because their names were vetted by hand."""
    inputs = dict(INPUTS, whitelist=["HEMI"])
    out = scan.scan(inputs, _Ctx(_MCP([_row("HEMI")], [], fail=("market_list_instruments",)), _State()))
    assert [x["asset"] for x in out] == ["HEMI"]
    assert out[0]["leverage"] == 5, "no instrument list means no venue clamp, so the package cap applies"


# ── 2.1.0: the universe is read off the venue ────────────────────────────────────────────

def test_the_universe_is_derived_by_volume_and_drops_the_unqualified():
    """Pure, so the band is pinned without MCP: over the floor, not delisted, not a banned
    major, not an xyz name, ranked by notional, capped at N."""
    insts = {
        "BTC":  {"day_ntl_vlm": 2_500_000_000.0, "delisted": False, "max_leverage": 40},
        "HYPE": {"day_ntl_vlm": 500_000_000.0,   "delisted": False, "max_leverage": 10},
        "XRP":  {"day_ntl_vlm": 147_000_000.0,   "delisted": False, "max_leverage": 20},
        "PUMP": {"day_ntl_vlm": 152_000_000.0,   "delisted": False, "max_leverage": 10},
        "AIXBT": {"day_ntl_vlm": 170_000.0,      "delisted": False, "max_leverage": 5},
        "GONE": {"day_ntl_vlm": 90_000_000.0,    "delisted": True,  "max_leverage": 5},
        "XYZ:GOLD": {"day_ntl_vlm": 80_000_000.0, "delisted": False, "max_leverage": 5},
        "USDC": {"day_ntl_vlm": 70_000_000.0,    "delisted": False, "max_leverage": 3},
    }
    got = scan.derive_universe(insts, 1_000_000, 60, {"BTC", "ETH", "SOL"}, {"USDC", "USDT"})
    assert got == ["HYPE", "PUMP", "XRP"], f"got {got}"   # 500M > 152M > 147M
    assert "BTC" not in got, "a banned major must go by NAME, not by volume rank"
    assert "AIXBT" not in got, "under the floor — the rot the whitelist was carrying"
    assert "GONE" not in got and "XYZ:GOLD" not in got and "USDC" not in got


def test_the_derived_universe_returns_the_venues_own_casing_not_the_upper_key():
    """The bug this nearly shipped with. `_instruments` keys on name.upper() for the membership
    joins, but the universe becomes the emitted `asset` — and kBONK/kPEPE and any `xyz:` name do
    not survive upper(). Returning the key would have sent KBONK to the venue and had the order
    refused as an unknown coin, which is the casing failure this repo has already been bitten by."""
    insts = {
        "KBONK": {"venue_name": "kBONK", "day_ntl_vlm": 5_000_000.0, "delisted": False},
        "KPEPE": {"venue_name": "kPEPE", "day_ntl_vlm": 4_000_000.0, "delisted": False},
        "HYPE":  {"venue_name": "HYPE",  "day_ntl_vlm": 9_000_000.0, "delisted": False},
    }
    got = scan.derive_universe(insts, 1_000_000, 60, set(), set())
    assert got == ["HYPE", "kBONK", "kPEPE"], f"got {got}"
    assert "KBONK" not in got and "KPEPE" not in got


def test_the_instrument_reader_keeps_the_venue_name_for_every_row():
    """The derivation can only return the venue's spelling if the reader kept it."""
    mcp = _MCP([], [_inst("kBONK", 5, 5_000_000)])
    insts = scan._instruments(_Ctx(mcp, _State()))
    assert insts["KBONK"]["venue_name"] == "kBONK"


def test_the_derived_universe_is_capped_and_ordered_by_volume():
    insts = {f"C{i}": {"day_ntl_vlm": float(i) * 1_000_000, "delisted": False} for i in range(1, 11)}
    got = scan.derive_universe(insts, 1_000_000, 3, set(), set())
    assert got == ["C10", "C9", "C8"], f"got {got}"


def test_a_name_the_whitelist_carried_but_the_floor_rejects_is_gone():
    """The specific rot measured on 2026-10-12: 4 of the 27 names had fallen under $1M/day."""
    for rotted in ("AIXBT", "DYDX", "HEMI", "POLYX"):
        insts = {rotted: {"day_ntl_vlm": 170_000.0, "delisted": False}}
        assert scan.derive_universe(insts, 1_000_000, 60, set(), set()) == []


def test_an_explicit_whitelist_still_pins_the_universe():
    """`whitelist` survives as an override, so an identity basket is still expressible — which
    is the one case a hand-listed universe is right for."""
    insts = [_inst("HEMI", 5, 5_000_000), _inst("WLD", 5, 9_000_000)]
    mcp = _MCP([_row("HEMI"), _row("WLD")], insts)
    out = scan.scan(dict(INPUTS, whitelist=["HEMI"]), _Ctx(mcp, _State()))
    assert [x["asset"] for x in out] == ["HEMI"], "WLD cleared every gate but is not in the override"


def test_the_chart_read_count_is_capped_so_it_cannot_grow_with_the_universe():
    """The reason the cap exists: `timeout_seconds` is 45 against a 60s tick, and a timeout
    latches the MCP client closed for the life of the process while ticks still report ok
    (strategies/tests/test_scanner_timeout_headroom.py). Reads must not scale with the universe."""
    names = [f"N{i}" for i in range(12)]
    insts = [_inst(n, 5, 5_000_000) for n in names]
    mcp = _MCP([_row(n) for n in names], insts)
    scan.scan(dict(INPUTS, maxChartReads=4), _Ctx(mcp, _State()))
    reads = [c for c in mcp.calls if c[0] == "market_get_asset_data"]
    assert len(reads) == 4, f"{len(reads)} candle reads against a cap of 4"


def test_a_held_name_is_not_re_emitted_and_a_board_outage_holds():
    mcp = _MCP([_row("HEMI")], [_inst("HEMI", 5, 5_000_000)], held=("HEMI",))
    assert scan.scan(dict(INPUTS), _Ctx(mcp, _State())) == []
    assert ("market_get_asset_data", "HEMI") not in mcp.calls           # a held name costs no candle read
    assert scan.scan(dict(INPUTS), _Ctx(_MCP([_row("HEMI")], [_inst("HEMI", 5, 5_000_000)], fail=("leaderboard_get_markets",)), _State())) == []
