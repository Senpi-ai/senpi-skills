"""Squirrel: the BTC/ETH dip ladder, pinned.

1. The inputs ARE the strategy: three rungs on three wallets (buying 10 / 20 / 30% under the 60-day
   high, funded 17 / 33 / 50%), BTC and ETH on the main dex, a quarter of the account per coin at 1x,
   no trend gate. The three runtime.yaml files are identical but for the rung's depth and names.
2. The DSL decides every exit: no clock cut, the stop is the scanner's stopRoePct, the ladder only
   ever locks a gain; the rails are the spec's, and the drawdown halt is a disaster brake only.
3. The rule (scoring): the 60-day high excludes the latest close and is read by TIME, so a dropped
   row cannot stretch the window back; too little history is not a candidate.
4. The scanner buys at the daily close only, once per coin per close, ONE signal per tick sized as an
   equal clip of the ACCOUNT. The dollars are asserted through a model of the runtime (each signal's
   margin = marginPct x the free margin left), at the computed minimum budget too. A coin whose series
   is unreadable or stale waits without being marked; it holds back other coins only when they would
   compete for its slot. An unfundable clip is skipped, never ended on.
5. The measured parameters are the shipped ones; the docstring of
   test_the_measured_parameters_are_the_shipped_ones carries the replay summary.

Run: python3 -B -m pytest strategies/squirrel/tests -q
"""
import importlib
import os
import re
import sys
import types

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(PKG, "..", ".."))
MANIFEST = yaml.safe_load(open(os.path.join(PKG, "strategy.yaml"), encoding="utf-8"))
CATALOG = MANIFEST["catalog"]
D1, W1 = 86400, 7 * 86400
MIDNIGHT = 1794441600          # 2026-11-12 00:00 UTC: `now % 86400` is the time since the daily close
DEPTHS = {"rung1": 10, "rung2": 20, "rung3": 30}
SHARES = {"rung1": 0.17, "rung2": 0.33, "rung3": 0.50}


def _load(rung):
    """Each rung ships its own scan.py + scoring.py under the same module names, so import them with
    that rung's scanners dir first on sys.path and the names cleared — the way the runtime launches
    each scanner in its own process."""
    d = os.path.join(PKG, rung, "scanners")
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
    rt = yaml.safe_load(open(os.path.join(PKG, rung, "runtime.yaml"), encoding="utf-8"))
    sig = next(s for s in rt["scanners"] if s["type"] == "external_scanner")
    return types.SimpleNamespace(name=rung, scan=scan, scoring=scoring, rt=rt, sig=sig, inputs=dict(sig["inputs"]))


RUNGS = {r: _load(r) for r in DEPTHS}
R1 = RUNGS["rung1"]


# ── 1. the inputs are the strategy ───────────────────────────────────────────────────────

def test_three_rungs_three_wallets_split_one_two_three():
    insts = MANIFEST["instances"]
    assert [i["name"] for i in insts] == list(DEPTHS)
    assert {i["name"]: i["funding_share"] for i in insts} == SHARES
    assert abs(sum(SHARES.values()) - 1.0) < 1e-9
    assert len({i["wallet_env"] for i in insts}) == 3                       # three wallets
    # sizes about 1:2:3 — the deeper the step, the more it buys
    assert SHARES["rung2"] / SHARES["rung1"] == pytest.approx(2, abs=0.1)
    assert SHARES["rung3"] / SHARES["rung1"] == pytest.approx(3, abs=0.1)
    assert MANIFEST["version"] == "1.0.0" and "varies" not in CATALOG


