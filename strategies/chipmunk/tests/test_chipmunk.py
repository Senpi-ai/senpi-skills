"""Chipmunk: the conservative BTC/ETH dip buyer — squirrel's ladder with a 40-week trend gate, pinned.

1. Each rung IS squirrel's rung of the same depth with one input changed (`regimeWeeks: 40`) and the
   risk label: same coins, depths, split, sizing, stop, ladder, rails and order types.
2. The gate decides BUYS only: a dip is bought only while the coin closes above the average of its last
   40 CLOSED weekly closes. Nothing closes a position but the DSL — no close action, no exit signal for
   a held coin under its average, and the card never says the trend closes anything.
3. The weekly series must be current at the close (its newest weekly close within a week), else that
   coin waits unmarked; fewer than 40 weekly closes is too little history, not a wait.
4. Dollars through a model of the runtime at the computed minimum budget, one signal per tick.
5. The measured parameters are the shipped ones (replay summary in the last test's docstring).

The engine's other behaviour (time-anchored 60-day high, stale/unreadable daily series, one signal per
tick, skipped opens, unfundable clips) is pinned in strategies/squirrel/tests on the same bytes.

Run: python3 -B -m pytest strategies/chipmunk/tests -q
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
STRATEGIES = os.path.abspath(os.path.join(PKG, ".."))
REPO = os.path.abspath(os.path.join(STRATEGIES, ".."))
MANIFEST = yaml.safe_load(open(os.path.join(PKG, "strategy.yaml"), encoding="utf-8"))
CATALOG = MANIFEST["catalog"]
D1, W1 = 86400, 7 * 86400
MIDNIGHT = 1794441600          # 2026-11-12 00:00 UTC — a Thursday, so a weekly candle can close here too
DEPTHS = {"rung1": 10, "rung2": 20, "rung3": 30}
SHARES = {"rung1": 0.17, "rung2": 0.33, "rung3": 0.50}


def _load(rung, pkg=PKG):
    """Each rung ships its own scan.py + scoring.py under the same module names; import them with that
    rung's scanners dir first on sys.path and the names cleared, the way the runtime runs each scanner
    in its own process."""
    d = os.path.join(pkg, rung, "scanners")
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
    rt = yaml.safe_load(open(os.path.join(pkg, rung, "runtime.yaml"), encoding="utf-8"))
    sig = next(s for s in rt["scanners"] if s["type"] == "external_scanner")
    return types.SimpleNamespace(name=rung, scan=scan, scoring=scoring, rt=rt, sig=sig, inputs=dict(sig["inputs"]))


RUNGS = {r: _load(r) for r in DEPTHS}
R1 = RUNGS["rung1"]


# ── 1. squirrel's ladder plus the gate ───────────────────────────────────────────────────

def test_three_rungs_three_wallets_split_one_two_three():
    insts = MANIFEST["instances"]
    assert [i["name"] for i in insts] == list(DEPTHS)
    assert {i["name"]: i["funding_share"] for i in insts} == SHARES
    assert [i["wallet_env"] for i in insts] == [f"CHIPMUNK_{r.upper()}_WALLET" for r in DEPTHS]
    assert MANIFEST["version"] == "1.0.0"
    assert "varies" not in CATALOG, "chipmunk must surface on its own for conservative asks"


@pytest.mark.parametrize("rung", list(DEPTHS))
def test_each_rung_is_squirrels_with_the_40_week_gate(rung):
    def doc(pkg):
        text = open(os.path.join(STRATEGIES, pkg, rung, "runtime.yaml"), encoding="utf-8").read()
        d = yaml.safe_load(text.replace(pkg, "PKG").replace(pkg.upper(), "PKG"))
        d.pop("description")
        return d
    ours, squirrel = doc("chipmunk"), doc("squirrel")
    gate = next(s for s in ours["scanners"] if s["type"] == "external_scanner")["inputs"]
    base = next(s for s in squirrel["scanners"] if s["type"] == "external_scanner")["inputs"]
    assert (gate.pop("regimeWeeks"), base.pop("regimeWeeks")) == (40, 0)
    assert (ours["strategy"].pop("trading_risk"), squirrel["strategy"].pop("trading_risk")) == ("conservative", "aggressive")
    assert ours == squirrel
    inp = RUNGS[rung].inputs
    assert inp["depthPct"] == DEPTHS[rung] and inp["allowedAssets"] == ["BTC", "ETH"] == CATALOG["assets"]
    assert inp["leverage"] == CATALOG["leverage_max"] == 1 and inp["maxSlots"] == CATALOG["max_slots"] == 2
    assert inp["marginPct"] == 25 and RUNGS[rung].rt["name"] == f"chipmunk-{rung}"


@pytest.mark.parametrize("rung", list(DEPTHS))
def test_the_dsl_decides_every_exit(rung):
    s = RUNGS[rung]
    dsl = s.rt["exit"]["dsl_preset"]
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert dsl[cut]["enabled"] is False, f"{cut} must stay off — the DSL decides every exit"
    assert dsl["phase1"]["enabled"] is False
    assert s.inputs["stopRoePct"] == dsl["phase1"]["max_loss_pct"] == 35
    assert [(t["trigger_pct"], t["lock_hw_pct"]) for t in dsl["phase2"]["tiers"]] == [(35, 40), (70, 50), (122, 65), (210, 80)]
    assert {a["action_type"] for a in s.rt["actions"]} == {"POSITION_TRACKER", "OPEN_POSITION"}   # no close action
    rails = s.rt["risk"]["guard_rails"]
    assert (rails["drawdown_halt_pct"], rails["drawdown_reset_on_day_rollover"], rails["max_entries_per_day"]) == (60, False, 2)


def test_min_budget_is_set_by_the_smallest_rung():
    sys.path.insert(0, os.path.join(REPO, "senpi-trading-runtime", "scripts"))
    try:
        import min_budget
    finally:
        sys.path.pop(0)
    mb = min_budget.strategy_min_budget(MANIFEST, {r: RUNGS[r].rt for r in DEPTHS})
    assert (mb["min_budget"], mb["binding_wallet"], mb["wallet_count"]) == (300, "rung1", 3)


# ── fixtures: a rung wallet with daily AND weekly candles ────────────────────────────────

def _bars(closes, last_open, step):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * step) * 1000), "o": str(c), "h": str(c), "l": str(c),
             "c": str(c), "v": "1"} for i, c in enumerate(closes)]


def _dip(below_pct, n=89, high=100.0):
    return [high] * (n - 1) + [round(high * (1 - below_pct / 100.0), 6)]


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
        self.senpi_mcp, self.dry_run, self.wallet = mcp, dry_run, "0x" + "c" * 40
        self.state = state if state is not None else _State()


class _Wallet:
    """`daily` maps coin -> closed daily closes; `weekly` maps coin -> closed weekly closes (default 52
    weeks at 80, an average under every fixture dip). The newest weekly candle closes `weekly_lag` seconds
    before the daily close (0: the same midnight); `weekly_stale` coins lose their newest weekly close.
    Each feed ends with its bar in progress at an absurd 50x close."""

    def __init__(self, daily=None, weekly=None, account=100.0, held=(), weekly_lag=0, weekly_stale=()):
        self.daily, self.weekly = dict(daily or {}), dict(weekly or {})
        self.account = self.free = float(account)
        self.held, self.weekly_lag, self.weekly_stale = list(held), weekly_lag, set(weekly_stale)
        self.calls = []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": str(self.account), "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": str(self.free), "assetPositions": []}
            pos = [{"position": {"coin": c, "szi": "1", "marginUsed": "25"}} for c in self.held]
            return {"success": True, "data": {"main": dict(sec, assetPositions=pos), "xyz": sec}}
        coin = args["asset"]
        d = self.daily.get(coin, [100.0] * 89)
        w = self.weekly.get(coin, [80.0] * 52)
        last_week_open = MIDNIGHT - self.weekly_lag - W1
        wrows = _bars(w, last_week_open, W1)
        if coin in self.weekly_stale:
            wrows = wrows[:-1]
        candles = {"1d": _bars(d, MIDNIGHT - D1, D1) + _bars([d[-1] * 50], MIDNIGHT, D1),
                   "1w": wrows + _bars([w[-1] * 50], last_week_open + W1, W1)}
        return {"success": True, "data": {"candles": {k: v for k, v in candles.items() if k in args["candle_intervals"]}}}

    def reads(self):
        return [a for t, a in self.calls if t == "market_get_asset_data"]


def _scan(monkeypatch, wallet, rung=R1, at=MIDNIGHT + 300, state=None, inputs=None):
    monkeypatch.setattr(rung.scan.time, "time", lambda: at)
    ctx = _Ctx(wallet, state)
    out = rung.scan.scan(dict(inputs or rung.inputs), ctx)
    return out, (ctx.state.last() or {}).get("result", {})


def _ticks(monkeypatch, wallet, rung=R1, start=MIDNIGHT + 300, end=MIDNIGHT + 3600, state=None):
    """Every tick of the entry hour; the runtime opens each signal as marginPct of the free margin left."""
    state = state if state is not None else _State()
    opened, t = [], start
    while t <= end:
        out, _ = _scan(monkeypatch, wallet, rung, at=t, state=state)
        assert len(out) <= 1, "one signal per tick"
        for s in out:
            margin = s["marginPct"] / 100.0 * wallet.free
            opened.append((s["asset"], round(margin, 2)))
            wallet.free -= margin
            wallet.held.append(s["asset"])
        t += 300
    return opened


# ── 2. the gate decides buys only ────────────────────────────────────────────────────────

def test_a_dip_above_the_40_week_average_is_bought_with_the_average_on_the_signal(monkeypatch):
    w = _Wallet({"BTC": _dip(11)})                                            # 89 against an 80 average
    out, res = _scan(monkeypatch, w)
    assert [(s["asset"], s["marginPct"], s["leverage"]) for s in out] == [("BTC", 25.0, 1)]
    assert out[0]["data"]["regimeAverage"] == 80.0 and set(out[0]["data"]) <= set(R1.sig["signal_data_schema"])
    assert [(a["asset"], a["dex"], a["candle_intervals"]) for a in w.reads()] == [("BTC", "", ["1d", "1w"]), ("ETH", "", ["1d", "1w"])]


@pytest.mark.parametrize("average,bought", [(80.0, True), (88.99, True), (89.0, False), (95.0, False)])
def test_no_buy_at_or_under_the_average(monkeypatch, average, bought):
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11)}, weekly={"BTC": [average] * 52}))
    assert bool(out) is bought
    assert res["measured"]["BTC"]["regime_average"] == pytest.approx(average)


def test_above_the_average_without_a_dip_buys_nothing(monkeypatch):
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(9)}))
    assert out == [] and res["measured"]["BTC"]["qualifies"] is False


def test_the_average_is_of_the_last_40_closed_weekly_closes(monkeypatch):
    weekly = [200.0] * 12 + [70.0] * 40                                       # older weeks never count
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11)}, weekly={"BTC": weekly}))
    assert res["measured"]["BTC"]["regime_average"] == 70.0 and [s["asset"] for s in out] == ["BTC"]


def test_the_gate_never_closes_anything(monkeypatch):
    """A held coin that has fallen under its 40-week average gets no signal of any kind: the trend gate
    is an entry filter, and the DSL's stop and profit lock are the only exits."""
    w = _Wallet({"BTC": _dip(40)}, weekly={"BTC": [95.0] * 52}, held=["BTC"])
    out, _ = _scan(monkeypatch, w)
    assert out == [] and [a["asset"] for a in w.reads()] == ["ETH"]
    card = " ".join(str(CATALOG[f]) for f in ("tagline", "belief_plain", "thesis"))
    assert not re.search(r"(sells?|exits?|steps? aside|closes?|gets? out)\b[^.]{0,60}\b(when|once|if)\b[^.]{0,40}"
                         r"\b(trend|average)\b", card, re.I), "the card must not claim the trend closes a position"
    assert "never because the trend turned" in CATALOG["belief_plain"]


