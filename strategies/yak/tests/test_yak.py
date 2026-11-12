"""Yak: all-weather with trend gates, pinned.

1. Five sleeves in equal 15% clips, each held only while its market is above its 40-week average and
   above its close 13 weeks back; five slots so every qualifier is held; modest leverage.
2. The engine is a byte copy of strategies/pronghorn's (SHA-256 pinned here; equality with the source
   checked once both packages share a branch). Yak differs only through runtime.yaml inputs.
3. The DSL decides every exit: no clock cut is enabled, the ladder only ever locks a gain.
4. Every qualifier is bought at one close (not just the strongest).

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
ENGINE = {"scan.py": "1ddeac8eed988db8ab0ef724545c40b34a574dc19689d79bc8512070ab324e78",
          "scoring.py": "7bdd8656acf434e86a8cc0bab9df4085385ae585b5b2d8f2ec18a52c148230f1"}
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


def test_the_engine_is_pronghorns_bytes():
    for name, sha in ENGINE.items():
        assert _sha(os.path.join(PKG, "main", "scanners", name)) == sha, f"{name} drifted from the pronghorn engine"
        src = os.path.join(PKG, "..", "pronghorn", "main", "scanners", name)
        if os.path.exists(src):
            assert _sha(src) == sha, f"pronghorn's {name} moved; re-vendor it here and update the hash"


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
    assert sorted((s["asset"], s["leverage"], s["marginPct"]) for s in out) == [("xyz:CL", 1, 15), ("xyz:GOLD", 2, 15), ("xyz:XYZ100", 2, 15)]


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026), US daily bars Jan 2010 - Nov 2026 (QQQ, SPY, GLD, USO, SLV), entry at the
    signal close, the stop on the wick, rungs arming on the close, 0.18% fees: 97 trades, profit factor
    3.03, every sleeve positive, blocks 2.82 / 2.07 / 2.49 / 4.49, worst book drawdown 17.5% at 15% a
    sleeve. 162 neighbouring cells all above 1.0, worst 1.84; a trend-only gate: 2.86, worst 1.79."""
    assert (INPUTS["trendWeeks"], INPUTS["momentumWeeks"], INPUTS["stopRoePct"]) == (40, 13, 30)
