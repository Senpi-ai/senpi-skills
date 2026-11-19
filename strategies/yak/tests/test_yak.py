"""Yak: all-weather with trend gates, pinned.

1. Five sleeves, each 15% of the account, each bought only while its market is above its 40-week
   average and above its close 13 weeks back; five slots so every qualifier is held; modest leverage.
2. The engine is pinned (SHA-256). It began as strategies/pronghorn's and now differs: it sizes each
   sleeve on the account, keeps going past a sleeve it cannot fund, reads the 13-week reference at
   the decision close, needs current weekly candles (review #978), and sends one sleeve per tick, so
   each is sized off a fresh balance read and a skipped open can't enlarge the next.
3. The DSL decides every exit: no clock cut is enabled, the ladder only ever locks a gain.
4. Every qualifier is bought at one close (not just the strongest), one a tick inside its entry hour.

Run: python3 -B -m pytest strategies/yak/tests -q
"""
import hashlib
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PKG, "main", "scanners"))
import scan  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(PKG, "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(PKG, "strategy.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "yak_main_signals")
INPUTS = dict(SIGNALS["inputs"])
DSL = RUNTIME["exit"]["dsl_preset"]
ENGINE = {"scan.py": "717192c7d7cb2f1a4a395d9e16493f5e058c9104fa20d5bb7ad49b7499373939",
          "scoring.py": "5487548c309b9fec2e35242e30a4ad6ef9c4d65d05cf62a85782454e2e25b6de"}
D1, W1 = 86400, 7 * 86400
MIDNIGHT = 1794441600


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_five_sleeves_equal_clips_every_qualifier_held():
    assert INPUTS["allowedAssets"] == ["xyz:XYZ100", "xyz:SP500", "xyz:GOLD", "xyz:CL", "xyz:SILVER"] == MANIFEST["catalog"]["assets"]
    assert INPUTS["leverageByAsset"] == {"xyz:XYZ100": 2, "xyz:SP500": 2, "xyz:GOLD": 2, "xyz:CL": 1, "xyz:SILVER": 1}
    assert INPUTS["leverageMax"] == MANIFEST["catalog"]["leverage_max"] == 2 and RUNTIME["strategy"]["default_leverage"] == 1
    assert INPUTS["maxSlots"] == RUNTIME["strategy"]["slots"] == MANIFEST["catalog"]["max_slots"] == len(INPUTS["allowedAssets"])
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == 15
    assert INPUTS["marginPct"] * INPUTS["maxSlots"] <= 100
    assert (INPUTS["trendWeeks"], INPUTS["momentumWeeks"]) == (40, 13)


def test_the_engine_is_pinned():
    for name, sha in ENGINE.items():
        assert _sha(os.path.join(PKG, "main", "scanners", name)) == sha, f"{name} changed: re-pin it deliberately"


def test_the_dsl_decides_every_exit():
    assert INPUTS["stopRoePct"] == DSL["phase1"]["max_loss_pct"] == 30 and DSL["phase1"]["enabled"] is False
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert DSL[cut]["enabled"] is False
    assert [(t["trigger_pct"], t["lock_hw_pct"]) for t in DSL["phase2"]["tiers"]] == [(15, 30), (25, 45), (40, 60), (60, 75), (100, 85)]
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > INPUTS["maxSlots"] and rails["drawdown_reset_on_day_rollover"] is False


def _bars(closes, last_open, step):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * step) * 1000), "o": str(c), "h": str(c), "l": str(c),
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
    def __init__(self, mcp):
        self.senpi_mcp, self.state, self.dry_run, self.wallet = mcp, _State(), False, "0xabc"


class _MCP:
    def __init__(self, trending):
        self.trending, self.calls = trending, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": "1000", "assetPositions": []}
            return {"data": {"main": sec, "xyz": sec}}
        last = 160.0 if args["asset"] in self.trending else 90.0
        weekly = _bars([100.0 + i for i in range(53)], MIDNIGHT - 3 * D1 - W1, W1)
        return {"data": {"candles": {"1d": _bars([last] * 90, MIDNIGHT - D1, D1) + _bars([last], MIDNIGHT, D1), "1w": weekly}}}