# ── 3. the weekly series must be current ─────────────────────────────────────────────────

def test_a_weekly_feed_missing_its_newest_close_waits_without_marking(monkeypatch):
    """The 40-week average must be this week's. BTC's weekly feed misses its newest close: BTC waits,
    ETH (current) is bought, nothing is marked for BTC, and the next tick that can read it buys it."""
    state = _State()
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(11)}, weekly_stale={"BTC"}), state=state)
    assert [s["asset"] for s in out] == ["ETH"] and res["waiting_on"] == ["BTC"]
    assert not any(k.startswith("BTC|") for k in state.last()["signaled"])
    out, _ = _scan(monkeypatch, _Wallet({"BTC": _dip(11)}, held=["ETH"]), at=MIDNIGHT + 600, state=state)
    assert [s["asset"] for s in out] == ["BTC"]


def test_a_weekly_close_days_before_the_daily_close_is_current(monkeypatch):
    """Weekly candles need not close at the same midnight as the daily one: a weekly close three days
    before the decision is this week's. With weeks closing a day before the decision and that row
    missing from the feed, the newest is eight days old — last week's — so the coin waits."""
    assert _scan(monkeypatch, _Wallet({"BTC": _dip(11)}, weekly_lag=3 * D1))[0]
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11)}, weekly_lag=D1, weekly_stale={"BTC"}))
    assert out == [] and res["waiting_on"] == ["BTC"]
    sc, decision = R1.scoring, float(MIDNIGHT)
    week_closing_at = lambda s: [{"t": (s - W1) * 1000, "c": "1"}]          # noqa: E731
    assert sc.weekly_current(week_closing_at(decision), decision)
    assert sc.weekly_current(week_closing_at(decision - W1 + 1), decision)
    assert not sc.weekly_current(week_closing_at(decision - W1), decision)
    assert not sc.weekly_current([], decision)


