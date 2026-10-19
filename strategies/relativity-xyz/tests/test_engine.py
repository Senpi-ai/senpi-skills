"""Relativity XYZ engine tests. Pure math + scan() smoke runs against a fake ctx (no network).
Run: python3 -m pytest strategies/relativity-xyz/tests -q
"""
import os
import sys
import types

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402

_RT = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
INPUTS = _RT["scanners"][1]["inputs"]
SCHEMA = _RT["scanners"][1]["signal_data_schema"]
MGN = float(INPUTS["marginPct"])


def _bars(n, start=100.0, step=0.0, hi=None, lo=None):
    """n bars walking by `step`. The LAST one is the in-progress bar, as the MCP serves it."""
    out = []
    px = start
    for i in range(n):
        out.append({"t": i, "T": i + 1, "o": px, "c": px,
                    "h": hi if hi is not None else px * 1.001,
                    "l": lo if lo is not None else px * 0.999, "v": 1000})
        px += step
    return out


def _up(n=10):      # higher lows ⇒ BULLISH
    return _bars(n, 100.0, 1.0)


def _down(n=10):    # lower highs ⇒ BEARISH
    return _bars(n, 100.0, -1.0)


def _flat(n=10):
    return _bars(n, 100.0, 0.0)


# ── the in-progress bar is dropped ────────────────────────────────────────────────────

def test_closed_bars_drops_the_bar_still_forming():
    """market_get_asset_data's last row is always the bar in progress (verified live: its
    `T` end-time sits in the future). A regime gate read off a price that is still moving
    flips back and forth inside one window, so it has to go."""
    c = _bars(5)
    assert scan.closed_bars(c) == c[:-1]
    assert len(scan.closed_bars(c)) == 4
    assert scan.closed_bars([]) == []
    assert scan.closed_bars([{"c": 1}]) == []          # one bar is only the open one
    assert scan.closed_bars(None) == []


# ── regime ────────────────────────────────────────────────────────────────────────────

def test_legs_follow_the_regime():
    assert scan.legs_for("risk_off") == ["short"]
    assert scan.legs_for("risk_on") == ["long"]
    assert scan.legs_for("neutral") == ["long", "short"]


def test_an_unreadable_index_is_neutral_not_a_guess():
    """No index read ⇒ both legs open and no shift boost. Never an invented direction."""
    ctx = types.SimpleNamespace(
        senpi_mcp=types.SimpleNamespace(call_tool=lambda n, a: None),
        state=None, wallet="0xw")
    r = scan.index_regime(ctx, INPUTS)
    assert r["regime"] == "neutral" and r["shift_detected"] is False


def test_regime_reads_the_closed_bar_so_a_moving_price_cannot_flip_it():
    """The in-progress bar is a crash; the last CLOSED pair is flat. Regime must be neutral."""
    closed_flat_then_crash = _flat(4) + [{"t": 9, "T": 10, "o": 100, "c": 50,
                                         "h": 100, "l": 50, "v": 1}]
    ctx = _ctx_tool({"market_get_asset_data": {"candles": {
        "4h": closed_flat_then_crash, "1h": _flat(4)}}})
    assert scan.index_regime(ctx, INPUTS)["regime"] == "neutral"


def test_shift_fires_when_the_closed_4h_turns_from_red_to_green():
    c4 = [{"t": 0, "T": 1, "o": 100, "c": 100, "h": 100, "l": 100, "v": 1},
          {"t": 1, "T": 2, "o": 100, "c": 90,  "h": 100, "l": 90,  "v": 1},   # red
          {"t": 2, "T": 3, "o": 90,  "c": 95,  "h": 95,  "l": 90,  "v": 1},   # green
          {"t": 3, "T": 4, "o": 95,  "c": 1,   "h": 95,  "l": 1,   "v": 1}]   # in progress
    ctx = _ctx_tool({"market_get_asset_data": {"candles": {"4h": c4, "1h": _flat(4)}}})
    r = scan.index_regime(ctx, INPUTS)
    assert r["shift_detected"] is True, r