def test_every_trending_sleeve_is_bought_at_one_close(monkeypatch):
    monkeypatch.setattr(scan.time, "time", lambda: MIDNIGHT + 300)
    out = scan.scan(dict(INPUTS), _Ctx(_MCP({"xyz:XYZ100", "xyz:GOLD", "xyz:CL"})))
    assert len(out) == 1                                             # one a tick, the strongest first
    mcp = _Account({"xyz:XYZ100", "xyz:GOLD", "xyz:CL"})
    assert sorted(_ticks(monkeypatch, mcp)) == [("xyz:CL", 150.0), ("xyz:GOLD", 150.0), ("xyz:XYZ100", 150.0)]
    assert sorted((s["asset"], s["leverage"]) for s in mcp.sent) == [("xyz:CL", 1), ("xyz:GOLD", 2), ("xyz:XYZ100", 2)]



class _Account(_MCP):
    """The fixture with a chosen account value / free margin, optional per-market closes and an
    optional shorter weekly history for one market."""

    def __init__(self, trending, account="1000", withdrawable="1000", closes=None, weekly_rows=None):
        super().__init__(trending)
        self.account, self.free, self.closes, self.weekly_rows = account, withdrawable, closes or {}, weekly_rows or {}
        self.held, self.sent = [], []

    def call_tool(self, tool, args):
        out = super().call_tool(tool, args)
        if tool == "strategy_get_clearinghouse_state":
            for sec in out["data"].values():
                sec["marginSummary"]["accountValue"], sec["withdrawable"] = self.account, self.free
            out["data"]["xyz"] = dict(out["data"]["xyz"], assetPositions=[
                {"position": {"coin": c, "szi": "1", "marginUsed": "15"}} for c in self.held])
        else:
            c = out["data"]["candles"]
            if args["asset"] in self.closes:
                for r in c["1d"]:
                    r["c"] = str(self.closes[args["asset"]])
            if args["asset"] in self.weekly_rows:
                c["1w"] = c["1w"][-self.weekly_rows[args["asset"]]:]
        return out


ALL = set(INPUTS["allowedAssets"])


def _ticks(monkeypatch, mcp, start=MIDNIGHT + 300, end=MIDNIGHT + 3600, skip=()):
    """Every five-minute tick through the entry window, with the runtime opening what each tick emits:
    marginPct of the free margin at that moment, after which that margin is no longer free and the
    market shows as held. A market in `skip` is one the runtime does not open (the per-asset
    cooldown, a risk gate), so nothing is committed. Returns the opens as [(asset, margin to the
    cent)], in order; every signal sent is kept in `mcp.sent`."""
    ctx, opened, t = _Ctx(mcp), [], start
    while t <= end:
        monkeypatch.setattr(scan.time, "time", lambda t=t: t)
        out = scan.scan(dict(INPUTS), ctx)
        assert len(out) <= 1, "one signal per tick"
        mcp.sent.extend(out)
        for s in out:
            if s["asset"] in skip:
                continue
            free = float(mcp.free)
            margin = s["marginPct"] / 100.0 * free
            opened.append((s["asset"], round(margin, 2)))
            mcp.free, mcp.held = str(free - margin), mcp.held + [s["asset"]]
        t += 300
    return opened


def _scan(monkeypatch, mcp):
    monkeypatch.setattr(scan.time, "time", lambda: MIDNIGHT + 300)
    return scan.scan(dict(INPUTS), _Ctx(mcp))


def test_every_qualifier_is_funded_at_the_100_dollar_minimum(monkeypatch):
    """Review #978: at the $100 minimum, sizing 15% of a shrinking free balance dropped oil and silver
    under the $12 order floor. Each sleeve is now $15 of the account: $15 at 1x clears $12."""
    opened = _ticks(monkeypatch, _Account(ALL, account="100", withdrawable="100"))
    assert {a for a, _ in opened} == ALL
    assert [m for _, m in opened] == [15.0] * 5                      # five equal $15 clips, one a tick


def test_with_sp500_too_young_the_other_four_are_funded(monkeypatch):
    opened = _ticks(monkeypatch, _Account(ALL, account="100", withdrawable="100", weekly_rows={"xyz:SP500": 34}))
    assert {a for a, _ in opened} == ALL - {"xyz:SP500"}


