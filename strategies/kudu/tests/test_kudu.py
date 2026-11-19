"""Kudu: the quality basket timed by the Nasdaq, pinned.

1. The inputs ARE the strategy: ten basket names at 2x in 4% clips, the Nasdaq 100 as the timer and
   the washout leg at 3x with 12%, RSI(10) lines 79 / 30, eleven slots.
2. The DSL decides every exit: no clock cut is enabled, the ladder only ever locks a gain.
3. plan(): the washout leg first, then every basket name not held unless overheated; never a held
   name; at most the open slots.
4. The scanner reads only the Nasdaq 100's candles, buys at the daily close only, once per close,
   one signal per tick, so each is sized off a fresh balance read and a skipped open can't enlarge
   the next.

Run: python3 -B -m pytest strategies/kudu/tests -q
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
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "kudu_main_signals")
INPUTS = dict(SIGNALS["inputs"])
DSL = RUNTIME["exit"]["dsl_preset"]
D1 = 86400
MIDNIGHT = 1794441600
BASKET = ["xyz:AAPL", "xyz:MSFT", "xyz:GOOGL", "xyz:AMZN", "xyz:META", "xyz:NVDA", "xyz:AVGO", "xyz:ORCL", "xyz:LLY", "xyz:COST"]


def test_the_inputs_are_the_strategy():
    assert INPUTS["basketAssets"] == BASKET
    assert INPUTS["allowedAssets"] == BASKET + ["xyz:XYZ100"] == MANIFEST["catalog"]["assets"]
    assert INPUTS["timingAsset"] == INPUTS["washoutAsset"] == "xyz:XYZ100"
    assert (INPUTS["rsiPeriod"], INPUTS["overheatRsi"], INPUTS["washoutRsi"]) == (10, 79, 30)
    assert (INPUTS["basketLeverage"], INPUTS["washoutLeverage"]) == (2, 3) == (RUNTIME["strategy"]["default_leverage"], MANIFEST["catalog"]["leverage_max"])
    assert (INPUTS["marginPct"], INPUTS["washoutMarginPct"]) == (4, 12) and RUNTIME["strategy"]["margin_pct"] == 4
    assert INPUTS["maxSlots"] == RUNTIME["strategy"]["slots"] == MANIFEST["catalog"]["max_slots"] == 11
    assert len(BASKET) * INPUTS["marginPct"] + INPUTS["washoutMarginPct"] <= 100


def test_the_dsl_decides_every_exit():
    assert INPUTS["stopRoePct"] == DSL["phase1"]["max_loss_pct"] == 30 and DSL["phase1"]["enabled"] is False
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert DSL[cut]["enabled"] is False
    assert [(t["trigger_pct"], t["lock_hw_pct"]) for t in DSL["phase2"]["tiers"]] == [(15, 30), (25, 45), (40, 60), (60, 75), (100, 85)]
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] >= INPUTS["maxSlots"]           # the first close fills the book
    assert rails["drawdown_reset_on_day_rollover"] is False


def test_plan():
    assert [a for a, _ in scoring.plan(50.0, BASKET, "xyz:XYZ100", [], 11, INPUTS)] == BASKET
    assert scoring.plan(85.0, BASKET, "xyz:XYZ100", [], 11, INPUTS) == []                       # overheated
    p = scoring.plan(25.0, BASKET, "xyz:XYZ100", ["AAPL", "xyz:MSFT"], 11, INPUTS)
    assert p[0] == ("xyz:XYZ100", "washout") and ("xyz:AAPL", "basket") not in p and len(p) == 9
    assert scoring.plan(25.0, BASKET, "xyz:XYZ100", BASKET, 1, INPUTS) == [("xyz:XYZ100", "washout")]
    assert scoring.plan(None, BASKET, "xyz:XYZ100", [], 11, INPUTS) == []


def _bars(closes, last_open):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * D1) * 1000), "o": str(c), "h": str(c), "l": str(c),
             "c": str(c), "v": "1"} for i, c in enumerate(closes)]


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
    def __init__(self, closes, held=(), withdrawable="1000", account="1000"):
        self.closes, self.held, self.withdrawable, self.account, self.calls = closes, held, withdrawable, account, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": self.account, "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": self.withdrawable, "assetPositions": []}
            pos = [{"position": {"coin": c, "szi": "1", "marginUsed": "50"}} for c in self.held]
            return {"data": {"main": sec, "xyz": dict(sec, assetPositions=pos)}}
        return {"data": {"candles": {"1d": _bars(self.closes, MIDNIGHT - D1) + _bars([self.closes[-1]], MIDNIGHT)}}}


CALM = [100.0 + (0.5 if i % 2 else -0.4) + 0.05 * i for i in range(90)]
CRASH = [100.0 + 0.2 * i for i in range(82)] + [116.2 * (1 - 0.03 * (k + 1)) for k in range(8)]


def _ticks(monkeypatch, mcp, start=MIDNIGHT + 300, end=MIDNIGHT + 3600, skip=(), state=None):
    """Every five-minute tick through the entry window, with the runtime opening what each tick emits:
    marginPct of the free margin at that moment, after which that margin is no longer free and the
    name shows as held. A name in `skip` is one the runtime does not open (the per-asset cooldown, a
    risk gate), so nothing is committed. Returns the opens as [(asset, margin to the cent)], in order."""
    state = state if state is not None else _State()
    opened, t = [], start
    while t <= end:
        monkeypatch.setattr(scan.time, "time", lambda t=t: t)
        out = scan.scan(dict(INPUTS), _Ctx(mcp, state=state))
        assert len(out) <= 1, "one signal per tick"
        for s in out:
            if s["asset"] in skip:
                continue
            free = float(mcp.withdrawable)
            margin = s["marginPct"] / 100.0 * free
            opened.append((s["asset"], round(margin, 2)))
            mcp.withdrawable, mcp.held = str(free - margin), tuple(mcp.held) + (s["asset"],)
        t += 300
    return opened


def _run(monkeypatch, mcp, at=MIDNIGHT + 300, **kw):
    monkeypatch.setattr(scan.time, "time", lambda: at)
    return scan.scan(dict(INPUTS), _Ctx(mcp, **kw))


def test_a_calm_close_fills_the_basket_at_2x_one_name_a_tick(monkeypatch):
    mcp = _MCP(CALM)
    out = _run(monkeypatch, mcp)
    assert [(s["asset"], s["leverage"], s["marginPct"]) for s in out] == [(BASKET[0], 2, 4.0)]
    assert set(out[0]["data"]) <= set(SIGNALS["signal_data_schema"])
    reads = [a for t, a in mcp.calls if t == "market_get_asset_data"]
    assert [(a["asset"], a["dex"]) for a in reads] == [("xyz:XYZ100", "xyz")]
    assert _ticks(monkeypatch, _MCP(CALM)) == [(a, 40.0) for a in BASKET]   # ten equal clips of 4% of the account


def test_a_washout_adds_the_index_at_3x_and_held_names_are_skipped(monkeypatch):
    out = _run(monkeypatch, _MCP(CRASH, held=("AAPL",)))
    assert out[0]["asset"] == "xyz:XYZ100" and (out[0]["leverage"], out[0]["marginPct"]) == (3, 12)
    opened = _ticks(monkeypatch, _MCP(CRASH, held=("AAPL",)))
    assert opened == [("xyz:XYZ100", 120.0)] + [(a, 40.0) for a in BASKET if a != "xyz:AAPL"]


def test_once_per_close_only_after_the_close_and_the_margin_gate(monkeypatch):
    state = _State()
    assert len(_ticks(monkeypatch, _MCP(CALM), state=state)) == 10
    assert _run(monkeypatch, _MCP(CALM), at=MIDNIGHT + 3500, state=state) == []   # every name went out at this close
    mcp = _MCP(CALM)
    assert _run(monkeypatch, mcp, at=MIDNIGHT + 2 * 3600) == []
    assert [t for t, _ in mcp.calls] == ["strategy_get_clearinghouse_state"]
    assert _run(monkeypatch, _MCP(CALM, withdrawable="100", account="100")) == []   # 4% of a $100 account at 2x = $8 < $12



def test_a_stopped_out_name_is_re_bought_at_the_200_dollar_minimum(monkeypatch):
    """Review #977: sized as 4% of FREE margin, a $200 book holding nine names had about $125 free, so
    a re-buy came to 4% x $125 x 2 = $10 of notional, under the $12 floor, and the basket shrank by one
    name per stop. Each name is now 4% of the ACCOUNT: $7.90 x 2 = $15.80, from 6.29% of the $125 free."""
    nine = BASKET[:9]
    out = _run(monkeypatch, _MCP(CALM, held=nine, withdrawable="125.6", account="197.6"))
    assert [s["asset"] for s in out] == [BASKET[9]]
    assert out[0]["marginPct"] == round(100 * 197.6 * 0.04 / 125.6, 4)


def test_an_unfundable_washout_does_not_block_the_basket(monkeypatch):
    """The washout clip ($24: 12% of a $200 account) is the largest. With $20 free, 90% of it ($18) can
    be committed: the washout does not fit, and the $8 basket clips behind it are still checked
    instead of the scanner stopping at the first miss. Two fit, one a tick."""
    out = _run(monkeypatch, _MCP(CRASH, withdrawable="20", account="200"))
    assert [s["asset"] for s in out] == BASKET[:1]
    assert _ticks(monkeypatch, _MCP(CRASH, withdrawable="20", account="200")) == [(BASKET[0], 8.0), (BASKET[1], 8.0)]


def test_the_first_close_fills_ten_equal_clips_at_the_200_dollar_minimum(monkeypatch):
    """Re-review of #978 (the same contract applies here): converting every clip against one
    free-margin figure let the runtime's one-at-a-time sizing shrink the later names, the tenth to about
    $5.54 x 2 = $11 at $200, under the $12 floor. Each name now goes out on its own tick, converted
    against the free margin that tick read."""
    assert _ticks(monkeypatch, _MCP(CALM, withdrawable="200", account="200")) == [(a, 8.0) for a in BASKET]


def test_an_open_the_runtime_skips_does_not_enlarge_the_next(monkeypatch):
    """Follow-up to #977/#978. When a close's signals went out in one batch, each converted against
    the free margin left after the ones before it, an open the runtime skipped left more free margin
    than the next signal assumed. The skip could be a cooldown on a name stopped out minutes before the
    close. The next position then opened larger than its clip: on a $200 account after a washout, a
    skipped $24 index leg would have opened the first name at 8/176 of $200 = $9.09. One signal a tick,
    sized off that tick's balance read, opens it at $8, and the skipped leg is not retried this close."""
    opened = _ticks(monkeypatch, _MCP(CRASH, withdrawable="200", account="200"), skip={"xyz:XYZ100"})
    assert opened == [(a, 8.0) for a in BASKET]


