"""asia-ai margin-units regression.

v3.0.4 runtime sizes (marginPct/100)*withdrawable, so per-signal `marginPct` MUST be
a PERCENT in (0,100], never a fraction. asia-ai previously emitted 0.12 (main) / 0.10
(hedge) -> ~100x undersize, silent (no 400). These tests drive the SHARED scan.py with
each sleeve's real runtime.yaml inputs and assert the emitted wire marginPct lands in
[1,100], plus cover the in-code default fallback.
"""

import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SLEEVES = {
    "main": {"path": ROOT / "main", "expect": 12},
    "hedge": {"path": ROOT / "hedge", "expect": 10},
}


def _load_scan(sleeve_dir):
    """Import the sleeve's own scan.py (with its sibling scoring.py importable)."""
    scanners = sleeve_dir / "scanners"
    import sys

    sys.path.insert(0, str(scanners))
    try:
        spec = importlib.util.spec_from_file_location(
            f"scan_{sleeve_dir.name}", scanners / "scan.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(scanners))


def _inputs(sleeve_dir):
    rt = yaml.safe_load((sleeve_dir / "runtime.yaml").read_text())
    for s in rt["scanners"]:
        if s.get("type") == "external_scanner":
            return s["inputs"]
    raise AssertionError("no external_scanner inputs")


class _FakeMcp:
    """Returns candles that guarantee confirm_trend fires for the given want trend."""

    def __init__(self, want):
        self.want = want

    def call_tool(self, name, args):
        # 8 monotone candles -> strong trend on both 4h and 1d in the wanted direction.
        if self.want == "UP":
            closes = [100, 102, 104, 106, 108, 110, 112, 114]
        else:
            closes = [114, 112, 110, 108, 106, 104, 102, 100]
        candles = [[i, c, c, c, c, 1] for i, c in enumerate(closes)]
        return {"data": {"candles": {"4h": candles, "1d": candles}}}


class _FakeCtx:
    def __init__(self, want):
        self.senpi_mcp = _FakeMcp(want)
        self.state = None


def _emit(mod, inputs):
    """Run scan with one asset forced through; return its emitted marginPct."""
    ins = dict(inputs)
    ins["universe"] = ["xyz:TEST"]
    ins["minScore"] = 0  # ensure the candidate isn't filtered on score
    want = (ins.get("wantTrend", "UP") or "UP").upper()
    out = mod.scan(ins, _FakeCtx(want))
    assert out, "scan emitted no candidates (trend stub failed)"
    return out[0]["marginPct"]


def test_each_sleeve_emits_percent_in_range():
    for name, cfg in SLEEVES.items():
        mod = _load_scan(cfg["path"])
        mpct = _emit(mod, _inputs(cfg["path"]))
        assert 1 <= mpct <= 100, f"{name}: emitted marginPct {mpct} not in [1,100]"
        assert mpct == cfg["expect"], f"{name}: expected {cfg['expect']}, got {mpct}"


def test_default_fallback_is_percent():
    """Empty inputs -> in-code default must also be a percent in [1,100], not a fraction."""
    for name, cfg in SLEEVES.items():
        mod = _load_scan(cfg["path"])
        ins = _inputs(cfg["path"])
        ins = {k: v for k, v in ins.items() if k != "marginPct"}  # drop config knob
        mpct = _emit(mod, ins)
        assert 1 <= mpct <= 100, f"{name}: default marginPct {mpct} not in [1,100]"


def test_runtime_yaml_margin_is_percent():
    """Config-level guard: both runtime.yamls must declare marginPct >= 1."""
    for name, cfg in SLEEVES.items():
        mpct = _inputs(cfg["path"])["marginPct"]
        assert mpct >= 1, f"{name}: runtime.yaml marginPct {mpct} < 1 (fraction, not percent)"
        assert mpct <= 100, f"{name}: runtime.yaml marginPct {mpct} > 100"