def test_an_unfundable_sleeve_does_not_stop_the_ones_after_it(monkeypatch):
    """Review #978: with $20 free on a $100 account, one $15 clip fits; the scanner used to stop at the
    first sleeve it could not fund. The strongest goes out; a sleeve it cannot fund is skipped, not
    ended on, so a later 2x sleeve is still checked after a 1x sleeve that failed."""
    two = {"xyz:CL": 200.0, "xyz:GOLD": 160.0}
    assert _ticks(monkeypatch, _Account(set(two), account="100", withdrawable="20", closes=two)) == [("xyz:CL", 15.0)]
    # a $70 account: each clip is $10.50. Oil ranks first (stronger momentum) but at 1x it is under the
    # $12 floor; gold at 2x is $21 and must still go out.
    out = _scan(monkeypatch, _Account(set(two), account="70", withdrawable="70", closes=two))
    assert [s["asset"] for s in out] == ["xyz:GOLD"]
    # Partly committed account: $70 free on a $100 account. Both $15 clips fit; gold is not suppressed.
    assert _ticks(monkeypatch, _Account(set(two), account="100", withdrawable="70", closes=two)) == [
        ("xyz:CL", 15.0), ("xyz:GOLD", 15.0)]                       # $15 of the account each, one a tick


def test_an_open_the_runtime_skips_does_not_enlarge_the_next(monkeypatch):
    """Follow-up to #978. When a close's sleeves went out in one batch, each converted against the
    free margin left after the ones before it, an open the runtime skipped left more free margin than
    the next signal assumed. The skip could be a cooldown on a market stopped out minutes before the
    close. The next sleeve then opened larger than its clip: on a $100 account with all five trending,
    a skipped first sleeve would have opened the second at 15/85 of $100 = $17.65. One sleeve a tick,
    sized off that tick's balance read, opens each at $15, and the skipped one is not retried this close."""
    first = _scan(monkeypatch, _Account(ALL, account="100", withdrawable="100"))[0]["asset"]
    opened = _ticks(monkeypatch, _Account(ALL, account="100", withdrawable="100"), skip={first})
    assert {a for a, _ in opened} == ALL - {first} and [m for _, m in opened] == [15.0] * 4


def test_a_weekly_feed_missing_its_newest_close_skips_that_sleeve(monkeypatch):
    class _Lag(_Account):
        def call_tool(self, tool, args):
            out = super().call_tool(tool, args)
            if tool == "market_get_asset_data" and args["asset"] == "xyz:GOLD":
                out["data"]["candles"]["1w"] = out["data"]["candles"]["1w"][:-2]
            return out
    out = _scan(monkeypatch, _Lag({"xyz:XYZ100", "xyz:GOLD"}))
    assert [s["asset"] for s in out] == ["xyz:XYZ100"]


def test_the_momentum_reference_is_thirteen_weeks_back(monkeypatch):
    """Review #979 found `wc[-13]` reaching back 12 weeks in kinkajou; yak's engine had the same line.
    The reference is now the weekly close nearest to 13 weeks before the decision close."""
    import scoring
    wk = _bars([float(i) for i in range(1, 41)], MIDNIGHT - W1, W1)        # weekly closes 1..40, the last at MIDNIGHT
    assert scoring.close_weeks_back(wk, MIDNIGHT, 13) == 27.0
    assert scoring.close_weeks_back(wk, MIDNIGHT + 3 * D1, 13) == 27.0
    assert scoring.close_weeks_back(wk, MIDNIGHT + 4 * D1, 13) == 28.0
    assert scoring.close_weeks_back(wk[-12:], MIDNIGHT, 13) is None


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026), US daily bars Jan 2010 - Nov 2026 (QQQ, SPY, GLD, USO, SLV), entry at the
    signal close, the stop on the wick, rungs arming on the close, each sleeve charged its Hyperliquid
    taker fee both sides, its measured spread and funding at its 180-day average rate (CL's negative):
    97 trades, profit factor 2.29, blocks 2.42 / 1.04 / 1.99 / 4.03, worst book drawdown 20% at 15% a
    sleeve. 162 neighbouring cells all above 1.0, worst 1.57. Funding stressed to 11% a year, never
    received: 0.98, worst 0.81 (flagged: the book depends on funding staying near recent levels)."""
    assert (INPUTS["trendWeeks"], INPUTS["momentumWeeks"], INPUTS["stopRoePct"]) == (40, 13, 30)