@pytest.mark.parametrize("rung", list(DEPTHS))
def test_each_rung_buys_btc_and_eth_at_its_depth_with_a_quarter_of_the_account_at_1x(rung):
    s = RUNGS[rung]
    inp = s.inputs
    assert inp["allowedAssets"] == ["BTC", "ETH"] == CATALOG["assets"]
    assert not any(":" in a for a in inp["allowedAssets"])                  # main dex, bare symbols
    assert inp["depthPct"] == DEPTHS[rung]
    assert (inp["highLookbackDays"], inp["regimeWeeks"], inp["entryWindowSeconds"]) == (60, 0, 3600)
    assert inp["leverage"] == s.rt["strategy"]["default_leverage"] == CATALOG["leverage_max"] == 1
    assert inp["marginPct"] == s.rt["strategy"]["margin_pct"] == 25
    assert inp["maxSlots"] == s.rt["strategy"]["slots"] == CATALOG["max_slots"] == 2
    assert inp["marginPct"] * inp["maxSlots"] == 50                         # half the wallet at most
    assert inp["minNotionalUsd"] == 12
    assert s.rt["name"] == f"squirrel-{rung}" and s.rt["group"] == "squirrel"
    assert s.rt["strategy"]["wallet"] == "${SQUIRREL_%s_WALLET}" % rung.upper()


def test_the_rungs_differ_only_by_depth_and_name():
    def norm(rung):
        text = open(os.path.join(PKG, rung, "runtime.yaml"), encoding="utf-8").read()
        doc = yaml.safe_load(text.replace(rung, "rungN").replace(rung.upper(), "RUNGN"))
        doc.pop("description")
        for sc in doc["scanners"]:
            sc.get("inputs", {}).pop("depthPct", None)
        return doc
    assert norm("rung1") == norm("rung2") == norm("rung3")


def test_min_budget_is_set_by_the_smallest_rung():
    sys.path.insert(0, os.path.join(REPO, "senpi-trading-runtime", "scripts"))
    try:
        import min_budget
    finally:
        sys.path.pop(0)
    mb = min_budget.strategy_min_budget(MANIFEST, {r: RUNGS[r].rt for r in DEPTHS})
    assert mb["min_budget"] == 300 and mb["binding_wallet"] == "rung1" and mb["wallet_count"] == 3
    # the smallest position: a quarter of 17% of the budget (about 1/24) must clear the $12 minimum
    assert 300 * SHARES["rung1"] * 0.25 >= 12 > 280 * SHARES["rung1"] * 0.25


# ── 2. the DSL decides every exit ────────────────────────────────────────────────────────

@pytest.mark.parametrize("rung", list(DEPTHS))
def test_the_dsl_decides_every_exit(rung):
    s = RUNGS[rung]
    dsl = s.rt["exit"]["dsl_preset"]
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert dsl[cut]["enabled"] is False, f"{cut} must stay off — the DSL decides every exit"
    assert dsl["phase1"]["enabled"] is False
    assert s.inputs["stopRoePct"] == dsl["phase1"]["max_loss_pct"] == 35      # 35% of price at 1x
    tiers = [(t["trigger_pct"], t["lock_hw_pct"]) for t in dsl["phase2"]["tiers"]]
    assert dsl["phase2"]["enabled"] and tiers == [(35, 40), (70, 50), (122, 65), (210, 80)]
    assert [t for t, _ in tiers] == sorted(t for t, _ in tiers)
    assert [k for _, k in tiers] == sorted(k for _, k in tiers) and all(0 < k < 100 for _, k in tiers)
    # nothing but the DSL closes: no close action, no scanner that is not the entry or the tracker
    assert {a["action_type"] for a in s.rt["actions"]} == {"POSITION_TRACKER", "OPEN_POSITION"}
    assert [x["type"] for x in s.rt["scanners"]] == ["position_tracker", "external_scanner"]


