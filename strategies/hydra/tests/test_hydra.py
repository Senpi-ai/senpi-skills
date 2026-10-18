"""Hydra 1.3.0: three sleeves, one wallet each — pinned.

12 Oct 2026 template audit: the core's apex leverage tier needed a score of 7 on a composite that
tops out at 6 (4h+1d 3, 1h 2, funding 1), so the catalog's 5x was never emitted; the card said the
core never bets against the thesis coin while the code shorted a bearish 4h; structure, RSI,
drawdown and ATR read the bar in progress in every sleeve. These tests pin the retuned numbers (core
1 x 40% at 3x on a 15% ROE stop = 5.0% of price; dip 1 x 30% at 3x on 12% ROE = 4.0%; hedge up to
3 x 15% = 45% of its wallet at 3x on 10% ROE = 3.3%; no clock cuts, no day-rollover breaker reset),
the LONG-only default, the closed-candle reads, and one fake-ctx tick per sleeve.

Run: python3 -B -m pytest strategies/hydra/tests -q
"""
import importlib
import os
import sys
import time
import types

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))
MANIFEST = yaml.safe_load(open(os.path.join(PKG, "strategy.yaml"), encoding="utf-8"))
NOW = float(int(time.time()))


def _load(sleeve):
    """Each sleeve ships its own `scan.py` + `scoring.py` under the same module names, so import them
    with that sleeve's scanners dir first on sys.path and the names cleared — the way the runtime
    launches each scanner in its own process (`sys.path.insert(0, <scanners dir>)`)."""
    d = os.path.join(PKG, sleeve, "scanners")
    sys.path.insert(0, d)
    for name in ("scan", "scoring"):
        sys.modules.pop(name, None)
    try:
        scoring = importlib.import_module("scoring")
        scan = importlib.import_module("scan")
    finally:
        sys.path.remove(d)
        for name in ("scan", "scoring"):
            sys.modules.pop(name, None)
    rt = yaml.safe_load(open(os.path.join(PKG, sleeve, "runtime.yaml"), encoding="utf-8"))
    sig = next(s for s in rt["scanners"] if s["type"] == "external_scanner")
    return types.SimpleNamespace(scan=scan, scoring=scoring, rt=rt, sig=sig, inputs=dict(sig["inputs"]))


CORE, DIP, HEDGE = _load("core"), _load("dip"), _load("hedge")
SLEEVES = {"core": CORE, "dip": DIP, "hedge": HEDGE}


# ── candles in the feed's shape (t/T epoch ms, o/h/l/c/v strings), oldest first ─────────────

def _bars(closes, step, pad=0.3, absurd=False, now=NOW):
    """Closed rows: the last one closed a second before `now`. `absurd` appends the bar in progress
    (opened ten minutes ago) with a 50x close and high and a /50 low, so any reader that includes it
    flips: the RSI to ~100, the 6-bar drawdown to 0, the ATR to the moon, the last low to nothing."""
    n = len(closes)
    rows = []
    for i, c in enumerate(closes):
        t = int((now - (n - i) * step - 1) * 1000)
        rows.append({"t": t, "T": t + step * 1000, "s": "X", "i": "x", "o": f"{c}", "h": f"{c + pad}",
                     "l": f"{c - pad}", "c": f"{c}", "v": "1", "n": 1})
    if absurd:
        t = int((now - 600) * 1000)
        last = closes[-1]
        rows.append({"t": t, "T": t + step * 1000, "s": "X", "i": "x", "o": f"{last}", "h": f"{last * 50}",
                     "l": f"{last / 50}", "c": f"{last * 50}", "v": "1", "n": 1})
    return rows


def _zigzag(n=20, start=100.0, up=2.0, down=1.0):
    """+up, -down alternating, the last step +up: higher lows on 3 of the last 5 steps = BULLISH
    structure, RSI(14) = 100 - 100/(1 + 14/7) = 66.7. Negate both for the bearish mirror (RSI 33.3)."""
    out = [start]
    for i in range(1, n):
        out.append(out[-1] + (up if i % 2 else -down))
    return out


