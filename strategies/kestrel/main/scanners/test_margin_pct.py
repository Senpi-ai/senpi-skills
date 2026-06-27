"""Guard: kestrel's marginPct must be a PERCENT in (0,100], never a v2 fraction.

The v3.x runtime sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so a
fraction like 0.30 sizes 0.30% of withdrawable — ~100x undersize, silent (no 400,
since 0.30 still satisfies the (0,100] bound).

Kestrel is the trap case in reverse: the v2 producer carried MARGIN_PCT = 0.30 as
a FRACTION and multiplied it into a dollar margin itself. The Runtime 3.0 port emits
a PERCENT (30) and lets the runtime size. This test drives the real scan.py with the
real runtime.yaml inputs through a stub that forces a clean breakout, and asserts the
emitted wire marginPct lands in [1,100], plus covers the runtime-input and in-code
default fallbacks.
"""

import importlib.util
import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")
_SCAN_PY = os.path.join(_HERE, "scan.py")


def _external_scanner_inputs():
    with open(_RUNTIME_YAML) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return s["inputs"]
    raise AssertionError("no external_scanner inputs in runtime.yaml")


def _load_scan():
    import sys
    sys.path.insert(0, _HERE)   # make sibling scoring.py importable
    try:
        spec = importlib.util.spec_from_file_location("kestrel_scan", _SCAN_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(_HERE)


class _FakeMcp:
    """Returns a strong clean LONG breakout on one XYZ name and an empty account
    (no held positions) so scan emits exactly one candidate."""

    def call_tool(self, name, args):
        if name == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"marginSummary": {"accountValue": 1000.0},
                                      "assetPositions": []},
                             "xyz": {"marginSummary": {"accountValue": 1000.0},
                                     "assetPositions": []}}}
        if name == "leaderboard_get_markets":
            return {"data": {"markets": []}}
        if name == "market_get_asset_data":
            # 6 ascending 1h closes -> a >3% 1H breakout (score >= 5), tight book.
            closes = [100, 101, 102, 103, 104, 108]
            candles_1h = [{"open": c, "high": c, "low": c, "close": c, "volume": 100}
                          for c in closes]
            candles_4h = list(candles_1h)
            return {"data": {
                "candles": {"1h": candles_1h, "4h": candles_4h},
                "asset_context": {"funding": 0.0},
                "order_book": {"levels": [[[107.99, 1]], [[108.01, 1]]]},
            }}
        return None


class _FakeCtx:
    def __init__(self):
        self.senpi_mcp = _FakeMcp()
        self.state = None
        self.wallet = "0xtest"
        self.scanner_name = "kestrel_main_signals"
        self.interval_seconds = 300


def _emit_one(mod, inputs):
    ins = dict(inputs)
    ins["universe"] = ["xyz:NVDA"]      # force a single name through
    out = mod.scan(ins, _FakeCtx())
    assert out, "scan emitted no candidates (breakout stub failed)"
    return out[0]


def test_runtime_top_level_margin_pct_is_percent_in_range():
    pct = float(_external_scanner_inputs()["marginPct"])
    # >=1 catches the fraction bug (0.30); <=100 is the wire upper bound.
    assert 1 <= pct <= 100, f"runtime.yaml marginPct={pct} not a PERCENT in [1,100]"


def test_emitted_margin_pct_is_percent_in_range():
    mod = _load_scan()
    sig = _emit_one(mod, _external_scanner_inputs())
    mpct = sig["marginPct"]
    assert 1 <= mpct <= 100, f"emitted marginPct {mpct} not in [1,100]"
    assert mpct == 30, f"expected emitted marginPct 30, got {mpct}"


def test_default_fallback_is_percent():
    """Drop the runtime marginPct knob -> the in-code default must also be a
    PERCENT in [1,100], not a fraction."""
    mod = _load_scan()
    ins = {k: v for k, v in _external_scanner_inputs().items() if k != "marginPct"}
    sig = _emit_one(mod, ins)
    assert 1 <= sig["marginPct"] <= 100, f"default marginPct {sig['marginPct']} not in [1,100]"


def test_emitted_leverage_is_score_tiered():
    """The >3% breakout stub scores >= 9 -> 5x tier; a smaller breakout -> 3x.
    Guards the score-tiered leverage port."""
    mod = _load_scan()
    sig = _emit_one(mod, _external_scanner_inputs())
    assert sig["leverage"] in (3, 5), f"leverage {sig['leverage']} not in score-tier set (3,5)"