@pytest.mark.parametrize("rung", list(DEPTHS))
def test_order_types_rails_and_tick_budget(rung):
    s = RUNGS[rung]
    entry = next(a for a in s.rt["actions"] if a["action_type"] == "OPEN_POSITION")
    assert entry["params"] == {"order_type": "FEE_OPTIMIZED_LIMIT", "fee_optimized_limit_options": {
        "ensure_execution_as_taker": True, "execution_timeout_seconds": 30}}
    ex = s.rt["exit"]
    assert ex["order_type"] == "FEE_OPTIMIZED_LIMIT" and ex["fee_optimized_limit_options"] == {
        "ensure_execution_as_taker": True, "execution_timeout_seconds": 60}
    rails = s.rt["risk"]["guard_rails"]
    assert rails == {"daily_loss_limit_pct": 20, "max_entries_per_day": 2,
                     "bypass_max_entries_per_day_on_profit": False, "max_consecutive_losses": 4,
                     "cooldown_seconds": 86400, "drawdown_halt_pct": 60,
                     "drawdown_reset_on_day_rollover": False, "per_asset_cooldown_seconds": 300}
    one_stop = s.inputs["stopRoePct"] * s.inputs["marginPct"] / 100          # 8.75% of the wallet
    assert rails["daily_loss_limit_pct"] > 2 * one_stop                       # both coins stopped in a day
    assert rails["max_entries_per_day"] >= s.inputs["maxSlots"]               # one close fills the wallet
    assert s.sig["timeout_seconds"] < s.sig["interval_seconds"] == 300
    assert s.inputs["entryWindowSeconds"] >= 3 * s.sig["interval_seconds"]    # several ticks to act on a close


# ── 3. the rule ──────────────────────────────────────────────────────────────────────────

def _bars(closes, last_open, step=D1):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * step) * 1000), "T": int((last_open - (n - 1 - i) * step + step) * 1000) - 1,
             "s": "X", "i": "1d", "o": str(c), "h": str(c * 1.01), "l": str(c * 0.99), "c": str(c), "v": "1", "n": 1}
            for i, c in enumerate(closes)]


def _dip(below_pct, n=89, high=100.0):
    """n closed daily closes: flat at `high`, the last one `below_pct` percent under it."""
    return [high] * (n - 1) + [round(high * (1 - below_pct / 100.0), 6)]


def test_the_prior_high_excludes_the_latest_close_and_needs_61_closes():
    sc = R1.scoring
    closes = [float(i) for i in range(1, 62)]                                 # 61 closes, rising
    assert sc.prior_high(_bars(closes, MIDNIGHT - D1), 60) == 60.0             # 61 (the latest) is not in it
    assert sc.prior_high(_bars(closes[1:], MIDNIGHT - D1), 60) is None         # 60 closes: too little history
    assert sc.measure("BTC", _bars(closes[1:], MIDNIGHT - D1), [], R1.inputs) is None


def test_the_window_is_read_by_time_so_a_dropped_row_cannot_reach_further_back():
    """A close of 120 sits 61 days before the latest bar, outside the 60-day window, whose high is 100.
    If the runtime's cast drops a row inside the window, a window counted by index (closes[-61:-1])
    reaches back to the 120 and calls a 5% dip a 21% one; read by time it stays at 100."""
    sc = R1.scoring
    closes = [50.0] * 27 + [120.0] + [100.0] * 60 + [95.0]                    # 89 closed bars
    rows = _bars(closes, MIDNIGHT - D1)
    assert sc.prior_high(rows, 60) == 100.0
    thinned = rows[:40] + rows[41:]                                           # a row inside the window dropped
    assert [sc._close(r) for r in thinned[-61:-1]].count(120.0) == 1          # the index window would see 120
    assert sc.prior_high(thinned, 60) == 100.0
    m = sc.measure("BTC", thinned, [], R1.inputs)
    assert m["high_px"] == 100.0 and not m["qualifies"]                        # 5% under: no rung-1 buy


def test_the_rule_is_close_at_or_under_the_trigger():
    sc = R1.scoring
    at = sc.measure("BTC", _bars(_dip(10), MIDNIGHT - D1), [], R1.inputs)
    assert at["trigger_px"] == pytest.approx(90.0) and at["qualifies"] and at["regime_average"] is None
    above = sc.measure("BTC", _bars([100.0] * 88 + [90.01], MIDNIGHT - D1), [], R1.inputs)
    assert not above["qualifies"]
    for rung, depth in DEPTHS.items():                                         # 15% under: rung 1 only
        m = RUNGS[rung].scoring.measure("ETH", _bars(_dip(15), MIDNIGHT - D1), [], RUNGS[rung].inputs)
        assert m["qualifies"] is (depth <= 15)


