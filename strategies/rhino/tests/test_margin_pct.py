"""rhino margin-units + per-book wiring regression.

v3.x runtime sizes (marginPct/100)*withdrawable, so per-signal `marginPct` MUST be a
PERCENT in (0,100], never a v2 fraction (the v2 producer carried marginPct 0.10 / 0.22
fractions; here they become 10 / 22 percents). These tests drive the SHARED scan.py with
each book's real runtime.yaml inputs through a fake MCP, and assert:

  - the emitted wire `marginPct` is the book's declared percent and lands in [1,100],
  - the in-code default fallback is also a percent,
  - both runtime.yamls declare marginPct >= 1,
  - the hedge book emits LONG defensives; the escalation book emits BOTH directions and
    only fires when the fake feed makes the stress detector trip,
  - the emitted per-signal leverage is the flat 5x clamp (v2 is NOT tiered).
"""

import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOOKS = {
    "hedge": {"path": ROOT / "hedge", "expect": 10},
    "escalation": {"path": ROOT / "escalation", "expect": 22},
}


def _load_scan(book_dir):
    scanners = book_dir / "scanners"
    import sys
    sys.path.insert(0, str(scanners))
    try:
        spec = importlib.util.spec_from_file_location(f"scan_{book_dir.name}", scanners / "scan.py")
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


class _FakeMcp:
    """Feeds candles that (a) trip the stress detector hard (every probe + vol fires) and
    (b) make every traded name confirm its mandated direction. A monotone-UP series gives
    crisis longs + defensives a BULLISH 4h; a monotone-DOWN series gives risk shorts a
    BEARISH 4h. The list-instruments call returns a board containing every rhino asset."""

    _UP = [[i, c, c + 1, c - 1, c, 1] for i, c in enumerate(range(100, 140, 2))]      # 20 bars up
    _DOWN = [[i, c, c + 1, c - 1, c, 1] for i, c in enumerate(range(140, 100, -2))]   # 20 bars down

    _CRISIS = {"XYZ:GOLD", "XYZ:SILVER", "XYZ:BRENTOIL", "XYZ:CL", "XYZ:NATGAS", "XYZ:JPY"}
    _RISK = {"BTC", "ETH", "SOL", "HYPE", "SUI", "XYZ:XYZ100", "XYZ:SP500"}

    def call_tool(self, name, args):
        if name == "market_list_instruments":
            allnames = self._CRISIS | self._RISK
            return {"instruments": [{"name": n if n.startswith("XYZ") is False else n.replace("XYZ:", "xyz:"),
                                     "max_leverage": 20} for n in allnames]}
        if name == "strategy_get_clearinghouse_state":
            return {"main": {"marginSummary": {"accountValue": 10000, "totalMarginUsed": 0,
                                               "totalNtlPos": 0}, "assetPositions": []},
                    "xyz": {"marginSummary": {"accountValue": 10000}, "assetPositions": []}}
        if name == "market_get_asset_data":
            asset = (args.get("asset") or "").upper()
            # risk assets + the equities stress probe go DOWN; everything else UP.
            down = asset in self._RISK or asset in ("XYZ:XYZ100", "XYZ:SP500")
            series = self._DOWN if down else self._UP
            return {"data": {"candles": {"1h": series, "4h": series},
                             "asset_context": {"markPx": series[-1][4], "prevDayPx": series[0][4]}}}
        return {}


class _FakeState:
    def __init__(self):
        self._records = []

    def last(self):
        return self._records[-1] if self._records else None

    def recent(self, n):
        return self._records[-n:]

    def append(self, rec):
        self._records.append(rec)

    def __len__(self):
        return len(self._records)


class _FakeCtx:
    def __init__(self):
        self.senpi_mcp = _FakeMcp()
        self.state = _FakeState()
        self.wallet = "0xtest"
        self.scanner_name = "test"
        self.interval_seconds = 300


def _run(mod, inputs):
    return mod.scan(dict(inputs), _FakeCtx())


def test_each_book_emits_percent_in_range():
    for name, cfg in BOOKS.items():
        mod = _load_scan(cfg["path"])
        out = _run(mod, _inputs(cfg["path"]))
        assert out, f"{name}: scan emitted no candidates (fake feed should fire)"
        for sig in out:
            assert 1 <= sig["marginPct"] <= 100, f"{name}: marginPct {sig['marginPct']} not in [1,100]"
            assert sig["marginPct"] == cfg["expect"], f"{name}: expected {cfg['expect']}, got {sig['marginPct']}"


def test_leverage_is_flat_5x_clamp():
    """v2 is NOT tiered: every emitted signal carries leverage = min(maxLeverage, venue_max).
    Fake venue max is 20, maxLeverage 5 -> every signal must be exactly 5."""
    for name, cfg in BOOKS.items():
        mod = _load_scan(cfg["path"])
        out = _run(mod, _inputs(cfg["path"]))
        for sig in out:
            assert sig["leverage"] == 5, f"{name}: expected flat 5x, got {sig['leverage']}"


def test_hedge_is_long_only_escalation_both_directions():
    hedge = _load_scan(BOOKS["hedge"]["path"])
    h_out = _run(hedge, _inputs(BOOKS["hedge"]["path"]))
    assert h_out and all(s["direction"] == "LONG" for s in h_out), "hedge must be LONG-only"

    # Lift the slot cap so every gated candidate emits — with the default 3 slots and a
    # tie on score, the stable sort fills all slots from the LONG crisis list first (which
    # is faithful v2 behaviour). Raising maxSlots proves the SHORT risk path is reachable.
    esc = _load_scan(BOOKS["escalation"]["path"])
    ins = dict(_inputs(BOOKS["escalation"]["path"]))
    ins["maxSlots"] = 99
    e_out = esc.scan(ins, _FakeCtx())
    dirs = {s["direction"] for s in e_out}
    assert "LONG" in dirs and "SHORT" in dirs, f"escalation must trade both directions, got {dirs}"


def test_escalation_dormant_without_stress():
    """With a flat (no-trend) feed the stress detector should not trip, so the escalation
    book stays dormant and emits nothing."""
    class _CalmMcp(_FakeMcp):
        _FLAT = [[i, 100, 100, 100, 100, 1] for i in range(20)]

        def call_tool(self, name, args):
            if name == "market_get_asset_data":
                return {"data": {"candles": {"1h": self._FLAT, "4h": self._FLAT},
                                 "asset_context": {"markPx": 100, "prevDayPx": 100}}}
            return super().call_tool(name, args)

    esc = _load_scan(BOOKS["escalation"]["path"])
    ctx = _FakeCtx()
    ctx.senpi_mcp = _CalmMcp()
    out = esc.scan(dict(_inputs(BOOKS["escalation"]["path"])), ctx)
    assert out == [], f"escalation should be dormant without stress, emitted {out}"


def test_runtime_yaml_margin_is_percent():
    for name, cfg in BOOKS.items():
        mpct = _inputs(cfg["path"])["marginPct"]
        assert 1 <= mpct <= 100, f"{name}: runtime.yaml marginPct {mpct} not a PERCENT in [1,100]"


def test_scan_default_margin_pct_is_percent():
    """The in-code inputs.get('marginPct', N) default must also be a percent."""
    import re
    src = (BOOKS["hedge"]["path"] / "scanners" / "scan.py").read_text()
    m = re.search(r'inputs\.get\(\s*"marginPct"\s*,\s*([0-9.]+)\s*\)', src)
    assert m, "scan.py marginPct default not found"
    assert 1 <= float(m.group(1)) <= 100, f"scan.py default marginPct {m.group(1)} not in [1,100]"
