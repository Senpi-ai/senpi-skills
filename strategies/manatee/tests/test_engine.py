"""Manatee engine tests. Pure math + scan() smoke runs against a fake ctx (no network).
Run: python3 -m pytest strategies/manatee/tests -q"""
import os
import sys
import types

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402

_RT = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
INPUTS = _RT["scanners"][1]["inputs"]
SCHEMA = _RT["scanners"][1]["signal_data_schema"]
CLIP = float(INPUTS["marginPct"])


def _bar(t, px):
    return {"t": t, "o": str(px), "h": str(px), "l": str(px), "c": str(px), "v": "1000.0"}


def _flat(n=20, px=100.0):
    return [_bar(i, px) for i in range(n)]


def _closed(px, n=20):
    """n flat closed bars at 100 then one closed bar at px."""
    return _flat(n) + [_bar(n, px)]


# ── the stretch picks the side; nothing fires inside the band ──

def test_below_the_mean_is_long_above_is_short_inside_is_nothing():
    lo = scoring.build_thesis("DOT", _closed(94.0), INPUTS)
    hi = scoring.build_thesis("DOT", _closed(106.0), INPUTS)
    assert lo and lo["direction"] == "LONG" and lo["dev_pct"] < -5
    assert hi and hi["direction"] == "SHORT" and hi["dev_pct"] > 5
    assert scoring.build_thesis("DOT", _closed(97.0), INPUTS) is None
    assert scoring.build_thesis("DOT", _closed(103.0), INPUTS) is None


def test_too_little_history_is_nothing():
    assert scoring.build_thesis("DOT", _closed(94.0, n=6), INPUTS) is None
    assert scoring.build_thesis("DOT", _closed(94.0, n=8), INPUTS) is not None


def test_the_mean_excludes_the_bar_being_judged():
    """A 7-day mean of the seven bars BEFORE the close: the stretch is measured against them."""
    th = scoring.build_thesis("DOT", _closed(94.0), INPUTS)
    assert th["dev_pct"] == -6.0


def test_bands_follow_depth():
    assert scoring.build_thesis("DOT", _closed(94.0), INPUTS)["band"] == "base"
    assert scoring.build_thesis("DOT", _closed(92.0), INPUTS)["band"] == "deep"
    assert scoring.build_thesis("DOT", _closed(89.0), INPUTS)["band"] == "extreme"


def test_string_candle_values_are_read():
    th = scoring.build_thesis("DOT", _closed(94.0), INPUTS)
    assert th and th["ret_pct"] == -6.0


# ── marginPct is a PERCENT; a clip is never rescaled ──

def test_sizing_is_a_percent_and_the_same_for_entry_and_add():
    assert scoring.sizing_for(INPUTS) == (1, CLIP)
    assert scoring.sizing_for(INPUTS, add=True) == (1, float(INPUTS["addMarginPct"]))
    assert CLIP > 1.0   # min_budget resolves a slot only above 1; a sub-1 value reads as a pasted fraction


# ── the emitted data{} never carries a null and only declared keys ──

def test_signal_data_has_no_nulls_and_only_declared_keys():
    th = scoring.build_thesis("DOT", _closed(94.0), INPUTS)
    th["ret_pct"] = None
    data = scoring.signal_data(th, 1, "entry", 0)
    assert "retPct" not in data
    assert all(v is not None for v in data.values())
    assert set(data) <= set(SCHEMA)
    assert {"score", "direction", "leverage"} <= set(data)


# ── scan(): a fake MCP whose candles end with an IN-PROGRESS bar back at 100 ──

class _State:
    def __init__(self):
        self.h = []

    def last(self):
        return self.h[-1] if self.h else None

    def append(self, x):
        self.h.append(x)


class _Mcp:
    def __init__(self, held, px, listed=("BTC", "DOT", "UNI", "SOL"), account=10_000.0, bar_t=20):
        self.held, self.px, self.listed, self.account, self.bar_t = held, px, listed, account, bar_t

    def call_tool(self, name, args):
        if name == "strategy_get_clearinghouse_state":
            used = 100.0 * len(self.held)
            return {"data": {"main": {
                "marginSummary": {"accountValue": str(self.account), "totalMarginUsed": str(used)},
                "withdrawable": str(self.account - used),
                "assetPositions": [{"position": {"coin": c, "szi": str(sz), "entryPx": str(e), "marginUsed": "100.0"}}
                                   for c, (sz, e) in self.held.items()]}}}
        if name == "market_list_instruments":
            return {"data": {"instruments": [{"name": n, "is_delisted": False} for n in self.listed] + [{"name": "XRP", "is_delisted": True}]}}
        if name == "market_get_asset_data":
            px = self.px.get(args["asset"], 100.0)
            closed = _flat(20) + [_bar(self.bar_t, px)]
            return {"data": {"candles": {args["candle_intervals"][0]: closed + [_bar(self.bar_t + 1, 100.0)]}}}   # last = in progress
        return None


def _ctx(held, px, state=None, listed=("BTC", "DOT", "UNI", "SOL"), account=10_000.0, bar_t=20):
    return types.SimpleNamespace(senpi_mcp=_Mcp(held, px, listed, account, bar_t), state=state or _State(), wallet="0xwallet")