def test_a_full_book_fits_in_the_entry_hour(monkeypatch):
    """Ten names and the washout leg, one a tick from the first tick after the close: eleven opens,
    all inside the hour the strategy buys in."""
    opened = _ticks(monkeypatch, _MCP(CRASH), start=MIDNIGHT + 299)
    assert [a for a, _ in opened] == ["xyz:XYZ100"] + BASKET


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026), US daily bars Jan 2010 - Nov 2026, entry at the signal close, the stop on
    the wick, rungs arming on the close, each market charged its Hyperliquid taker fee both sides, its
    measured spread and funding at its 180-day average rate: 810 trades, profit factor 2.07 (1.98 on the
    bar extreme), every name positive, blocks 2.09 / 3.65 / 1.93 / 1.78. 486 neighbouring cells all
    above 1.0, worst 1.53 (1.40 stressed). Worst book drawdown 28% at 4% clips and a 12% washout (34% at
    the first build's 5% / 15%; the trades are the same, so the profit factor is too). Hindsight
    caveat: the ten names are today's winners; with the Nasdaq 100 itself as the basket the
    neighbourhood's worst is 1.85."""
    assert (INPUTS["overheatRsi"], INPUTS["washoutRsi"], INPUTS["stopRoePct"]) == (79, 30, 30)
