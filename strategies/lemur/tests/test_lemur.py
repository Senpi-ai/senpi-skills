"""Lemur 1.2.0: one position at 40% and 3x, a 12% ROE stop, text that matches the file, a board outage logged as one.

12 Oct 2026 template audit. The runtime text said "one position at a time" while the file carried
2 slots x 15%; the scan emits 3x while the catalog listed 5x; the description promised a first lock
at +10% ROE and a 10% stop that were nowhere in the file (the ladder's first rung was +20% locking
25% of high-water, the stop 8% ROE = 2.7% of price at 3x); and the card said the 4h leaders "must
agree" while the code consults their board only when it carries the name, which for a pre-IPO perp
is the exception. 1.2.0 ships 1 slot x 40% at 3x, a 12% ROE stop (4.0% of price), a first rung
locking 40%, a card that says what the code does, and a scan that logs a board OUTAGE distinctly
from "no board row". These tests pin the numbers, a tick against a fake desk, and that log.

Run: python3 -B -m pytest strategies/lemur/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))
for _m in ("scoring", "scan"):            # a shared pytest session may hold another package's copy
    sys.modules.pop(_m, None)

import scan  # noqa: E402
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "lemur_main_signals")
INPUTS = dict(SIGNALS["inputs"])
CARD = MANIFEST["catalog"]
IPOP = "xyz:SPCX"   # the one live pre-IPO perpetual: tiny funding, 5x cap, over the volume floor


def _candles(lows, highs):
    """Six bars in the feed's shape (o/h/l/c/v strings, t/T epoch ms)."""
    return [{"t": 1789000000000 + i * 3600000, "T": 1789000000000 + (i + 1) * 3600000 - 1,
             "o": str(lo + 0.5), "h": str(hi), "l": str(lo), "c": str(hi - 0.5), "v": "1200", "n": 40}
            for i, (lo, hi) in enumerate(zip(lows, highs))]


BULL = _candles([100, 101, 102, 103, 104, 105], [102, 103, 104, 105, 106, 107])   # 5/5 higher lows -> BULLISH
BEAR = _candles([105, 104, 103, 102, 101, 100], [107, 106, 105, 104, 103, 102])   # 5/5 lower highs -> BEARISH
FLAT = _candles([100, 99, 100, 99, 100, 99], [102, 103, 102, 103, 102, 103])       # 2/5 either way -> NEUTRAL


def _inst(name, max_lev=5, funding="0.00000001", vol="1850000", delisted=False):
    """One market_list_instruments row; the defaults match the IPOP signature."""
    return {"name": name, "dex": "xyz", "max_leverage": max_lev, "is_delisted": delisted, "only_isolated": True,
            "context": {"funding": funding, "dayNtlVlm": vol, "markPx": "104.2", "prevDayPx": "101.9"}}


def _row(token, dex, direction, pct, traders):
    """One leaderboard_get_markets row (one per token/dex/direction)."""
    return {"token": token, "dex": dex, "direction": direction, "max_leverage": 5 if dex == "xyz" else 40,
            "pct_of_top_traders_gain": pct, "trader_count": traders, "is_dominant_direction": pct >= 5.0,
            "token_price_change_pct_4h": 1.3, "token_price_change_pct_1h": 0.5, "token_price_change_pct_15m": 0.1,
            "contribution_pct_change_15m": 0.4, "contribution_pct_change_1h": 0.9, "contribution_pct_change_4h": 1.6,
            "day_notional_volume": 1.0e8}


# what the 4h board normally looks like for a pre-IPO name: rows for the majors, none for it
MAJORS = [_row("BTC", "", "long", 38.2, 41), _row("BTC", "", "short", 0.9, 3), _row("ETH", "", "long", 17.5, 26)]


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
    """The four reads the scan makes, canned. `fail` names tools whose read raises."""

    def __init__(self, instruments, markets=(), held=(), fail=(), c1=BULL, c4=BULL):
        self.instruments, self.held, self.fail = list(instruments), tuple(held), set(fail)
        self.markets = markets if markets is None else list(markets)
        self.c1, self.c4, self.calls = c1, c4, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool in self.fail:
            raise RuntimeError(f"{tool} down")
        if tool == "strategy_get_clearinghouse_state":
            held = [{"position": {"coin": c, "szi": "12.0", "entryPx": "104.0"}} for c in self.held]
            used = "500.0" if held else "0.0"
            return {"success": True, "data": {
                "main": {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0"},
                         "withdrawable": "1000.0", "assetPositions": []},
                "xyz": {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": used, "totalNtlPos": used},
                        "withdrawable": "500.0", "assetPositions": held}}}
        if tool == "market_list_instruments":
            return {"success": True, "data": {"instruments": self.instruments, "count": len(self.instruments)}}
        if tool == "market_get_asset_data":
            return {"success": True, "data": {"asset": args["asset"], "candles": {"1h": self.c1, "4h": self.c4}}}
        if tool == "leaderboard_get_markets":
            return {"success": True, "data": {"markets": self.markets, "source_trader_count": 100}}
        raise AssertionError(f"unexpected tool call {tool!r}")


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.dry_run = mcp, "0x" + "0" * 40, False
        self.state = state if state is not None else _State()
        self.scanner_name, self.interval_seconds = "lemur_main_signals", SIGNALS["interval_seconds"]