def test_closed_candles_drop_the_bar_in_progress():
    rows = _bars([100.0] * 5, MIDNIGHT)                                       # the last bar OPENED at midnight
    closed = R1.scoring.closed_candles(rows, D1, MIDNIGHT + 600)
    assert len(closed) == 4 and closed[-1]["t"] == (MIDNIGHT - D1) * 1000


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
        self.senpi_mcp, self.dry_run, self.wallet = mcp, dry_run, "0x" + "a" * 40
        self.state = state if state is not None else _State()


class _Wallet:
    """One rung wallet: a clearinghouse with a chosen account value / free margin / held coins (main
    dex), and each coin's daily candles: `closes` maps coin -> closed daily closes (default no dip).
    The bar in progress is appended with an absurd close (50x the last), so a reader that used it would
    see no dip anywhere. `stale` coins lose their newest closed bar; `broken` maps coin -> how its read
    fails ("raise", "text", "fail"). `midnight` is the daily close the feed is current to."""

    def __init__(self, closes=None, account=100.0, free=None, held=(), stale=(), broken=None, midnight=MIDNIGHT):
        self.closes, self.account, self.midnight = dict(closes or {}), float(account), midnight
        self.free = float(account if free is None else free)
        self.held, self.stale, self.broken = list(held), set(stale), dict(broken or {})
        self.calls = []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": str(self.account), "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": str(self.free), "assetPositions": []}
            pos = [{"position": {"coin": c, "szi": "1", "marginUsed": "25"}} for c in self.held]
            return {"success": True, "data": {"main": dict(sec, assetPositions=pos), "xyz": sec}}
        assert tool == "market_get_asset_data", tool
        coin = args["asset"]
        how = self.broken.get(coin)
        if how == "raise":
            raise RuntimeError("upstream timeout")
        if how == "text":
            return "upstream says no"
        if how == "fail":
            return {"success": False, "error": "no data"}
        closes = self.closes.get(coin, [100.0] * 89)
        rows = _bars(closes, self.midnight - D1)
        if coin in self.stale:
            rows = rows[:-1]                                                   # the newest close is missing
        rows += _bars([closes[-1] * 50], self.midnight)                         # the bar in progress
        return {"success": True, "data": {"candles": {"1d": rows}}}

    def reads(self):
        return [a for t, a in self.calls if t == "market_get_asset_data"]


def _scan(monkeypatch, wallet, rung=R1, at=MIDNIGHT + 300, state=None, dry_run=False, inputs=None):
    monkeypatch.setattr(rung.scan.time, "time", lambda: at)
    ctx = _Ctx(wallet, state, dry_run)
    out = rung.scan.scan(dict(inputs or rung.inputs), ctx)
    return out, (ctx.state.last() or {}).get("result", {})


def _ticks(monkeypatch, wallet, rung=R1, start=MIDNIGHT + 300, end=MIDNIGHT + 3600, skip=(), state=None, inputs=None):
    """Every five-minute tick through the entry hour, with the runtime opening what each tick emits:
    marginPct of the free margin at that moment, after which that margin is no longer free and the coin
    shows as held. A coin in `skip` is one the runtime does not open (the per-asset cooldown, a risk
    gate), so nothing is committed. Returns the opens as [(coin, margin to the cent)], in order."""
    state = state if state is not None else _State()
    opened, t = [], start
    while t <= end:
        out, _ = _scan(monkeypatch, wallet, rung, at=t, state=state, inputs=inputs)
        assert len(out) <= 1, "one signal per tick"
        for s in out:
            assert s["direction"] == "LONG" and s["leverage"] == 1 and 0 < s["marginPct"] <= 100
            if s["asset"] in skip:
                continue
            margin = s["marginPct"] / 100.0 * wallet.free
            opened.append((s["asset"], round(margin, 2)))
            wallet.free -= margin
            wallet.held.append(s["asset"])
        t += 300
    return opened