def _ctx_tool(table):
    def call(name, args):
        v = table.get(name)
        return {"data": v} if v is not None else None
    return types.SimpleNamespace(senpi_mcp=types.SimpleNamespace(call_tool=call),
                                 state=None, wallet="0xw")


# ── hard gates ────────────────────────────────────────────────────────────────────────

def test_long_refuses_a_laggard_and_a_4h_downtrend():
    assert scoring.score_candidate("A", _up(), _up(), -1.0, 1.0, "long", INPUTS) is None
    assert scoring.score_candidate("A", _up(), _down(), 5.0, 1.0, "long", INPUTS) is None


def test_short_refuses_a_leader_and_a_4h_uptrend():
    assert scoring.score_candidate("A", _down(), _down(), 1.0, -1.0, "short", INPUTS) is None
    assert scoring.score_candidate("A", _down(), _up(), -5.0, -1.0, "short", INPUTS) is None


def test_too_little_history_is_nothing():
    assert scoring.score_candidate("A", _up(3), _up(), 5.0, 1.0, "long", INPUTS) is None
    assert scoring.score_candidate("A", _up(), _up(3), 5.0, 1.0, "long", INPUTS) is None


# ── leverage ──────────────────────────────────────────────────────────────────────────

def test_leverage_is_scaled_by_conviction():
    assert scoring.get_leverage_for_score(5) == 5
    assert scoring.get_leverage_for_score(8) == 7
    assert scoring.get_leverage_for_score(10) == 10
    assert scoring.get_leverage_for_score(99) == 10
    assert scoring.get_leverage_for_score(10, max_leverage=5) == 5   # capped by the input


def test_leverage_is_clamped_to_the_venue_cap():
    """A 10x request on a 5x asset is refused as 5x, never sent as 10x."""
    assert scoring.clamp_leverage(10, 5) == 5
    assert scoring.clamp_leverage(10, 20) == 10
    assert scoring.clamp_leverage(10, None) == 10
    assert scoring.clamp_leverage(10, 0) == 10
    assert scoring.clamp_leverage(10, "x") == 10
    assert scoring.clamp_leverage(1, 1) == 1


# ── the funding term is DEAD as shipped, and that is pinned on purpose ────────────────

def test_the_funding_squeeze_bonus_cannot_fire_at_the_shipped_threshold():
    """Measured on xyz:SP500 over 168 hourly rows (19 Oct 2026): max |funding| 0.0000523,
    median 0.0000063. The shipped gate is 0.005 — 96x the observed maximum — so the +1 is
    unreachable. The live fork carried it that way, so it ships that way; this test exists
    so that lowering it is a deliberate retune with a measurement behind it, never a quiet
    patch that moves every score."""
    assert float(INPUTS["fundingSqueezeThreshold"]) == 0.005
    realistic = -0.00005            # a fat real funding print
    a = scoring.score_candidate("A", _up(), _up(), 5.0, 1.0, "long", INPUTS,
                                asset_funding=realistic)
    b = scoring.score_candidate("A", _up(), _up(), 5.0, 1.0, "long", INPUTS,
                                asset_funding=0.0)
    assert a["score"] == b["score"]
    assert not any("funding_squeeze" in r for r in a["reasons"])
    # and it DOES fire once the threshold is where funding actually lives
    tuned = {**INPUTS, "fundingSqueezeThreshold": 0.00003}
    c = scoring.score_candidate("A", _up(), _up(), 5.0, 1.0, "long", tuned,
                                asset_funding=realistic)
    assert c["score"] == b["score"] + 1


def test_shift_boost_adds_two_on_longs():
    a = scoring.score_candidate("A", _up(), _up(), 5.0, 1.0, "long", INPUTS,
                                shift_detected=True)
    b = scoring.score_candidate("A", _up(), _up(), 5.0, 1.0, "long", INPUTS,
                                shift_detected=False)
    assert a["score"] == b["score"] + 2 and "shift_boost" in a["reasons"]


def test_rsi_blowoff_penalises_a_long():
    spike = _bars(20, 100.0, 5.0)                     # relentless up ⇒ RSI 100
    hot = scoring.score_candidate("A", spike, _up(), 5.0, 1.0, "long", INPUTS)
    assert hot["rsi"] > float(INPUTS["rsiOverbought"])
    assert any("rsi_blowoff" in r for r in hot["reasons"])