def _tick(mcp, inputs=None, state=None):
    ctx = _Ctx(mcp, state)
    return scan.scan(dict(inputs or INPUTS), ctx), ctx.state


# ── the shipped numbers ───────────────────────────────────────────────────────────────────

def test_the_stop_is_twelve_roe_four_of_price_and_there_is_no_clock():
    pre = RUNTIME["exit"]["dsl_preset"]
    assert pre["phase1"]["enabled"] is False                             # max_loss_pct IS the stop
    assert pre["phase1"]["max_loss_pct"] == 12.0
    assert pre["phase1"]["max_loss_pct"] / INPUTS["leverage"] == 4.0     # 4.0% of price at 3x (was 8 = 2.7%)
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, cut
    tiers = pre["phase2"]["tiers"]
    assert tiers[0] == {"trigger_pct": 20, "lock_hw_pct": 40}             # the first rung locks 40 (was 25)
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


def test_one_slot_at_forty_percent_and_three_x_and_the_catalog_agrees():
    assert RUNTIME["strategy"]["slots"] == CARD["max_slots"] == 1
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == scan._DEFAULT_MARGIN_PCT == 40
    assert INPUTS["leverage"] == RUNTIME["strategy"]["default_leverage"] == CARD["leverage_max"] == scan._DEFAULT_LEVERAGE == 3
    assert INPUTS["ipopMaxLeverageCap"] == scoring.MAX_LEVERAGE == 5     # the IPOP discovery filter, not the trading leverage
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False
    assert SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"] == 900
    assert INPUTS["minScore"] == 5 and INPUTS["minTraderCount"] == 10
    assert MANIFEST["version"] == "1.2.0"


def test_the_text_quotes_the_files_numbers_and_the_boards_real_role():
    """1.1.3 promised a +10% first lock and a 10% stop that were nowhere in the file, and said the
    leaders 'must agree' when the code consults their board only when it carries the name."""
    text = " ".join([RUNTIME["description"], CARD["tagline"], CARD["belief_plain"], CARD["thesis"]])
    for stale in ("+10%", "10% stop", "max_loss 10", "15%", "lock 0", "2 slots"):
        assert stale not in text, stale
    assert "12% ROE" in text and "40%" in text and "3x" in text and "+20% ROE" in text
    assert "one position at a time" in text.lower()
    assert "carries the name" in CARD["tagline"] and "carries the name" in RUNTIME["description"]


# ── scan(): one tick against a fake desk ─────────────────────────────────────────────────

def test_a_tick_emits_one_signal_at_forty_percent_and_three_x_inside_the_schema():
    # the normal pre-IPO day: the board holds the majors, not the name; both trends up
    mcp = _MCP([_inst(IPOP), _inst("xyz:TSLA", max_lev=20, funding="0.0000125", vol="41000000")], MAJORS)
    out, state = _tick(mcp)
    assert len(out) == 1
    sig = out[0]
    assert sig["asset"] == IPOP and sig["direction"] == "LONG"
    assert sig["marginPct"] == 40 and sig["leverage"] == 3
    assert sig["data"]["score"] == 5 and sig["data"]["leverage"] == 3            # 4h trend + 1h confirms; no board row
    assert sig["data"]["smDirection"] == "NONE" and "sm_no_board_row" in sig["data"]["reasons"]
    schema = SIGNALS["signal_data_schema"]
    assert set(sig["data"]) <= set(schema) and all(v is not None for v in sig["data"].values())
    rec = state.last()
    assert rec["result"]["gate"] == "pass" and rec["signaled"] == {IPOP.upper(): rec["result"]["ts"]}
    reads = [(t, a.get("asset")) for t, a in mcp.calls]
    assert reads.count(("market_get_asset_data", IPOP)) == 1
    assert ("market_get_asset_data", "xyz:TSLA") not in reads                   # 20x: not an IPOP, never read
    (args,) = [a for t, a in mcp.calls if t == "market_get_asset_data"]
    assert args["dex"] == "xyz" and args["candle_intervals"] == ["1h", "4h"]


def test_a_short_needs_a_bearish_4h_and_the_1h_must_confirm_when_the_board_has_no_row():
    out, _ = _tick(_MCP([_inst(IPOP)], MAJORS, c1=BEAR, c4=BEAR))
    assert out[0]["direction"] == "SHORT" and out[0]["data"]["trend4h"] == "BEARISH"
    out, state = _tick(_MCP([_inst(IPOP)], MAJORS, c1=FLAT, c4=BULL))        # 3 points < minScore 5
    assert out == [] and state.last()["result"]["gate"] == "no_candidate"
    assert _tick(_MCP([_inst(IPOP)], MAJORS, c1=BULL, c4=FLAT))[0] == []       # the 4h gate


