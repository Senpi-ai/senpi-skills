"""Gannet Crypto: Gannet's dip cascade on BTC, ETH, SOL and HYPE, pinned.

1. The cascade is the four majors in order, main-dex names (no xyz: prefix), every coin at 1x.
2. The stop and the ladder are its own, wider than Gannet's, and the scanner's stop agrees with
   the DSL's. The DSL decides every exit: no clock cut is enabled, the ladder only locks a gain.
3. The scanner reads the main dex for these coins and buys the first washed-out one.

Run: python3 -B -m pytest strategies/gannet-crypto/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))
import scan  # noqa: E402
import scoring  # noqa: E402

RT = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MAN = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SIG = next(s for s in RT["scanners"] if s["type"] == "external_scanner")
INPUTS = dict(SIG["inputs"])
DSL = RT["exit"]["dsl_preset"]
D1 = 86400
MIDNIGHT = 1794441600


def test_four_majors_in_order_all_at_1x():
    assert INPUTS["allowedAssets"] == ["BTC", "ETH", "SOL", "HYPE"] == MAN["catalog"]["assets"]
    assert not any(":" in a for a in INPUTS["allowedAssets"])            # main-dex coins carry no prefix
    assert set(INPUTS["leverageByAsset"].values()) == {1} and INPUTS["leverageMax"] == 1
    assert RT["strategy"]["default_leverage"] == MAN["catalog"]["leverage_max"] == 1
    assert INPUTS["maxSlots"] == RT["strategy"]["slots"] == MAN["catalog"]["max_slots"] == 1
    assert INPUTS["marginPct"] == RT["strategy"]["margin_pct"] == 20
    assert MAN["catalog"]["varies"] == "gannet"


def test_the_stop_and_the_ladder():
    assert INPUTS["stopRoePct"] == DSL["phase1"]["max_loss_pct"] == 30
    assert DSL["phase1"]["enabled"] is False
    tiers = [(t["trigger_pct"], t["lock_hw_pct"]) for t in DSL["phase2"]["tiers"]]
    assert tiers == [(15, 40), (25, 50), (40, 65), (60, 80), (100, 90)]
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert DSL[cut]["enabled"] is False


def test_the_rails():
    rails = RT["risk"]["guard_rails"]
    assert rails["daily_loss_limit_pct"] > INPUTS["stopRoePct"] * INPUTS["marginPct"] / 100
    assert rails["max_entries_per_day"] > RT["strategy"]["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False


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


def _bars(closes, last_open):
    n = len(closes)
    return [{"t": int((last_open - (n - 1 - i) * D1) * 1000), "o": str(c), "h": str(c), "l": str(c),
             "c": str(c), "v": "1"} for i, c in enumerate(closes)]


class _MCP:
    def __init__(self, washed):
        self.washed, self.calls = washed, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "strategy_get_clearinghouse_state":
            sec = {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
                   "withdrawable": "1000", "assetPositions": []}
            return {"data": {"main": sec, "xyz": sec}}
        up = [100.0 + 0.2 * i for i in range(82)]
        closes = up + [up[-1] * (1 - 0.04 * (k + 1)) for k in range(8)] if args["asset"] in self.washed else \
            [100.0 + (0.5 if i % 2 else -0.4) + 0.05 * i for i in range(90)]
        return {"data": {"candles": {"1d": _bars(closes, MIDNIGHT - D1) + _bars([closes[-1]], MIDNIGHT)}}}


def test_the_scanner_reads_the_main_dex_and_buys_the_first_washed_out_coin(monkeypatch):
    monkeypatch.setattr(scan.time, "time", lambda: MIDNIGHT + 300)
    mcp = _MCP({"SOL", "HYPE"})
    out = scan.scan(dict(INPUTS), _Ctx(mcp))
    assert [(s["asset"], s["leverage"], s["marginPct"]) for s in out] == [("SOL", 1, 20)]
    reads = [a for t, a in mcp.calls if t == "market_get_asset_data"]
    assert [a["asset"] for a in reads] == INPUTS["allowedAssets"] and all(a["dex"] == "" for a in reads)


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026) on Hyperliquid's own daily candles, Aug 2020 - Nov 2026, entry at the
    signal close, the stop on the wick, rungs arming on the close, each coin charged its taker fee both
    sides (4.5 bps a side) plus its measured spread: 28 trades, 75% winners, profit factor 2.38 (2.20
    on the bar extreme); BTC 13 trades PF 1.87, ETH 5 PF 1.17, SOL 8 and HYPE 2 all winners; 2022 lost
    (PF 0.40); worst drawdown about 15% at 20% margin. Every neighbouring cell (RSI 8/10/12 x line
    28/30/32 x stop 21/30/40 x ladders wide/wider/widest x both arming bounds) above 1.0, the worst
    1.03 — thin, and said so. With BTC at 2x the BTC leg lost (PF 0.71), which is why every coin is 1x."""
    assert (INPUTS["rsiPeriod"], INPUTS["oversoldRsi"], INPUTS["stopRoePct"]) == (10, 30, 30)
