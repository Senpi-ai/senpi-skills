"""Salamander 1.3.0: a 12% ROE stop, no clock, one board read a tick, 40% at 5x.

Live over 60 days the package closed six positions and every one was the exchange stop: the entry
is a 3-7% pullback and the 6% ROE stop it carried (1.2% of price at 5x) never gave the dip room to
finish. 1.3.0 sets the stop at 12% ROE (2.4% of price at 5x), turns the 48h clock off so the DSL
decides every exit, lifts the single slot to 40% margin, and reads the 4h leaderboard once a tick
instead of once per coin — with an unreadable board logged as an outage rather than reported as
"no pullback". These tests pin the shipped numbers, the pure math, and a tick against a fake desk.

Run: python3 -B -m pytest strategies/salamander/tests -q
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
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "salamander_main_signals")
INPUTS = dict(SIGNALS["inputs"])
NOW = time.time()


# ── fixtures in the venue's own shape ────────────────────────────────────────────────────

def _bar(o, h, l, c, k, step):
    """One candle as market_get_asset_data returns it: o/h/l/c strings, t/T epoch ms, `k` steps ago."""
    t = int((NOW - k * step) * 1000)
    return {"t": t, "T": t + step * 1000, "s": "X", "i": "x", "o": f"{o}", "h": f"{h}", "l": f"{l}",
            "c": f"{c}", "v": "1", "n": 1}


def _candles(trend="BULLISH", move_pct=5.0, hours=30):
    """A `candles` dict: eight 4h bars with strictly higher lows (BULLISH), strictly lower highs
    (BEARISH) or neither (NEUTRAL); `hours` 1h bars closing at 100, the last one `move_pct` below
    (a dip, for a long) or above (a rally, for a short)."""
    four_h = []
    for k in range(8, 0, -1):                                  # oldest first
        lo = 90.0 + (8 - k) if trend == "BULLISH" else 90.0
        hi = 110.0 - (8 - k) if trend == "BEARISH" else 110.0
        four_h.append(_bar(100, hi, lo, 100, k, 4 * 3600))
    last = 100.0 * (1 - move_pct / 100) if trend == "BULLISH" else 100.0 * (1 + move_pct / 100)
    one_h = [_bar(100, 101, 99, 100, k, 3600) for k in range(hours, 1, -1)]
    one_h.append(_bar(100, 101, 99, last, 1, 3600))
    return {"1h": one_h, "4h": four_h}


def _row(token, direction, pct, traders, dex=""):
    """One leaderboard_get_markets row in the documented shape."""
    return {"token": token, "dex": dex, "direction": direction, "pct_of_top_traders_gain": pct,
            "trader_count": traders}


def _board(lean="LONG", pct=70.0, traders=40, coins=("BTC", "ETH", "SOL")):
    """Two rows per coin: the leaning side with `pct` of the 4h winners' gain and `traders` on it,
    the other side with the rest and a thin crowd."""
    other = "SHORT" if lean == "LONG" else "LONG"
    rows = []
    for coin in coins:
        rows += [_row(coin, lean, pct, traders), _row(coin, other, 100.0 - pct, 20)]
    return rows


class _State:
    def __init__(self):
        self._log = []

    def __len__(self):
        return len(self._log)

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)


class _MCP:
    """A fake desk: the clearinghouse, the 4h board and the candle feed. `fail` names tools that
    raise, or coins whose candle read raises."""

    def __init__(self, board=None, candles=None, held=(), fail=()):
        self.board = _board() if board is None else board
        self.candles = candles or {}
        self.held, self.fail, self.calls = tuple(held), set(fail), []

    def call_tool(self, tool, args):
        self.calls.append((tool, args.get("asset")))
        if tool in self.fail:
            raise RuntimeError(f"{tool} down")
        if tool == "strategy_get_clearinghouse_state":
            positions = [{"position": {"coin": c, "szi": "0.5", "marginUsed": "400"}} for c in self.held]
            summary = {"accountValue": "1000.0", "totalMarginUsed": "400" if self.held else "0"}
            return {"data": {"main": {"marginSummary": summary, "assetPositions": positions}}}
        if tool == "leaderboard_get_markets":
            return {"data": {"markets": self.board}}
        if tool == "market_get_asset_data":
            if args["asset"] in self.fail:
                raise RuntimeError("asset data down")
            return {"success": True, "data": {"candles": self.candles.get(args["asset"], _candles())}}
        raise AssertionError(f"unexpected tool {tool!r}")


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.dry_run = mcp, "0x" + "c" * 40, False
        self.state = _State() if state is None else state
        self.scanner_name, self.interval_seconds = "salamander_main_signals", SIGNALS["interval_seconds"]


# ── the config, as shipped ───────────────────────────────────────────────────────────────

def test_the_stop_is_twelve_percent_roe_and_there_is_no_clock():
    pre = RUNTIME["exit"]["dsl_preset"]
    assert pre["phase1"]["enabled"] is False
    assert pre["phase1"]["max_loss_pct"] == 12.0                                     # ROE
    assert abs(pre["phase1"]["max_loss_pct"] / INPUTS["leverage"] - 2.4) < 1e-9      # 2.4% of price at 5x
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, cut
    tiers = pre["phase2"]["tiers"]
    assert tiers[0] == {"trigger_pct": 20, "lock_hw_pct": 40}
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


def test_sizing_leverage_rails_cadence_and_version():
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == scan._DEFAULT_MARGIN_PCT == 40
    assert RUNTIME["strategy"]["slots"] == MANIFEST["catalog"]["max_slots"] == 1
    assert RUNTIME["strategy"]["slots"] * INPUTS["marginPct"] <= 100
    assert (INPUTS["leverage"] == scan._MAX_LEVERAGE == RUNTIME["strategy"]["default_leverage"]
            == MANIFEST["catalog"]["leverage_max"] == 5)
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False
    assert SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]
    assert MANIFEST["version"] == "1.3.0"


def test_the_card_and_the_runtime_quote_the_shipped_stop():
    card = " ".join(str(MANIFEST["catalog"][f]) for f in ("tagline", "belief_plain", "thesis"))
    for text in (card, RUNTIME["description"]):
        assert "12% ROE" in text and "2.4% of price" in text
        assert "+10% ROE" not in text and "48h" not in text and "max_loss 10" not in text
    assert "+20% ROE" in card and "40%" in card


# ── the pure math ────────────────────────────────────────────────────────────────────────

def test_the_trend_reads_higher_lows_and_lower_highs():
    assert scoring.trend_4h(_candles("BULLISH")["4h"]) == "BULLISH"
    assert scoring.trend_4h(_candles("BEARISH")["4h"]) == "BEARISH"
    assert scoring.trend_4h(_candles("NEUTRAL")["4h"]) == "NEUTRAL"
    assert scoring.trend_4h(_candles()["4h"][:5]) == "NEUTRAL"                  # too few bars: no verdict


def test_the_pullback_band_gates_at_three_and_seven_percent():
    for pct, want in ((2.9, None), (3.0, "LONG"), (5.0, "LONG"), (6.9, "LONG"), (7.1, None)):
        got, _ = scoring.detect_pullback(_candles(move_pct=pct)["1h"], 24, 3.0, 7.0, "BULLISH")
        assert got == want, (pct, got)
    assert scoring.detect_pullback(_candles("BEARISH", 4.0)["1h"], 24, 3.0, 7.0, "BEARISH")[0] == "SHORT"
    assert scoring.detect_pullback(_candles(hours=20)["1h"], 24, 3.0, 7.0, "BULLISH") == (None, 0.0)


def test_the_lean_is_the_v2_math_on_the_board_rows():
    assert scan._sm_direction(_board("LONG", 70.0, 40), "BTC", 10) == ("LONG", 70.0)
    assert scan._sm_direction(_board("SHORT", 60.0, 40), "ETH", 10) == ("SHORT", 60.0)
    assert scan._sm_direction(_board("LONG", 70.0, 5), "BTC", 10) == (None, 0.0)     # leading side too thin to call
    assert scan._sm_direction([_row("BTC", "LONG", 0, 40), _row("BTC", "SHORT", 0, 40)], "BTC") == ("NEUTRAL", 50.0)
    assert scan._sm_direction(_board(), "HYPE") == (None, 0.0)                         # not on the board
    assert scan._sm_direction([_row("BTC", "LONG", 70, 40, dex="xyz")], "BTC") == (None, 0.0)   # an xyz twin never matches


def test_build_thesis_scores_nine_on_the_full_setup_and_gates_the_rest():
    c = _candles()
    th = scoring.build_thesis("BTC", c["1h"], c["4h"], ("LONG", 70.0), INPUTS)
    assert th["score"] == 9 and th["direction"] == "LONG" and th["trend_4h"] == "BULLISH"
    assert abs(th["pullback_pct"] - 5.0) < 1e-6
    assert scoring.build_thesis("BTC", _candles(move_pct=3.5)["1h"], c["4h"], ("LONG", 60.0), INPUTS)["score"] == 7
    assert scoring.build_thesis("BTC", c["1h"], c["4h"], ("SHORT", 70.0), INPUTS) is None      # SM disagrees
    assert scoring.build_thesis("BTC", c["1h"], c["4h"], ("LONG", 54.0), INPUTS) is None       # under the 55% floor
    assert scoring.build_thesis("BTC", c["1h"], c["4h"], (None, 0.0), INPUTS) is None
    assert scoring.build_thesis("BTC", c["1h"], _candles("NEUTRAL")["4h"], ("LONG", 70.0), INPUTS) is None
    s = _candles("BEARISH")
    assert scoring.build_thesis("ETH", s["1h"], s["4h"], ("SHORT", 70.0), INPUTS)["direction"] == "SHORT"


# ── scan(): one tick against the fake desk ───────────────────────────────────────────────

def test_a_tick_emits_the_best_dip_at_forty_percent_and_five_x():
    mcp = _MCP(candles={"BTC": _candles(move_pct=3.5)})            # BTC scores 8 (off the midpoint), ETH/SOL 9
    ctx = _Ctx(mcp)
    out = scan.scan(dict(INPUTS), ctx)
    assert len(out) == 1
    sig = out[0]
    assert sig["asset"] == "ETH" and sig["direction"] == "LONG"
    assert sig["marginPct"] == 40 and sig["leverage"] == 5
    assert sig["data"]["score"] == 9 and sig["data"]["smDirection"] == "LONG" and sig["data"]["smTiltPct"] == 70.0
    schema = SIGNALS["signal_data_schema"]
    assert set(sig["data"]) <= set(schema) and all(v is not None for v in sig["data"].values())
    assert {k for k, v in schema.items() if v.get("required", True)} <= set(sig["data"])
    rec = ctx.state.last()
    assert rec["result"]["gate"] == "emit" and rec["signaled"]["ETH"] > 0


def test_the_board_is_read_once_a_tick_not_once_per_coin():
    """1.2.x called leaderboard_get_markets inside the per-coin loop: three identical reads a tick on
    the default basket, inside a 180 s budget that also has to fit the candle reads."""
    mcp = _MCP()
    scan.scan(dict(INPUTS), _Ctx(mcp))
    reads = [t for t, _ in mcp.calls]
    assert reads.count("market_get_asset_data") == 3, reads               # every un-held coin was scanned
    assert reads.count("leaderboard_get_markets") == 1, reads


def test_a_board_outage_holds_the_tick_and_says_so(capsys):
    """Before 1.3.0 an unreadable board returned (None, 0.0) for every coin, every thesis failed the
    SM gate, and the tick reported "WAITING — no pullback in trend with SM agreement": an outage
    disguised as a quiet market. It now holds with its own reason, and reads no candles."""
    mcp = _MCP(fail=("leaderboard_get_markets",))
    st = _State()
    assert scan.scan(dict(INPUTS), _Ctx(mcp, st)) == []
    assert st.last()["result"]["gate"] == "board_unreadable", st.last()
    assert "market_get_asset_data" not in [t for t, _ in mcp.calls]
    err = capsys.readouterr().err
    assert "leaderboard unreadable" in err and "WAITING" not in err

    class _Dead(_MCP):                                                     # the read returns, but says it failed
        def call_tool(self, tool, args):
            if tool == "leaderboard_get_markets":
                return {"success": False, "error": "upstream"}
            return super().call_tool(tool, args)

    st2 = _State()
    assert scan.scan(dict(INPUTS), _Ctx(_Dead(), st2)) == []
    assert st2.last()["result"]["gate"] == "board_unreadable"


def test_a_quiet_market_is_waiting_not_an_outage():
    mcp = _MCP(board=_board("SHORT"))                                       # the leaders lean short against a bullish chart
    st = _State()
    assert scan.scan(dict(INPUTS), _Ctx(mcp, st)) == []
    assert st.last()["result"]["gate"] == "waiting" and st.last()["result"]["scanned"] == 3


def test_a_failed_candle_read_skips_that_coin_and_the_tick_goes_on(capsys):
    mcp = _MCP(fail=("BTC",))
    out = scan.scan(dict(INPUTS), _Ctx(mcp))
    assert [s["asset"] for s in out] == ["ETH"]
    assert "market_get_asset_data(BTC) read failed" in capsys.readouterr().err


def test_a_failed_clearinghouse_read_returns_nothing_and_reads_nothing_else():
    mcp = _MCP(fail=("strategy_get_clearinghouse_state",))
    assert scan.scan(dict(INPUTS), _Ctx(mcp)) == []
    assert [t for t, _ in mcp.calls] == ["strategy_get_clearinghouse_state"]


def test_a_held_coin_is_not_re_emitted_and_costs_no_candle_read():
    mcp = _MCP(held=("ETH",))
    out = scan.scan(dict(INPUTS), _Ctx(mcp))
    assert [s["asset"] for s in out] == ["BTC"] and out[0]["data"]["heldAssets"] == ["ETH"]
    assert ("market_get_asset_data", "ETH") not in mcp.calls


def test_the_race_window_dedup_skips_a_coin_signaled_seconds_ago():
    st = _State()
    first = scan.scan(dict(INPUTS), _Ctx(_MCP(), st))
    second = scan.scan(dict(INPUTS), _Ctx(_MCP(), st))                     # the same state: BTC was just signaled
    assert first[0]["asset"] == "BTC" and second[0]["asset"] == "ETH"


def test_a_pasted_fraction_margin_becomes_a_percent():
    out = scan.scan(dict(INPUTS, marginPct=0.4), _Ctx(_MCP()))
    assert out and out[0]["marginPct"] == 40.0