def test_a_present_lean_must_agree_and_a_thin_leading_side_is_refused(capsys):
    aligned = MAJORS + [_row("SPCX", "xyz", "long", 6.0, 12), _row("SPCX", "xyz", "short", 1.0, 2)]
    out, _ = _tick(_MCP([_inst(IPOP)], aligned))
    assert out[0]["data"]["score"] == 8 and out[0]["data"]["smDirection"] == "LONG"
    assert out[0]["data"]["smTiltPct"] == 85.71 and "sm_strongly_tilted" in out[0]["data"]["reasons"]
    opposed = MAJORS + [_row("SPCX", "xyz", "short", 6.0, 12), _row("SPCX", "xyz", "long", 1.0, 2)]
    assert _tick(_MCP([_inst(IPOP)], opposed))[0] == []                         # a present row that disagrees blocks
    thin = MAJORS + [_row("SPCX", "xyz", "long", 6.0, 3), _row("SPCX", "xyz", "short", 1.0, 2)]
    out, _ = _tick(_MCP([_inst(IPOP)], thin))
    assert out[0]["data"]["score"] == 5 and out[0]["data"]["smDirection"] == "NONE"   # under minTraderCount: trend-only
    assert "leading side thinner than 10 traders" in capsys.readouterr().err


def test_leverage_is_the_min_of_config_instrument_cap_and_venue_cap():
    assert _tick(_MCP([_inst(IPOP, max_lev=2)], MAJORS))[0][0]["leverage"] == 2                   # the name's own cap
    assert _tick(_MCP([_inst(IPOP)], MAJORS), dict(INPUTS, leverage=10))[0][0]["leverage"] == 5   # the 5x venue cap
    assert scoring.leverage_for(3, 5) == 3 and scoring.leverage_for(10, 5) == 5


def test_a_pasted_fraction_margin_becomes_a_percent_and_one_percent_stays_one():
    assert _tick(_MCP([_inst(IPOP)], MAJORS), dict(INPUTS, marginPct=0.4))[0][0]["marginPct"] == 40.0
    assert _tick(_MCP([_inst(IPOP)], MAJORS), dict(INPUTS, marginPct=1))[0][0]["marginPct"] == 1.0


def test_a_held_name_and_a_just_signaled_name_are_not_re_emitted():
    mcp = _MCP([_inst(IPOP)], MAJORS, held=(IPOP,))
    out, state = _tick(mcp)
    assert out == [] and state.last()["result"]["gate"] == "no_candidate"
    assert not [t for t, _ in mcp.calls if t == "market_get_asset_data"]      # a held name costs no candle read
    mcp, state = _MCP([_inst(IPOP)], MAJORS), _State()
    assert len(_tick(mcp, state=state)[0]) == 1
    out, _ = _tick(mcp, state=state)                                            # same state, inside the 240s TTL
    assert out == [] and state.last()["result"]["scanned"] == 0


def test_a_failed_read_degrades_the_tick_instead_of_crashing():
    insts = [_inst(IPOP)]
    out, state = _tick(_MCP(insts, MAJORS, fail=("market_list_instruments",)))
    assert out == [] and state.last()["result"]["gate"] == "no_universe"
    assert _tick(_MCP(insts, MAJORS, fail=("strategy_get_clearinghouse_state",)))[0] == []
    out, state = _tick(_MCP(insts, MAJORS, fail=("market_get_asset_data",)))
    assert out == [] and state.last()["result"]["gate"] == "no_candidate"
    out, _ = _tick(_MCP(insts, MAJORS, fail=("leaderboard_get_markets",)))
    assert len(out) == 1 and out[0]["data"]["score"] == 5                      # the board is not load-bearing: trend-only


# ── 1.2.0: a board outage is logged as one, not as "no leaders" ──────────────────────────

def test_a_board_outage_is_logged_as_an_outage_not_as_no_board_row(capsys):
    """Before 1.2.0 both cases returned (None, 0.0), scored trend-only and reached the signal as
    `sm_no_board_row` — so a dead board for a week read like the normal pre-IPO day."""
    insts = [_inst(IPOP)]
    out, _ = _tick(_MCP(insts, MAJORS))                                        # rows for the majors, none for the name
    err = capsys.readouterr().err
    assert out[0]["data"]["smDirection"] == "NONE" and "sm_no_board_row" in out[0]["data"]["reasons"]
    assert f"no 4h-board row for {IPOP}" in err and "UNREADABLE" not in err
    out, _ = _tick(_MCP(insts, MAJORS, fail=("leaderboard_get_markets",)))    # the read raises
    err = capsys.readouterr().err
    assert out[0]["data"]["score"] == 5, "an outage still scores trend-only, exactly as before"
    assert out[0]["data"]["smDirection"] == "UNREADABLE" and "sm_board_unreadable" in out[0]["data"]["reasons"]
    assert "sm_no_board_row" not in out[0]["data"]["reasons"]
    assert f"4h board UNREADABLE for {IPOP}" in err and "no 4h-board row" not in err
    # a board that is not a list of rows is an outage too, and the helper's own return says so
    assert scan._get_sm_direction(_Ctx(_MCP(insts, markets=None)), IPOP) == scan._BOARD_UNREADABLE
    assert scan._get_sm_direction(_Ctx(_MCP(insts, MAJORS)), IPOP) == (None, 0.0)
