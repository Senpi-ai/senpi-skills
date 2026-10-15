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


def _flat(n=70, px=100.0):
    return [_bar(i, px) for i in range(n)]


def _closed(px, n=70):
    """n flat closed bars at 100 then one closed bar at px. n clears minHistoryBars (60): a name
    with less history than that is not judged at all."""
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
    def __init__(self, held, px, listed=("BTC", "DOT", "UNI", "SOL"), account=10_000.0, bar_t=70,
                 margin_used=None, oi=None):
        self.held, self.px, self.listed, self.account, self.bar_t = held, px, listed, account, bar_t
        # OI per name, descending in the order given, so derive_universe's ranking is deterministic
        self.oi = oi or {n: 9e8 - i for i, n in enumerate(listed)}
        self.margin_used = margin_used or {}   # name -> marginUsed, so a position can be shown at a
                                               # size this scanner never emitted (a double fill)

    def call_tool(self, name, args):
        if name == "strategy_get_clearinghouse_state":
            used = 100.0 * len(self.held)
            return {"data": {"main": {
                "marginSummary": {"accountValue": str(self.account), "totalMarginUsed": str(used)},
                "withdrawable": str(self.account - used),
                "assetPositions": [{"position": {"coin": c, "szi": str(sz), "entryPx": str(e),
                                                 "marginUsed": str(self.margin_used.get(c, 100.0))}}
                                   for c, (sz, e) in self.held.items()]}}}
        if name == "market_list_instruments":
            rows = [{"name": n, "is_delisted": False,
                     "context": {"openInterest": str(self.oi.get(n, 9e8)), "markPx": "1.0"}}
                    for n in self.listed]
            rows.append({"name": "XRP", "is_delisted": True,
                         "context": {"openInterest": "9e8", "markPx": "1.0"}})
            return {"data": {"instruments": rows}}
        if name == "market_get_asset_data":
            px = self.px.get(args["asset"], 100.0)
            closed = _flat(70) + [_bar(self.bar_t, px)]   # 71 closed bars clears minHistoryBars
            return {"data": {"candles": {args["candle_intervals"][0]: closed + [_bar(self.bar_t + 1, 100.0)]}}}   # last = in progress
        return None


def _ctx(held, px, state=None, listed=("BTC", "DOT", "UNI", "SOL"), account=10_000.0, bar_t=70,
         margin_used=None, oi=None):
    return types.SimpleNamespace(senpi_mcp=_Mcp(held, px, listed, account, bar_t, margin_used, oi),
                                 state=state or _State(), wallet="0xwallet")


def test_scan_opens_both_sides_on_the_closed_bar_and_skips_the_band_and_delisted_names():
    out = scan.scan(INPUTS, _ctx({}, {"DOT": 94.0, "UNI": 106.5, "BTC": 98.0, "SOL": 93.0, "XRP": 90.0},
                                listed=("BTC", "DOT", "UNI", "SOL")))
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
    st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": 0, "side": "LONG", "t": 0},
                                        "UNI": {"px": 106.5, "adds": 0, "side": "SHORT", "t": 0}}})
    held = {"DOT": (10, 94.0), "UNI": (-10, 106.5), "SOL": (10, 93.0)}
    out = scan.scan(INPUTS, _ctx(held, {"DOT": 91.0, "UNI": 110.0, "SOL": 92.0}, st))
    got = {o["asset"]: (o["direction"], o["data"]["kind"], o["data"]["addIndex"], o["marginPct"]) for o in out}
    assert got == {"DOT": ("LONG", "add", 1, float(INPUTS["addMarginPct"])), "UNI": ("SHORT", "add", 1, float(INPUTS["addMarginPct"]))}   # SOL moved only 1%: no add
    assert st.last()["entries"]["DOT"]["adds"] == 1 and "margin" not in st.last()["entries"]["DOT"]
    assert scan.scan(INPUTS, _ctx(held, {"DOT": 88.0, "UNI": 114.0}, st)) == []                       # same bar: no second add
    out = scan.scan(INPUTS, _ctx({"DOT": (20, 92.5), "UNI": (-20, 108.2)}, {"DOT": 94.5, "UNI": 100.0}, st, bar_t=21))
    assert out == []                                                                                   # DOT above its cost, UNI back at the mean