def test_a_dip_buys_both_coins_as_two_equal_quarter_clips_one_a_tick(monkeypatch):
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(12)}))
    assert [(s["asset"], s["marginPct"], s["leverage"]) for s in out] == [("BTC", 25.0, 1)]
    data = out[0]["data"]
    assert set(data) <= set(R1.sig["signal_data_schema"]) and "regimeAverage" not in data
    assert data["depthPct"] == 10 and data["signalBarTime"] == (MIDNIGHT - D1) * 1000
    assert data["clipUsd"] == 25.0 and data["stopPricePct"] == 35.0
    # the runtime commits marginPct of the free margin LEFT: both coins get a quarter of the $100 account
    assert _ticks(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(12)})) == [("BTC", 25.0), ("ETH", 25.0)]


def test_reads_are_daily_only_on_the_main_dex_and_skip_held_coins(monkeypatch):
    w = _Wallet({"BTC": _dip(11), "ETH": _dip(11)}, held=["BTC"])
    out, _ = _scan(monkeypatch, w)
    assert [s["asset"] for s in out] == ["ETH"]
    assert [(a["asset"], a["dex"], a["candle_intervals"]) for a in w.reads()] == [("ETH", "", ["1d"])]
    assert all(a["include_funding"] is False and a["include_order_book"] is False for a in w.reads())
    w = _Wallet({"BTC": _dip(11), "ETH": _dip(11)}, held=["BTC", "ETH"])
    assert _scan(monkeypatch, w)[0] == [] and w.reads() == []                 # slots full: nothing read


@pytest.mark.parametrize("below,buyers", [(9, []), (15, ["rung1"]), (25, ["rung1", "rung2"]),
                                          (35, ["rung1", "rung2", "rung3"])])
def test_each_rung_buys_only_from_its_own_depth(monkeypatch, below, buyers):
    got = [r for r, s in RUNGS.items() if _scan(monkeypatch, _Wallet({"BTC": _dip(below)}), s)[0]]
    assert got == buyers


def test_a_full_ladder_at_the_minimum_budget_opens_every_position_at_or_over_12_dollars(monkeypatch):
    """$300, the computed minimum, split 17/33/50 and less the ~$1.50 the wallet creation costs: in a
    crash that reaches every rung, each rung's two clips are a quarter of its account (to the cent: the
    emitted percent is rounded to 4 places), the smallest rung1's $12.37, and the six positions together
    are half of what was funded."""
    total = 0.0
    for rung, s in RUNGS.items():
        account = 300 * SHARES[rung] - 1.5
        opened = _ticks(monkeypatch, _Wallet({"BTC": _dip(35), "ETH": _dip(40)}, account=account), s)
        assert [c for c, _ in opened] == ["BTC", "ETH"]
        assert all(m == pytest.approx(account / 4, abs=0.01) and m >= 12 for _, m in opened)   # $12.37-12.38 at rung1
        total += sum(m for _, m in opened)
    assert total == pytest.approx((300 - 4.5) / 2, abs=0.02)


def test_under_the_minimum_an_unfundable_clip_is_skipped_not_ended_on(monkeypatch):
    """A $45 rung1 account: a quarter is $11.25, under the $12 minimum at 1x. Both coins qualify; the
    scanner records each it cannot fund and moves past it instead of stopping at the first, marks
    neither sent, and buys at a later tick of the same close once the clip clears."""
    state = _State()
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(11)}, account=45), state=state)
    assert out == [] and res["unfunded"] == ["BTC", "ETH"] and res["emitted"] == []
    out, _ = _scan(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(11)}, account=60), at=MIDNIGHT + 600, state=state)
    assert [s["asset"] for s in out] == ["BTC"]


def test_a_clip_beyond_the_free_margin_waits(monkeypatch):
    """$100 account with $20 free (margin committed elsewhere): a $25 clip does not fit in 90% of it."""
    out, res = _scan(monkeypatch, _Wallet({"ETH": _dip(11)}, free=20))
    assert out == [] and res["unfunded"] == ["ETH"]


