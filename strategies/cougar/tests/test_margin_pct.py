"""cougar margin-units + scan regression.

v3.0.x runtime sizes (marginPct/100)*withdrawable, so per-signal `marginPct` MUST be a
PERCENT in (0,100], never a fraction (the v2 producer used marginPct=0.20 as a FRACTION and
computed the dollars itself; the 3.0 scan emits the PERCENT and the runtime sizes). These
tests drive the SHARED scan.py with each book's real runtime.yaml inputs and assert the
emitted wire marginPct lands in [1,100], leverage is venue-clamped, and direction matches
the leg.
"""

import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOOKS = {
    "long": {"path": ROOT / "long", "leg": "long", "dir": "LONG", "expect_pct": 20},
    "short": {"path": ROOT / "short", "leg": "short", "dir": "SHORT", "expect_pct": 20},
}


def _load_scan(book_dir):
    scanners = book_dir / "scanners"
    import sys

    sys.path.insert(0, str(scanners))
    try:
        spec = importlib.util.spec_from_file_location(
            f"scan_{book_dir.name}", scanners / "scan.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(scanners))


def _inputs(book_dir):
    rt = yaml.safe_load((book_dir / "runtime.yaml").read_text())
    for s in rt["scanners"]:
        if s.get("type") == "external_scanner":
            return s["inputs"]
    raise AssertionError("no external_scanner inputs")


def _candles(direction, n=10, base=100.0):
    """Monotone candles guaranteeing a strong trend in `direction`."""
    out = []
    for i in range(n):
        c = base + (i if direction == "UP" else -i)
        out.append([i, c, c + 1, c - 1, c, 1000])
    return out


class _FakeMcp:
    """Serves clearinghouse + instrument board + per-asset candles so a dispersion
    candidate clears the gates for the given leg."""

    def __init__(self, leg, universe):
        self.leg = leg
        self.universe = universe

    def call_tool(self, name, args):
        if name == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"marginSummary": {"accountValue": 100000.0,
                                                        "totalMarginUsed": 0.0,
                                                        "totalNtlPos": 0.0},
                                      "assetPositions": []},
                             "xyz": {"marginSummary": {"accountValue": 100000.0},
                                     "assetPositions": []}}}
        if name == "market_list_instruments":
            insts = []
            # spread the universe across distinct 24h returns so there IS dispersion
            for i, u in enumerate(self.universe):
                ret = (i - len(self.universe) / 2) * 2.0     # -N..+N percent
                insts.append({
                    "name": u,
                    "max_leverage": 10,
                    "is_delisted": False,
                    "context": {"markPx": 100 + ret, "prevDayPx": 100,
                                "dayNtlVlm": 5_000_000},
                })
            return {"data": {"instruments": insts}}
        if name == "market_get_asset_data":
            asset = args.get("asset", "")
            # the strongest name trends UP (for long), weakest trends DOWN (for short)
            idx = self.universe.index(asset) if asset in self.universe else 0
            up = idx >= len(self.universe) / 2
            d = "UP" if up else "DOWN"
            return {"data": {"candles": {"1h": _candles(d), "4h": _candles(d)}}}
        return None


class _FakeState:
    def __init__(self):
        self._rec = []

    def last(self):
        return self._rec[-1] if self._rec else None

    def append(self, r):
        self._rec.append(r)


class _FakeCtx:
    def __init__(self, leg, universe):
        self.senpi_mcp = _FakeMcp(leg, universe)
        self.state = _FakeState()
        self.wallet = "0xCOUGARTEST"
        self.scanner_name = f"cougar_{leg}_signals"
        self.interval_seconds = 300


def _run(mod, inputs, leg):
    ins = dict(inputs)
    ins["minScore"] = 0  # ensure a candidate isn't dropped on score alone
    out = mod.scan(ins, _FakeCtx(leg, ins["equities"]))
    return out


def test_each_book_emits_percent_in_range():
    for name, cfg in BOOKS.items():
        mod = _load_scan(cfg["path"])
        out = _run(mod, _inputs(cfg["path"]), cfg["leg"])
        assert out, f"{name}: scan emitted no candidates (dispersion stub failed)"
        for sig in out:
            mpct = sig["marginPct"]
            assert 1 <= mpct <= 100, f"{name}: marginPct {mpct} not in [1,100]"
            assert mpct == cfg["expect_pct"], f"{name}: expected {cfg['expect_pct']}, got {mpct}"
            assert sig["direction"] == cfg["dir"], f"{name}: wrong direction {sig['direction']}"
            assert 1 <= sig["leverage"] <= 5, f"{name}: leverage {sig['leverage']} breached clamp"


def test_runtime_yaml_margin_is_percent():
    for name, cfg in BOOKS.items():
        mpct = _inputs(cfg["path"])["marginPct"]
        assert 1 <= mpct <= 100, f"{name}: runtime.yaml marginPct {mpct} not a percent"


def test_universe_is_xyz_only():
    for name, cfg in BOOKS.items():
        for t in _inputs(cfg["path"])["equities"]:
            assert t.lower().startswith("xyz:"), f"{name}: non-xyz ticker {t}"