def test_scan_never_adds_past_the_per_name_count():
    st = _State()
    st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": int(INPUTS["maxAddsPerAsset"]), "side": "LONG", "t": 0}}})
    assert scan.scan(INPUTS, _ctx({"DOT": (20, 92.5)}, {"DOT": 88.0}, st)) == []


def test_the_per_name_cap_is_read_from_the_book_not_from_what_the_scanner_emitted():
    """The cap is the only thing bounding a book that adds into losers, so it must see the position
    that EXISTS. A tally of emitted signals cannot: FEE_OPTIMIZED_LIMIT occasionally double-fills,
    and a name carrying two clips while the tally says one would keep collecting adds on top.

    Same state both times — one add recorded. Only the book differs."""
    def _try(margin_used):
        st = _State()
        st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": 1, "side": "LONG", "t": 0}}})
        return scan.scan(INPUTS, _ctx({"DOT": (20, 92.5)}, {"DOT": 88.0}, st, margin_used=margin_used))

    assert [o["asset"] for o in _try({"DOT": 100.0})] == ["DOT"]   # 1.0% of account + a 1.5% clip < 4.5
    assert _try({"DOT": 320.0}) == []                              # 3.2% + 1.5% > the 4.5% cap: no add


def test_a_double_filled_name_stops_collecting_adds():
    """The concrete case: the scanner emitted one clip, two filled. The ledger would still read one."""
    st = _State()
    st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": 0, "side": "LONG", "t": 0}}})
    doubled = float(INPUTS["maxMarginPctPerAsset"]) / 100.0 * 10_000.0   # already at the cap in USD
    assert scan.scan(INPUTS, _ctx({"DOT": (20, 92.5)}, {"DOT": 88.0}, st, margin_used={"DOT": doubled})) == []


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


def test_scan_stops_when_free_margin_is_exhausted_and_serves_the_deepest_stretch_first():
    """Serving `universe` in order meant the names typed first were funded on every tight day and
    the tail never was — a fixed bias, not a tie-break. SOL is 7% from its mean, DOT 6%, UNI 6.5%:
    one clip of budget buys SOL."""
    inputs = {**INPUTS, "freeMarginHeadroom": 1.2 * CLIP / 100.0}
    out = scan.scan(inputs, _ctx({}, {"DOT": 94.0, "UNI": 106.5, "SOL": 93.0}, listed=("DOT", "UNI", "SOL")))
    assert [o["asset"] for o in out] == ["SOL"]


def test_the_last_slot_goes_to_the_deepest_stretch_not_the_first_in_the_list():
    """Same ordering guarantee on slots rather than margin."""
    inputs = {**INPUTS, "maxSlots": 1}
    out = scan.scan(inputs, _ctx({}, {"DOT": 94.0, "UNI": 106.5, "SOL": 93.0}, listed=("DOT", "UNI", "SOL")))
    assert [o["asset"] for o in out] == ["SOL"]


def test_a_name_skipped_for_margin_is_retried_rather_than_marked_judged():
    inputs = {**INPUTS, "freeMarginHeadroom": 1.2 * CLIP / 100.0}
    st = _State()
    assert [o["asset"] for o in scan.scan(inputs, _ctx({}, {"DOT": 94.0, "SOL": 93.0}, st, listed=("DOT", "SOL")))] == ["SOL"]
    assert "DOT" not in st.last()["acted"]          # not judged — it never got a decision
    assert "DOT" in [o["asset"] for o in scan.scan(INPUTS, _ctx({"SOL": (10, 93.0)}, {"DOT": 94.0}, st, listed=("DOT", "SOL")))]


def test_a_round_trip_counts_the_builder_fee_on_both_legs():
    """6 bps was the venue's half. The hurdle built on it was half the real one."""
    assert scoring.roundtrip_fee_pct(INPUTS) == (1.5 + 4.06 + 4.5 + 4.06) / 100.0
    assert scoring.roundtrip_fee_pct(INPUTS) > 0.14
    # inert at the shipped 3% harvest, by design — but now against the real number
    assert float(INPUTS["expectedCapturePct"]) > scoring.roundtrip_fee_pct(INPUTS) * float(INPUTS["feeCoverMultiple"])