def test_an_open_the_runtime_skips_does_not_enlarge_the_next(monkeypatch):
    """Both coins dip. The runtime skips BTC (a per-asset cooldown after a stop-out minutes before the
    close): nothing is committed. ETH still opens at a quarter of the account, $25 — not 25/75 of the
    free margin a batch sized on BTC's open would have assumed — and BTC is not retried at this close."""
    opened = _ticks(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(11)}), skip={"BTC"})
    assert opened == [("ETH", 25.0)]


def test_once_per_coin_per_close_and_a_re_buy_at_a_later_close(monkeypatch):
    state, w = _State(), _Wallet({"BTC": _dip(11)})
    assert _ticks(monkeypatch, w, state=state) == [("BTC", 25.0)]
    w.held, w.free = [], 100.0                                                 # stopped out the same day
    assert _scan(monkeypatch, w, at=MIDNIGHT + 3000, state=state)[0] == []     # this close already went out
    # the next close, flat again and still deep under the high: it buys again, lower, by design
    nxt = _Wallet({"BTC": [100.0] * 88 + [89.0, 80.0]}, midnight=MIDNIGHT + D1)
    out, _ = _scan(monkeypatch, nxt, at=MIDNIGHT + D1 + 300, state=state)
    assert [(s["asset"], s["data"]["closePx"], s["data"]["signalBarTime"]) for s in out] == [("BTC", 80.0, MIDNIGHT * 1000)]


def test_only_inside_the_hour_after_the_close(monkeypatch):
    w = _Wallet({"BTC": _dip(11)})
    assert _scan(monkeypatch, w, at=MIDNIGHT + 2 * 3600)[0] == []
    assert [t for t, _ in w.calls] == ["strategy_get_clearinghouse_state"]
    w = _Wallet({})
    _scan(monkeypatch, w, at=MIDNIGHT + 2 * 3600, dry_run=True)                # validation still reads candles
    assert [a["asset"] for a in w.reads()] == ["BTC", "ETH"]


def test_the_bar_in_progress_never_decides(monkeypatch):
    """The fixture's bar in progress closes at 50x, which would erase every dip; the scanner buys off
    the closed bar only."""
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11)}))
    assert [s["asset"] for s in out] == ["BTC"] and res["measured"]["BTC"]["close_px"] == 89.0


@pytest.mark.parametrize("how", ["stale", "raise", "text", "fail"])
def test_a_coin_that_cannot_be_read_waits_without_holding_back_the_other(monkeypatch, how):
    """Two coins, two slots: each coin has a slot of its own, so a BTC read that fails or misses this
    close takes nothing from ETH. BTC is not marked sent: once its close can be read, it is bought."""
    kw = {"stale": {"BTC"}} if how == "stale" else {"broken": {"BTC": how}}
    state = _State()
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(11)}, **kw), state=state)
    assert [s["asset"] for s in out] == ["ETH"] and res["waiting_on"] == ["BTC"] and "blocked_by" not in res
    assert not any(k.startswith("BTC|") for k in state.last()["signaled"])
    out, _ = _scan(monkeypatch, _Wallet({"BTC": _dip(11)}, held=["ETH"]), at=MIDNIGHT + 600, state=state)
    assert [s["asset"] for s in out] == ["BTC"]