def test_scan_opens_both_sides_on_the_closed_bar_and_skips_the_band_and_delisted_names():
    inputs = {**INPUTS, "universe": ["BTC", "DOT", "UNI", "SOL", "XRP"]}
    out = scan.scan(inputs, _ctx({}, {"DOT": 94.0, "UNI": 106.5, "BTC": 98.0, "SOL": 93.0, "XRP": 90.0}))
    got = {o["asset"]: (o["direction"], o["marginPct"], o["leverage"], o["data"]["kind"]) for o in out}
    assert got == {"DOT": ("LONG", CLIP, 1, "entry"), "UNI": ("SHORT", CLIP, 1, "entry"), "SOL": ("LONG", CLIP, 1, "entry")}
    for o in out:
        assert all(v is not None for v in o["data"].values()) and set(o["data"]) <= set(SCHEMA)


def test_scan_acts_once_per_closed_bar():
    st = _State()
    assert len(scan.scan(INPUTS, _ctx({}, {"DOT": 94.0}, st))) == 1
    assert scan.scan(INPUTS, _ctx({}, {"DOT": 94.0}, st)) == []                   # same closed bar: already acted
    assert len(scan.scan(INPUTS, _ctx({}, {"DOT": 94.0}, st, bar_t=21))) == 1     # a new closed bar: judged again


def test_scan_adds_once_per_bar_only_while_the_move_deepens_against_an_under_water_position():
    st = _State()
    st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": 0, "margin": CLIP, "side": "LONG", "t": 0},
                                        "UNI": {"px": 106.5, "adds": 0, "margin": CLIP, "side": "SHORT", "t": 0}}})
    held = {"DOT": (10, 94.0), "UNI": (-10, 106.5), "SOL": (10, 93.0)}
    out = scan.scan(INPUTS, _ctx(held, {"DOT": 91.0, "UNI": 110.0, "SOL": 92.0}, st))
    got = {o["asset"]: (o["direction"], o["data"]["kind"], o["data"]["addIndex"], o["marginPct"]) for o in out}
    assert got == {"DOT": ("LONG", "add", 1, float(INPUTS["addMarginPct"])), "UNI": ("SHORT", "add", 1, float(INPUTS["addMarginPct"]))}   # SOL moved only 1%: no add
    assert st.last()["entries"]["DOT"]["margin"] == CLIP + float(INPUTS["addMarginPct"])
    assert scan.scan(INPUTS, _ctx(held, {"DOT": 88.0, "UNI": 114.0}, st)) == []                       # same bar: no second add
    out = scan.scan(INPUTS, _ctx({"DOT": (20, 92.5), "UNI": (-20, 108.2)}, {"DOT": 94.5, "UNI": 100.0}, st, bar_t=21))
    assert out == []                                                                                   # DOT above its cost, UNI back at the mean


def test_scan_never_adds_past_the_per_name_count_or_cap():
    st = _State()
    st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": int(INPUTS["maxAddsPerAsset"]), "margin": CLIP, "side": "LONG", "t": 0}}})
    assert scan.scan(INPUTS, _ctx({"DOT": (20, 92.5)}, {"DOT": 88.0}, st)) == []
    st = _State()
    st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": 0, "margin": float(INPUTS["maxMarginPctPerAsset"]), "side": "LONG", "t": 0}}})
    assert scan.scan(INPUTS, _ctx({"DOT": (20, 92.5)}, {"DOT": 88.0}, st)) == []


def test_scan_never_adds_against_a_flipped_signal():
    st = _State()
    st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": 0, "margin": CLIP, "side": "LONG", "t": 0}}})
    assert scan.scan(INPUTS, _ctx({"DOT": (10, 94.0)}, {"DOT": 107.0}, st)) == []   # DOT held long, now 7% ABOVE the mean: the DSL owns it


def test_scan_stands_down_when_the_clearinghouse_is_unreadable():
    ctx = _ctx({}, {"DOT": 94.0})
    ctx.senpi_mcp.call_tool = lambda name, args: (_ for _ in ()).throw(RuntimeError("boom")) if name == "strategy_get_clearinghouse_state" else None
    assert scan.scan(INPUTS, ctx) == []


def test_scan_stands_down_on_a_corrupt_clearinghouse_read():
    ctx = _ctx({}, {"DOT": 94.0})
    ctx.senpi_mcp.call_tool = lambda name, args: ({"data": {"main": {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "400"},
                                                                     "withdrawable": "600", "assetPositions": []}}}
                                                  if name == "strategy_get_clearinghouse_state" else None)
    assert scan.scan(INPUTS, ctx) == []


def test_scan_stands_down_when_a_clip_is_under_the_venue_minimum():
    """Free margin x the clip under the $12 minimum: the order would fail, so nothing is emitted."""
    tiny = 12.0 / (CLIP / 100.0) * 0.5
    assert scan.scan(INPUTS, _ctx({}, {"DOT": 94.0, "UNI": 106.5}, account=tiny)) == []
    assert len(scan.scan(INPUTS, _ctx({}, {"DOT": 94.0, "UNI": 106.5}, account=tiny * 4)) ) == 2


def test_scan_stops_when_free_margin_is_exhausted():
    inputs = {**INPUTS, "freeMarginHeadroom": 1.2 * CLIP / 100.0, "universe": ["DOT", "UNI", "SOL"]}   # budget = 1.2 clips
    out = scan.scan(inputs, _ctx({}, {"DOT": 94.0, "UNI": 106.5, "SOL": 93.0}))
    assert [o["asset"] for o in out] == ["DOT"]