# ── universe ──────────────────────────────────────────────────────────────────────────

def _inst(name, vol_m=50.0, prev=100.0, mark=110.0, delisted=False, lev=10):
    return {"name": name, "is_delisted": delisted, "max_leverage": lev,
            "context": {"dayNtlVlm": str(vol_m * 1e6), "prevDayPx": str(prev),
                        "markPx": str(mark)}}


def test_universe_takes_xyz_only_and_never_the_regime_proxy():
    """A book whose gate is also a position is reading its own exposure."""
    rows = [_inst("xyz:AAA"), _inst("xyz:SP500"), _inst("BTC"), _inst("xyz:DEAD", delisted=True),
            _inst("xyz:THIN", vol_m=0.5)]
    names = [u["name"] for u in scan.build_universe(rows, INPUTS, "xyz:SP500")]
    assert names == ["xyz:AAA"], names


def test_universe_is_ranked_by_volume_and_capped():
    rows = [_inst("xyz:%d" % i, vol_m=float(i)) for i in range(1, 12)]
    got = scan.build_universe(rows, {**INPUTS, "universeMaxNames": 3, "minVolumeUsdM": 0.0,
                                     "volFloorPctOfMedian": 0.0}, "xyz:SP500")
    assert [u["name"] for u in got] == ["xyz:11", "xyz:10", "xyz:9"]


def test_excess_is_measured_against_the_universe_mean():
    u = [{"name": "a", "prev": 100.0, "mark": 110.0},     # +10%
         {"name": "b", "prev": 100.0, "mark": 100.0},     #   0%
         {"name": "c", "prev": 100.0, "mark": 90.0}]      # -10%
    mean = scan.rank(u)
    assert abs(mean) < 1e-9
    assert [round(x["excess"], 6) for x in u] == [10.0, 0.0, -10.0]


# ── recent-signal TTL ─────────────────────────────────────────────────────────────────

def test_recent_signals_expire_on_the_ttl():
    """The fork read `recentSignalTtlSeconds` and never used it: names went into a set that
    was truncated with list(set)[-50:], and set ordering is arbitrary — so which names
    stayed suppressed changed tick to tick for no reason."""
    now = 1_000_000.0
    live = scan.live_recent({"recent_signals": {"xyz:OLD": now - 999, "xyz:NEW": now - 10}},
                            now, 180)
    assert set(live) == {"xyz:NEW"}


def test_the_forks_list_shaped_state_migrates_without_crashing():
    assert scan.live_recent({"recent_signals": ["xyz:A", "xyz:B"]}, 1.0, 180) == {}
    assert scan.live_recent(None, 1.0, 180) == {}
    assert scan.live_recent({"recent_signals": {"xyz:A": "nonsense"}}, 1.0, 180) == {}


# ── scan() against a fake venue ───────────────────────────────────────────────────────

class _State:
    def __init__(self):
        self.h = []

    def last(self):
        return self.h[-1] if self.h else None

    def append(self, x):
        self.h.append(x)


class _Mcp:
    """Two xyz names: LEAD runs, LAG falls. The index is flat ⇒ neutral ⇒ both legs."""
    def __init__(self, account=10_000.0, held=(), lev=10):
        self.account, self.held, self.lev = account, list(held), lev
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append(name)
        if name == "strategy_get_clearinghouse_state":
            used = 100.0 * len(self.held)
            return {"data": {"main": {
                "marginSummary": {"accountValue": str(self.account),
                                  "totalMarginUsed": str(used)},
                "withdrawable": str(self.account - used),
                "assetPositions": [{"position": {"coin": c, "szi": "1.0"}}
                                   for c in self.held]}}}
        if name == "market_list_instruments":
            return {"data": {"instruments": [
                _inst("xyz:LEAD", vol_m=80.0, prev=100.0, mark=112.0, lev=self.lev),
                _inst("xyz:LAG", vol_m=70.0, prev=100.0, mark=88.0, lev=self.lev),
                _inst("xyz:SP500", vol_m=99.0)]}}
        if name == "market_get_asset_data":
            a = args["asset"]
            if a == "xyz:SP500":
                return {"data": {"candles": {"4h": _flat(6), "1h": _flat(6)}}}
            bars = _up(14) if a == "xyz:LEAD" else _down(14)
            return {"data": {"candles": {"1h": bars, "4h": bars},
                             "asset_context": {"funding": "0.0"}}}
        return None