def test_with_fewer_slots_than_coins_an_unreadable_coin_holds_back_the_ones_after_it(monkeypatch):
    """A fork with three coins in two slots: BTC comes first in the order. If it cannot be read it may
    have qualified, so ETH and SOL behind it may not take its slot; nothing is marked and the next tick
    resumes in order. A coin LAST in the order holds back nobody before it, and a coin with too little
    history (a recent listing) is not a candidate at all, so it holds back nothing."""
    fork = dict(R1.inputs, allowedAssets=["BTC", "ETH", "SOL"], maxSlots=2)
    dips = {"BTC": _dip(11), "ETH": _dip(11), "SOL": _dip(11)}
    out, res = _scan(monkeypatch, _Wallet(dips, stale={"BTC"}), inputs=fork)
    assert out == [] and res["blocked_by"] == "BTC"
    out, res = _scan(monkeypatch, _Wallet(dips, stale={"SOL"}), inputs=fork)
    assert [s["asset"] for s in out] == ["BTC"]
    young = dict(fork, allowedAssets=["SOL", "BTC", "ETH"])
    out, res = _scan(monkeypatch, _Wallet(dict(dips, SOL=_dip(11, n=30))), inputs=young)
    assert [s["asset"] for s in out] == ["BTC"] and "blocked_by" not in res
    # the shipped ladder holding a stray position has fewer open slots than coins: the same rule applies
    out, res = _scan(monkeypatch, _Wallet(dips, held=["SOL"], stale={"BTC"}))
    assert out == [] and res["blocked_by"] == "BTC"


def test_an_unreadable_account_emits_nothing(monkeypatch):
    class _NoAccount(_Wallet):
        def call_tool(self, tool, args):
            if tool == "strategy_get_clearinghouse_state":
                raise RuntimeError("down")
            return super().call_tool(tool, args)
    assert _scan(monkeypatch, _NoAccount({"BTC": _dip(11)}))[0] == []


# ── 5. the card and the measured parameters ──────────────────────────────────────────────

def test_the_card_matches_the_code_and_promises_nothing():
    card = " ".join(str(CATALOG[f]) for f in ("tagline", "belief_plain", "thesis"))
    assert CATALOG["direction"] == "long_only" and CATALOG["tier"] == "advanced"
    assert CATALOG["risk_level"] == "aggressive" and CATALOG["group"] == "dca" and CATALOG["sub_style"] == "dca"
    assert not re.search(r"\d", CATALOG["tagline"]), "keep numbers out of the tagline"
    assert not re.search(r"\b(returns?|makes?|pays?|earns?|guarantee\w*)\b", card, re.I)
    for fact in ("10%", "20%", "30%", "60 days", "1x", "35%", "17%", "33%", "50%"):
        assert fact in CATALOG["belief_plain"], fact
    assert "is not a forecast" in CATALOG["thesis"] and "lost money in 2025 and 2026" in CATALOG["thesis"]


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay, as approved for this build. Binance BTC/USDT + ETH/USDT daily bars (Jan 2019 - Nov
    2026) for decisions, 4h bars for the exit path; entry at the daily close; the stop on the wick;
    rungs arming on the close (extreme as the upper bound); each rung trade weighted by its rung's
    share (1:2:3) x 25% per position (half the budget at most); Hyperliquid main-dex taker fee 4.5 bps
    a side + measured spread; funding at each coin's 180-day average (BTC 8.0%, ETH 7.6% a year, longs
    paying) for every hour held; stress = 11% a year always paid.

    SQUIRREL (no gate): 117 trades across the six rung-coin books, profit factor 1.96 (1.86 stressed,
    1.95 free/trial), worst book drawdown 32.5%. Neighbourhood, 108 settings (60-day-high lookback
    45/60/75 x rung depths 8-16-24 / 10-20-30 / 12-24-36% x stop 25/35/45% x two ladders x both
    arming bounds): every setting clears, worst 1.33 (1.28 stressed); book drawdown worst 41.6%,
    median 32.3%. By year: 2019 1.25, 2020 6.74, 2021 1.60, 2022 2.45, 2023 one trade (won), 2024
    1.28, 2025 0.59, 2026 0.41. Fully deployed (90%) the drawdown was 51%. A "once per cycle" re-buy
    rule lost its edge (worst setting 0.75), so a flat rung re-buys at any close where the rule holds.
    """
    assert [RUNGS[r].inputs["depthPct"] for r in DEPTHS] == [10, 20, 30]
    assert (R1.inputs["highLookbackDays"], R1.inputs["stopRoePct"], R1.inputs["regimeWeeks"]) == (60, 35, 0)