def test_fewer_than_40_weekly_closes_is_too_little_history_not_a_wait(monkeypatch):
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(11)}, weekly={"BTC": [80.0] * 39}))
    assert [s["asset"] for s in out] == ["ETH"] and res["waiting_on"] == [] and "BTC" not in res["measured"]
    fork = dict(R1.inputs, allowedAssets=["BTC", "ETH", "SOL"], maxSlots=2)   # more coins than slots
    out, res = _scan(monkeypatch, _Wallet({"BTC": _dip(11), "ETH": _dip(11)}, weekly={"BTC": [80.0] * 39}), inputs=fork)
    assert [s["asset"] for s in out] == ["ETH"] and "blocked_by" not in res


# ── 4. dollars ───────────────────────────────────────────────────────────────────────────

def test_a_full_ladder_at_the_minimum_budget(monkeypatch):
    """$300, less ~$1.50 a wallet for its creation: each rung buys both coins, one a tick, each a quarter
    of its account — rung1's $12.37 clears the $12 minimum — and the rest stays in USDC."""
    for rung, s in RUNGS.items():
        account = 300 * SHARES[rung] - 1.5
        w = _Wallet({"BTC": _dip(35), "ETH": _dip(40)}, weekly={"BTC": [50.0] * 52, "ETH": [50.0] * 52}, account=account)
        opened = _ticks(monkeypatch, w, s)
        assert [c for c, _ in opened] == ["BTC", "ETH"]
        assert all(m == pytest.approx(account / 4, abs=0.01) and m >= 12 for _, m in opened)
        assert w.free == pytest.approx(account / 2, abs=0.02)