def test_the_drawdown_halt_sits_beyond_the_worst_replayed_drawdown():
    """-26.1% (Jul 2024 - Jun 2025, 4h-close arming) is the worst the replay produced. The halt is
    LATCHED, so one set inside that range does not shade the result — it ends the run."""
    assert float(_RT["risk"]["guard_rails"]["drawdown_halt_pct"]) > 26.1


# ── the universe is read off the venue, not typed ──

def test_derive_universe_ranks_by_open_interest_and_applies_the_floor():
    insts = {"BTC": {"venue_name": "BTC", "oi_usd": 3.2e9, "delisted": False},
             "DOT": {"venue_name": "DOT", "oi_usd": 5e6, "delisted": False},     # under the floor
             "SOL": {"venue_name": "SOL", "oi_usd": 5.9e8, "delisted": False},
             "DEAD": {"venue_name": "DEAD", "oi_usd": 9e9, "delisted": True}}
    assert scan.derive_universe(insts, 5e7, 24) == ["BTC", "SOL"]
    assert scan.derive_universe(insts, 5e7, 1) == ["BTC"]
    assert scan.derive_universe(insts, 0, 24) == ["BTC", "SOL", "DOT"]           # no floor: all live


def test_derive_universe_returns_the_venues_own_spelling():
    """kSHIB does not survive upper(), and `asset` is what the order carries — returning the KEY
    would emit KSHIB and be refused as an unknown asset."""
    insts = {"KSHIB": {"venue_name": "kSHIB", "oi_usd": 9e8, "delisted": False}}
    assert scan.derive_universe(insts, 5e7, 24) == ["kSHIB"]


def test_derive_universe_excludes_the_equities_book():
    insts = {"BTC": {"venue_name": "BTC", "oi_usd": 3e9, "delisted": False},
             "XYZ:NVDA": {"venue_name": "xyz:NVDA", "oi_usd": 9e9, "delisted": False}}
    assert scan.derive_universe(insts, 5e7, 24) == ["BTC"]


def test_an_unreadable_instrument_list_trades_nothing_rather_than_an_unvetted_board():
    """None and {} are opposite facts once the universe is derived from the read."""
    ctx = _ctx({}, {"DOT": 94.0})
    real = ctx.senpi_mcp.call_tool
    ctx.senpi_mcp.call_tool = lambda n, a: (None if n == "market_list_instruments" else real(n, a))
    assert scan.scan(INPUTS, ctx) == []


def test_a_name_under_the_floor_is_not_opened_but_a_held_one_is_still_managed():
    """The floor selects what to START. A position already on keeps its adds — the DSL owns its
    exit, and dropping it from scope would only stop it being scaled."""
    thin = {"DOT": 5e6, "SOL": 9e8}
    assert [o["asset"] for o in scan.scan(INPUTS, _ctx({}, {"DOT": 94.0, "SOL": 93.0},
                                                       listed=("DOT", "SOL"), oi=thin))] == ["SOL"]
    st = _State()
    st.append({"acted": {}, "entries": {"DOT": {"px": 94.0, "adds": 0, "side": "LONG", "t": 0}}})
    out = scan.scan(INPUTS, _ctx({"DOT": (10, 94.0)}, {"DOT": 91.0}, st, listed=("DOT", "SOL"), oi=thin))
    assert [o["asset"] for o in out] == ["DOT"] and out[0]["data"]["kind"] == "add"


def test_a_freshly_listed_name_is_not_judged_until_it_has_history():
    """minHistoryBars: the venue-wide measurement behind the OI floor excluded late listings, and
    they underperformed by about 17% — so a name the measurement never saw is not traded."""
    short = _ctx({}, {"DOT": 94.0}, listed=("DOT",))
    short.senpi_mcp.bar_t = 20
    real = short.senpi_mcp.call_tool

    def few(n, a):
        if n == "market_get_asset_data":
            return {"data": {"candles": {a["candle_intervals"][0]: _flat(20) + [_bar(20, 94.0), _bar(21, 100.0)]}}}
        return real(n, a)
    short.senpi_mcp.call_tool = few
    assert scan.scan(INPUTS, short) == []