def _ctx(account=10_000.0, held=(), state=None, lev=10):
    return types.SimpleNamespace(senpi_mcp=_Mcp(account, held, lev),
                                 state=state if state is not None else _State(),
                                 wallet="0xwallet")


def test_scan_opens_both_sides_in_a_neutral_regime():
    out = scan.scan(INPUTS, _ctx())
    got = {o["asset"]: o["direction"] for o in out}
    assert got == {"xyz:LEAD": "LONG", "xyz:LAG": "SHORT"}, got
    for o in out:
        assert o["marginPct"] == MGN and o["leverage"] >= 5
        assert set(o["data"]) <= set(SCHEMA)
        assert all(v is not None for v in o["data"].values())


def test_scan_stands_down_when_the_clearinghouse_is_unreadable():
    """None and {} are opposite facts: unreadable must open nothing rather than double-open."""
    ctx = _ctx()
    real = ctx.senpi_mcp.call_tool
    ctx.senpi_mcp.call_tool = lambda n, a: (
        None if n == "strategy_get_clearinghouse_state" else real(n, a))
    assert scan.scan(INPUTS, ctx) == []


def test_scan_never_exceeds_its_slots():
    assert scan.scan(INPUTS, _ctx(held=("A", "B", "C", "D"))) == []


def test_scan_skips_a_name_already_held():
    out = scan.scan(INPUTS, _ctx(held=("xyz:LEAD",)))
    assert [o["asset"] for o in out] == ["xyz:LAG"]


def test_scan_respects_the_free_margin_gate():
    """A clip under the venue minimum emits nothing — the order would just fail."""
    tiny = 12.0 / (MGN / 100.0) / 10.0 * 0.5     # a clip worth under $12 even at 10x
    assert scan.scan(INPUTS, _ctx(account=tiny)) == []


def test_scan_suppresses_a_name_it_just_signalled():
    st = _State()
    first = scan.scan(INPUTS, _ctx(state=st))
    assert first, "nothing emitted on the first tick"
    again = scan.scan(INPUTS, _ctx(state=st))
    assert again == [], again


def test_leverage_reaching_the_order_is_clamped_to_the_venue_cap():
    out = scan.scan(INPUTS, _ctx(lev=3))
    assert out and all(o["leverage"] <= 3 for o in out), out


# ── config guards ─────────────────────────────────────────────────────────────────────

def test_the_entry_action_spells_the_taker_fallback_correctly():
    """The fork wrote `ensure_execution_asTaker`, which the runtime does not read — entries
    rested with no taker fallback and a missed fill was silent. camelCase here is a typo
    that costs fills, so it is pinned."""
    entry = [a for a in _RT["actions"] if a["action_type"] == "OPEN_POSITION"][0]
    opts = entry["params"]["fee_optimized_limit_options"]
    assert opts["ensure_execution_as_taker"] is True
    assert "ensure_execution_asTaker" not in opts


def test_no_time_cut_fires_inside_the_horizon_this_template_claims():
    """intraday, and the DSL owns every exit — no clock may close a position."""
    p = _RT["exit"]["dsl_preset"]
    assert p["hard_timeout"]["enabled"] is False
    assert p["weak_peak_cut"]["enabled"] is False
    assert p["dead_weight_cut"]["enabled"] is False


def test_the_daily_entry_cap_exceeds_the_slot_count():
    """A cap at or below the slots means the book fills once and every re-entry after a
    stop-out waits for 00:00 UTC — the strategy reads as dead for the rest of the day."""
    assert _RT["risk"]["guard_rails"]["max_entries_per_day"] > _RT["strategy"]["slots"]