# ── 5. the card and the measured parameters ──────────────────────────────────────────────

def test_the_card_matches_the_code_and_promises_nothing():
    assert (CATALOG["risk_level"], CATALOG["tier"], CATALOG["direction"]) == ("conservative", "starter", "long_only")
    assert CATALOG["group"] == "dca" and CATALOG["sub_style"] == "dca"
    card = " ".join(str(CATALOG[f]) for f in ("tagline", "belief_plain", "thesis"))
    assert not re.search(r"\d", CATALOG["tagline"]), "keep numbers out of the tagline"
    assert not re.search(r"\b(returns?|makes?|pays?|earns?|guarantee\w*)\b", card, re.I)
    for fact in ("10%", "20%", "30%", "60 days", "40 weekly closes", "1x", "35%", "17%", "33%", "50%"):
        assert fact in CATALOG["belief_plain"], fact
    assert "is not a forecast" in CATALOG["thesis"] and "lost money in 2025 and 2026" in CATALOG["thesis"]


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay, as approved for this build. Binance BTC/USDT + ETH/USDT daily bars (Jan 2019 - Nov
    2026) for decisions, 4h bars for the exit path; entry at the daily close; the stop on the wick;
    rungs arming on the close (extreme as the upper bound); each rung trade weighted by its rung's
    share (1:2:3) x 25% per position (half the budget at most); Hyperliquid main-dex taker fee 4.5 bps
    a side + measured spread; funding at each coin's 180-day average (BTC 8.0%, ETH 7.6% a year, longs
    paying) for every hour held; stress = 11% a year always paid.

    CHIPMUNK (40-week gate): 59 trades, profit factor 2.21 (2.12 stressed, 2.21 free/trial), worst
    drawdown 18.4%. Neighbourhood worst 1.21 (1.15 stressed); drawdown worst 23.4%, median 16.0%. By
    year: 2019 0.00 (4 trades), 2020 8.67, 2021 1.92, 2022 one trade (stopped), 2023 5 trades (all
    won), 2024 0.85, 2025 0.22, 2026 0.00 (2). It lost money in 2025-26: BTC and ETH fell from Oct 2025.
    """
    assert [RUNGS[r].inputs["depthPct"] for r in DEPTHS] == [10, 20, 30]
    assert (R1.inputs["regimeWeeks"], R1.inputs["highLookbackDays"], R1.inputs["stopRoePct"]) == (40, 60, 35)
