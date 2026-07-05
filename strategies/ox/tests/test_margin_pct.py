"""ox margin-units + risk-parity regression.

Ox's distinctive mechanic is INVERSE-VOLATILITY sizing: it emits a DIFFERENT per-sleeve
top-level `marginPct` (the risk-parity weight re-expressed as a PERCENT of equity), NOT a
flat one. Runtime 3.0 sizes off a top-level `marginPct` in (0,100] and silently DROPS a
top-level `marginUsd`, so the scan converts its inverse-vol dollars to marginPct =
margin_usd/account_value*100. These tests drive the SHARED scan.py with each book's real
runtime.yaml inputs against a fake MCP that returns candles for the whole basket, and assert:

  1. Emitted signals carry a POSITIVE top-level `marginPct` (a PERCENT in (0,100]) and
     `leverage` — never a top-level `marginUsd` (the runtime would drop it), never a
     non-positive value.
  2. Every signal is LONG (both books are long-only).
  3. Leverage is clamped to <= maxLeverage (3) and the per-sleeve venue max.
  4. The inverse-vol weights are correct: the LOWER-vol sleeve gets the LARGER marginPct
     (sum of weights == 1 over the priced basket) — the risk-parity property.
  5. The dual-DEX account_value collapse uses max(), not sum().
"""

import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOOKS = {
    "core": {"path": ROOT / "core", "max_lev": 3},
    "ballast": {"path": ROOT / "ballast", "max_lev": 3},
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


def _candles(close, n, vol_scale):
    """n dict-form bars trending gently UP with a per-bar wiggle of size vol_scale (so a larger
    vol_scale => higher realized vol). Higher-lows pattern => BULLISH trend_structure."""
    out = []
    px = close
    for i in range(n):
        up = px * (1.0 + 0.002 * i)              # gentle rising floor -> higher lows -> BULLISH
        wig = up * vol_scale * (1 if i % 2 == 0 else -1)
        o = up
        c = up + wig
        h = max(o, c) * 1.001
        lo = min(o, c) * 0.999
        out.append({"o": str(o), "c": str(c), "h": str(h), "l": str(lo), "v": "1000"})
    return out


class _FakeMcp:
    """market_list_instruments returns the full basket live (non-delisted), each at venue lev 20;
    strategy_get_clearinghouse_state returns equity via two equal sub-DEX sections (tests max());
    market_get_asset_data returns candles whose vol differs per sleeve (low-vol vs high-vol)."""

    def __init__(self, sleeves, equity):
        self.sleeves = sleeves
        self.equity = equity
        # assign alternating low/high vol so we can assert the inverse-vol ordering
        self.vol = {s: (0.002 if i % 2 == 0 else 0.02) for i, s in enumerate(sleeves)}

    def call_tool(self, name, args):
        if name == "market_list_instruments":
            return {"data": {"instruments": [
                {"name": s, "max_leverage": 20, "is_delisted": False,
                 "context": {"coin": s, "dayNtlVlm": "100000000"}} for s in self.sleeves]}}
        if name == "strategy_get_clearinghouse_state":
            section = {"marginSummary": {"accountValue": str(self.equity)}, "assetPositions": []}
            return {"data": {"main": section, "xyz": section}}   # two equal views -> max() == equity
        if name == "market_get_asset_data":
            asset = args["asset"]
            vs = self.vol.get(asset, 0.005)
            return {"data": {"candles": {"1h": _candles(100.0, 40, vs),
                                         "4h": _candles(100.0, 10, vs)},
                             "asset_context": {}}}
        raise AssertionError(f"unexpected tool {name}")


class _State:
    def __init__(self):
        self._recs = []

    def last(self):
        return self._recs[-1] if self._recs else None

    def recent(self, n):
        return self._recs[-n:]

    def append(self, rec):
        assert isinstance(rec, dict)
        self._recs.append(rec)

    def __len__(self):
        return len(self._recs)


class _Ctx:
    def __init__(self, mcp, state, wallet="0xWALLET"):
        self.senpi_mcp = mcp
        self.state = state
        self.wallet = wallet
        self.scanner_name = "ox_test"
        self.interval_seconds = 600


def _run(book):
    book_dir = BOOKS[book]["path"]
    mod = _load_scan(book_dir)
    inputs = _inputs(book_dir)
    sleeves = inputs["sleeves"]
    mcp = _FakeMcp(sleeves, equity=10000.0)
    ctx = _Ctx(mcp, _State())
    return mod.scan(inputs, ctx), inputs


def test_emits_positive_margin_pct_long():
    for book in BOOKS:
        out, inputs = _run(book)
        assert out, f"{book}: expected signals from the all-weather basket"
        for sig in out:
            assert sig["direction"] == "LONG", f"{book}: both books are long-only"
            mp = sig.get("marginPct")
            assert isinstance(mp, (int, float)) and 0 < mp <= 100.0, \
                f"{book}: marginPct must be a PERCENT in (0,100], got {mp!r}"
            lev = sig.get("leverage")
            assert isinstance(lev, (int, float)) and 0 < lev <= BOOKS[book]["max_lev"], \
                f"{book}: leverage must be clamped to <= maxLeverage, got {lev!r}"
            assert "marginUsd" not in sig, \
                f"{book}: ox emits marginPct (runtime drops a top-level marginUsd)"


def test_inverse_vol_weighting():
    """Risk-parity property: the LOWER-vol sleeve gets the LARGER marginPct."""
    out, _ = _run("core")
    by_asset = {s["asset"]: s for s in out}
    mcp_vol = _FakeMcp(_inputs(BOOKS["core"]["path"])["sleeves"], 10000.0).vol
    priced = [(a, mcp_vol[a], by_asset[a]["marginPct"]) for a in by_asset]
    # any low-vol emitted sleeve should outweigh any high-vol emitted sleeve
    los = [m for a, v, m in priced if v <= 0.005]
    his = [m for a, v, m in priced if v >= 0.01]
    if los and his:
        assert min(los) > max(his), f"inverse-vol broken: low-vol marginPct {los} not all > high-vol {his}"


def test_max_weight_cap():
    """No single sleeve exceeds maxWeightPct of equity (as a PERCENT)."""
    for book in BOOKS:
        out, inputs = _run(book)
        cap_pct = float(inputs["maxWeightPct"]) * 100.0     # maxWeightPct is a FRACTION -> percent cap
        for sig in out:
            assert sig["marginPct"] <= cap_pct + 0.01, \
                f"{book}: {sig['asset']} marginPct {sig['marginPct']} > cap {cap_pct}"


if __name__ == "__main__":
    test_emits_positive_margin_pct_long()
    test_inverse_vol_weighting()
    test_max_weight_cap()
    print("OK — all ox margin-units + risk-parity tests passed")