def _line(n=8, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def _tape(side, absurd=False):
    """1h/4h/1d candles for one confirmed tape. BULL = rising 4h and 1d lows with a bullish 1h zigzag
    (RSI 67). BEAR = falling 4h/1d (-2 a bar: a 10.4% six-bar drawdown, above stressDropPct 8) with a
    bearish 1h zigzag (RSI 33). DIP = rising 4h/1d with the bearish 1h zigzag — a pullback (RSI 33 <=
    dipRsiMax 42) inside the uptrend."""
    if side == "BULL":
        h1, hs = _zigzag(), _line()
    elif side == "BEAR":
        h1, hs = _zigzag(up=-2.0, down=-1.0), _line(step=-2.0)
    else:
        h1, hs = _zigzag(up=-2.0, down=-1.0), _line()
    return {"1h": _bars(h1, 3600, absurd=absurd), "4h": _bars(hs, 14400, absurd=absurd),
            "1d": _bars(hs, 86400, absurd=absurd)}


def _three(tape):
    return tape["1h"], tape["4h"], tape["1d"]


# ── a fake ctx: the two reads every sleeve makes ─────────────────────────────────────────────

CH = {"data": {"main": {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": "0", "totalNtlPos": "0"},
                        "assetPositions": []}}}


class _State:
    def __init__(self, last=None):
        self.rows = [last] if last else []

    def __len__(self):
        return len(self.rows)

    def last(self):
        return self.rows[-1] if self.rows else None

    def append(self, d):
        self.rows.append(d)


class _MCP:
    """One clearinghouse view plus market_get_asset_data per asset (`tapes` maps asset -> candles).
    `fail` names the tools that raise, the way a flaky MCP does."""
    def __init__(self, tapes, fail=(), funding="-0.0001"):
        self.tapes, self.fail, self.funding, self.calls = tapes, set(fail), funding, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool in self.fail:
            raise RuntimeError(f"{tool} unavailable")
        if tool == "strategy_get_clearinghouse_state":
            return CH
        assert tool == "market_get_asset_data", tool
        return {"data": {"candles": self.tapes.get(args["asset"], {}), "asset_context": {"funding": self.funding}}}


class _Ctx:
    def __init__(self, sleeve, tapes, fail=(), funding="-0.0001", last=None):
        self.wallet = "0x" + "b" * 40
        self.senpi_mcp = _MCP(tapes, fail, funding)
        self.state = _State(last)
        self.scanner_name = sleeve.sig["name"]
        self.interval_seconds = sleeve.sig["interval_seconds"]
        self.dry_run = False


def _tick(sleeve, tapes, inputs=None, **kw):
    ctx = _Ctx(sleeve, tapes, **kw)
    out = sleeve.scan.scan(dict(inputs or sleeve.inputs), ctx)
    return out, (ctx.state.last() or {}).get("result") or {}, ctx


# ── the shipped numbers ───────────────────────────────────────────────────────────────────────

STOPS = {"core": (15.0, 5.0), "dip": (12.0, 4.0), "hedge": (10.0, 10.0 / 3)}   # (ROE %, % of price at 3x)


def test_version_leverage_cap_and_slot_count():
    assert MANIFEST["version"] == "1.3.0"
    assert MANIFEST["catalog"]["leverage_max"] == 3
    assert MANIFEST["catalog"]["max_slots"] == sum(s.rt["strategy"]["slots"] for s in SLEEVES.values()) == 5
    assert [i["name"] for i in MANIFEST["instances"]] == ["core", "dip", "hedge"]


@pytest.mark.parametrize("name", sorted(SLEEVES))
def test_the_stop_is_the_only_loss_exit_and_there_is_no_clock(name):
    s = SLEEVES[name]
    pre = s.rt["exit"]["dsl_preset"]
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, (name, cut)
    assert pre["phase1"]["enabled"] is False
    roe, px = STOPS[name]
    assert pre["phase1"]["max_loss_pct"] == roe
    assert abs(pre["phase1"]["max_loss_pct"] / s.inputs["leverage"] - px) < 1e-9
    tiers = pre["phase2"]["tiers"]
    assert pre["phase2"]["enabled"] and tiers[0]["lock_hw_pct"] == 40
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


@pytest.mark.parametrize("name", sorted(SLEEVES))
def test_leverage_is_three_everywhere_and_the_rails_sit_above_the_slots(name):
    s = SLEEVES[name]
    assert s.inputs["leverage"] == s.rt["strategy"]["default_leverage"] == MANIFEST["catalog"]["leverage_max"] == 3
    for stale in ("maxLeverage", "stdLeverage", "apexScore"):
        assert stale not in s.inputs, (name, stale)
    rails = s.rt["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > s.rt["strategy"]["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False
    assert s.sig["timeout_seconds"] < s.sig["interval_seconds"] == 300


def test_core_and_dip_size_one_slot_each():
    assert CORE.rt["strategy"]["slots"] == DIP.rt["strategy"]["slots"] == 1
    assert CORE.inputs["marginPct"] == CORE.rt["strategy"]["margin_pct"] == 40
    assert DIP.inputs["marginPct"] == DIP.rt["strategy"]["margin_pct"] == 30
    assert CORE.inputs["allowShort"] is False


def test_the_hedge_basket_cannot_exceed_forty_five_percent_of_its_wallet():
    strat, inp = HEDGE.rt["strategy"], HEDGE.inputs
    assert strat["slots"] == inp["maxSlots"] == 3
    assert round(inp["maxMarginPct"] * 100) == strat["margin_pct"] == 15      # the clamp hi, a fraction of equity
    assert strat["slots"] * strat["margin_pct"] <= round(inp["hedgeMaxTotalPct"] * 100) == 45
    assert 0 < inp["minMarginPct"] < inp["maxMarginPct"] < 1.0
    sc = HEDGE.scoring
    assert sc.vol_parity_margin(1000.0, 0.3, inp, mult=2.0) == 150.0           # a calm name under thesis stress: the 15% cap
    assert sc.vol_parity_margin(1000.0, 100.0, inp) == 20.0                     # a wild name: the 2% floor


# ── the composite and the LONG-only default (pure) ────────────────────────────────────────────

def test_the_composite_tops_out_at_six_and_emits_one_leverage():
    th = CORE.scoring.score_core(*_three(_tape("BULL")), {"funding": "-0.0001"}, CORE.inputs, NOW)
    assert th["direction"] == "LONG" and th["score"] == 6 and th["leverage"] == 3
    assert th["reasons"] == ["4h_bullish_100%_1d_bullish", "1h_confirms_bullish", "funding_pays_long"]
    assert th["score"] >= CORE.inputs["minScore"]


def test_a_bearish_tape_stands_down_unless_shorts_are_allowed():
    """The card: the core never bets against the thesis coin. Before 1.3.0 a bearish 4h+1d emitted a
    SHORT at the same score; now it does nothing unless allowShort is set."""
    args = (*_three(_tape("BEAR")), {"funding": "0.0001"})
    assert CORE.scoring.score_core(*args, CORE.inputs, NOW) is None
    th = CORE.scoring.score_core(*args, dict(CORE.inputs, allowShort=True), NOW)
    assert th["direction"] == "SHORT" and th["score"] == 6 and th["leverage"] == 3
    out, rec, _ = _tick(CORE, {"ETH": _tape("BEAR")}, funding="0.0001")
    assert out == [] and rec["gate"] == "blocked"
    out, _, _ = _tick(CORE, {"ETH": _tape("BEAR")}, dict(CORE.inputs, allowShort=True), funding="0.0001")
    assert [o["direction"] for o in out] == ["SHORT"]


# ── closed candles only ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(SLEEVES))
def test_closed_candles_drops_the_bar_in_progress_and_unreadable_rows(name):
    sc = SLEEVES[name].scoring
    rows = _bars(_line(), 14400, absurd=True)
    closed = sc.closed_candles(rows + [{"t": "x"}, {}, [None, 1, 2, 3, 4]], 14400, NOW)
    assert closed == rows[:-1]                                     # the last row opened ten minutes ago
    assert sc.closed_candles(None, 14400, NOW) == []


def test_core_reads_closed_candles_only():
    """An in-progress bar with a 50x close pushes the 1h RSI to 100 (a 'blowoff' penalty) and its /50
    low breaks the 1h structure (the 1h no longer confirms): measured on the 1.2.0 reader, the composite
    went from 6 to 2, under minScore 5. The read must not move."""
    a = CORE.scoring.score_core(*_three(_tape("BULL")), {"funding": "-0.0001"}, CORE.inputs, NOW)
    b = CORE.scoring.score_core(*_three(_tape("BULL", absurd=True)), {"funding": "-0.0001"}, CORE.inputs, NOW)
    assert a == b and a["score"] == 6


def test_dip_reads_closed_candles_only():
    """The absurd bar lifts the 1h RSI from 33 to ~100: no pullback, no entry — unless it is ignored."""
    a = DIP.scoring.score_dip(*_three(_tape("DIP")), DIP.inputs, NOW)
    b = DIP.scoring.score_dip(*_three(_tape("DIP", absurd=True)), DIP.inputs, NOW)
    assert a == b and a["direction"] == "LONG" and a["score"] == 6 and a["leverage"] == 3


def test_hedge_reads_closed_candles_only():
    """The absurd bar zeroes the 6-bar drawdown (its close becomes the peak), blows the ATR up (the
    vol-parity size collapses to the floor) and resets the thesis-stress multiplier to 1."""
    sc = HEDGE.scoring
    bear, bear_x = _tape("BEAR"), _tape("BEAR", absurd=True)
    a = sc.score_hedge_one("BTC", bear["1h"], bear["4h"], HEDGE.inputs, NOW)
    b = sc.score_hedge_one("BTC", bear_x["1h"], bear_x["4h"], HEDGE.inputs, NOW)
    assert a == b and a["score"] == 6 and a["direction"] == "SHORT"
    assert sc.thesis_stress_from_candles(bear["4h"], HEDGE.inputs, NOW) == \
        sc.thesis_stress_from_candles(bear_x["4h"], HEDGE.inputs, NOW) == (2.0, 10.42)


# ── one tick through each sleeve (fake ctx) ───────────────────────────────────────────────────

def _assert_envelope(sleeve, sig, margin_pct=None):
    schema = sleeve.sig["signal_data_schema"]
    assert set(sig["data"]) <= set(schema), set(sig["data"]) - set(schema)
    assert all(v is not None for v in sig["data"].values())           # optional keys are absent, never null
    assert all(k in sig["data"] for k, v in schema.items() if v.get("required", True))
    assert sig["leverage"] == sig["data"]["leverage"] == 3
    assert 0 < sig["marginPct"] <= 100
    if margin_pct is not None:
        assert sig["marginPct"] == margin_pct


def test_core_tick_emits_the_long_at_forty_percent_and_three_x():
    out, rec, ctx = _tick(CORE, {"ETH": _tape("BULL")})
    assert len(out) == 1 and out[0]["asset"] == "ETH" and out[0]["direction"] == "LONG"
    _assert_envelope(CORE, out[0], 40.0)
    assert rec["gate"] == "pass" and rec["score"] == 6
    assert [t for t, _ in ctx.senpi_mcp.calls] == ["strategy_get_clearinghouse_state", "market_get_asset_data"]


def test_dip_tick_emits_the_pullback_buy_at_thirty_percent():
    out, rec, _ = _tick(DIP, {"ETH": _tape("DIP")})
    assert len(out) == 1 and out[0]["asset"] == "ETH" and out[0]["direction"] == "LONG"
    _assert_envelope(DIP, out[0], 30.0)
    assert rec["gate"] == "pass"
    out, rec, _ = _tick(DIP, {"ETH": _tape("BULL")})                          # no pullback (RSI 67): nothing
    assert out == [] and rec["gate"] == "blocked"


def test_hedge_tick_shorts_the_breaking_name_never_the_thesis_coin():
    tapes = {"ETH": _tape("BEAR"), "BTC": _tape("BEAR"), "SOL": _tape("BULL")}
    out, rec, ctx = _tick(HEDGE, tapes, dict(HEDGE.inputs, hedgeUniverse=["BTC", "ETH", "SOL"]))
    assert [(o["asset"], o["direction"]) for o in out] == [("BTC", "SHORT")]
    _assert_envelope(HEDGE, out[0])
    assert out[0]["data"]["hedgeFor"] == "ETH" and out[0]["data"]["stressMult"] == 2.0   # the thesis coin is breaking too
    assert rec["emitted"] == 1 and rec["scanned"] == 2
    blend_reads = [a["asset"] for t, a in ctx.senpi_mcp.calls if t == "market_get_asset_data" and "1h" in a["candle_intervals"]]
    assert blend_reads == ["BTC", "SOL"]                                       # ETH is read for stress only, never scored


def test_hedge_basket_stops_at_three_slots_and_forty_five_percent():
    """Five names breaking down, all calm enough that vol-parity hits the 15% cap: the tick emits
    three shorts at 15% each (45% of the wallet) and stops — the slot ceiling and the total cap agree."""
    calm = {"1h": _bars(_zigzag(up=-0.2, down=-0.1), 3600, pad=0.02),
            "4h": _bars(_line(step=-0.3), 14400, pad=0.02), "1d": []}
    names = ["BTC", "SOL", "HYPE", "BNB", "XRP"]
    tapes = dict({n: calm for n in names}, ETH=_tape("BULL"))
    out, rec, _ = _tick(HEDGE, tapes, dict(HEDGE.inputs, hedgeUniverse=names + ["ETH"]))
    assert [o["marginPct"] for o in out] == [15.0, 15.0, 15.0]
    assert sum(o["marginPct"] for o in out) <= 45.0
    assert rec["candidates"] == 5 and rec["emitted"] == 3


@pytest.mark.parametrize("name,tool", [(n, t) for n in sorted(SLEEVES)
                                       for t in ("strategy_get_clearinghouse_state", "market_get_asset_data")])
def test_a_failing_read_degrades_the_tick_instead_of_crashing(name, tool):
    s = SLEEVES[name]
    tapes = {"ETH": _tape("BULL" if name != "dip" else "DIP"), "BTC": _tape("BEAR")}
    inputs = dict(s.inputs, hedgeUniverse=["BTC", "ETH"]) if name == "hedge" else s.inputs
    assert _tick(s, tapes, inputs)[0], "the happy path must emit, or the degrade test proves nothing"
    out, rec, ctx = _tick(s, tapes, inputs, fail=(tool,))
    assert out == [] and len(ctx.state) == 1 and rec.get("emitted") in (False, 0)
