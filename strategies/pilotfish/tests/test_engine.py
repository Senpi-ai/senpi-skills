"""Pilotfish engine tests: the gate over fixture dicts (no network), the hit-rate ledger, the
headcount, the shipped config, and a scan() smoke run against a fake ctx.
Run: python3 -m pytest strategies/pilotfish/tests -q  or  python3 strategies/pilotfish/tests/test_engine.py"""
import math
import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME = os.path.join(_HERE, "..", "main", "runtime.yaml")
sys.path.insert(0, os.path.join(_HERE, "..", "main", "scanners"))
for _m in ("scoring", "scan"):          # never inherit another package's module of the same name
    sys.modules.pop(_m, None)
import scoring  # noqa: E402
import scan  # noqa: E402

_INPUTS = yaml.safe_load(open(_RUNTIME))["scanners"][1]["inputs"]     # the shipped config


# ── (a) THE GATE — Phalanx's engine over fixture dicts ──

def test_gate_refuses_one_wallet_and_a_thin_lean_and_accepts_a_growing_one_sided_name():
    inp = {"tiltThreshold": 65, "deltaMin": 2}
    acc = scoring.new_accuracy_state()
    # +1: one wallet joined a one-sided name (13L/7S -> 14L/7S, 66.7%) — noise, not an accumulation
    assert scan.fresh_picks({"BTC": {"long_n": 14, "short_n": 7}},
                            {"BTC": {"long_n": 13, "short_n": 7}}, acc, inp) == []
    # 30L/26S is 54% one-sided: never trades, however fast it grew (+4 here). The old
    # notional bias could widen on the same wallets' mark-to-market drift.
    assert scan.fresh_picks({"ETH": {"long_n": 30, "short_n": 26}},
                            {"ETH": {"long_n": 26, "short_n": 26}}, acc, inp) == []
    # +2 at 65%+ with >= 10 wallets positioned (13L/7S -> 15L/7S, 68.2%) -> fires, dominant side
    picks = scan.fresh_picks({"BTC": {"long_n": 15, "short_n": 7, "raw_coin": "BTC"}},
                             {"BTC": {"long_n": 13, "short_n": 7}}, acc, inp)
    assert [(p["asset"], p["direction"], p["count"], p["delta"]) for p in picks] == [("BTC", "LONG", 15, 2.0)]
    assert picks[0]["conviction"] == 68.2
    # A RATIO IS NOT EVIDENCE WITHOUT A SAMPLE: under the 10-wallet min sample one_sidedness
    # is 0 -> refused however aligned (5L/0S, +5)
    assert scan.fresh_picks({"SOL": {"long_n": 5, "short_n": 0}}, {}, acc, inp) == []


def test_gate_takes_the_short_side_and_ranks_by_one_sidedness():
    inp = {"tiltThreshold": 65, "deltaMin": 2}
    acc = scoring.new_accuracy_state()
    cur = {"ETH": {"long_n": 4, "short_n": 18, "raw_coin": "ETH"},         # 81.8% short, net -14
           "NVDA": {"long_n": 15, "short_n": 7, "raw_coin": "xyz:NVDA"}}   # 68.2% long, net +8
    prev = {"ETH": {"long_n": 5, "short_n": 15},                           # net -10 -> SHORT delta +4
            "NVDA": {"long_n": 13, "short_n": 7}}                          # net +6  -> LONG delta +2
    picks = scan.fresh_picks(cur, prev, acc, inp)
    assert [(p["asset"], p["direction"]) for p in picks] == [("ETH", "SHORT"), ("xyz:NVDA", "LONG")]
    assert picks[0]["delta"] == 4.0 and picks[0]["count"] == 18


def test_the_minority_side_is_unpickable():
    """A 13-vs-29 minority long once fired AND banded apex. The gate takes ONLY the side more
    wallets are on, so a growing minority leg is never a candidate — and the dominant side's
    delta is negative while the other leg grows, so nothing fires at all."""
    inp = {"tiltThreshold": 65, "deltaMin": 2}
    acc = scoring.new_accuracy_state()
    cur = {"ETH": {"long_n": 13, "short_n": 29, "raw_coin": "ETH"}}       # 69% SHORT
    assert scan.fresh_picks(cur, {"ETH": {"long_n": 5, "short_n": 29}}, acc, inp) == []   # long leg 5 -> 13
    # the SHORT side growing is the only thing that can fire, and it fires SHORT
    picks = scan.fresh_picks(cur, {"ETH": {"long_n": 13, "short_n": 25}}, acc, inp)
    assert [(p["direction"], p["count"], p["delta"]) for p in picks] == [("SHORT", 29, 4.0)]


def test_standing_conviction_is_ignored_but_widening_fires():
    """The derivative is taken on the NET headcount: a short that is still filling fires SHORT,
    the same read twice is delta 0 (already priced), and a narrowing lean is not an entry."""
    inp = {"tiltThreshold": 65, "deltaMin": 2}
    acc = scoring.new_accuracy_state()
    prev = {"ETH": {"long_n": 4, "short_n": 6}}                            # 60% short
    cur = {"ETH": {"long_n": 2, "short_n": 10, "raw_coin": "ETH"}}         # 83% short — widening
    picks = scan.fresh_picks(cur, prev, acc, inp)
    assert [(p["direction"], p["delta"]) for p in picks] == [("SHORT", 6.0)]
    assert scan.fresh_picks(cur, cur, acc, inp) == [], "standing conviction is already priced"
    assert scan.fresh_picks(prev, cur, acc, inp) == [], "narrowing is not an entry"


def test_ledger_raises_the_bar_for_a_class_that_keeps_missing():
    """The hit-rate ledger. Five signals marked wrong an hour later -> 0% hit rate (< 40%) -> the
    crypto threshold rises by 4 (65 -> 69), so a 68.2% name that fired before is refused until the
    class earns it back. xyz keeps its own ledger."""
    acc = scoring.new_accuracy_state()
    for _ in range(5):
        scoring.add_pending_signal(acc, "crypto", "BTC", "LONG", 100.0, 0)
    scoring.update_accuracy(acc, "crypto", scoring.EVAL_DELAY_S + 1, lambda asset: 90.0)  # LONG, price fell
    assert scoring.adjusted_threshold(65, "crypto", acc) == 69.0
    inp = {"tiltThreshold": 65, "deltaMin": 2}
    cur = {"BTC": {"long_n": 15, "short_n": 7, "raw_coin": "BTC"}}
    prev = {"BTC": {"long_n": 13, "short_n": 7}}                                       # 68.2%, +2
    assert scan.fresh_picks(cur, prev, acc, inp) == []                                  # 68.2 < 69
    assert scan.fresh_picks(cur, prev, scoring.new_accuracy_state(), inp)               # clean ledger: fires
    assert scan.fresh_picks({"NVDA": {"long_n": 15, "short_n": 7, "raw_coin": "xyz:NVDA"}},
                            {"NVDA": {"long_n": 13, "short_n": 7}}, acc, inp)           # xyz untouched


# ── (b) fake ctx (no network) ──

class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)


class _Ctx:
    def __init__(self, mcp):
        self.wallet = "0xpilot"
        self.state = _State()
        self.senpi_mcp = mcp


def test_cohort_headcount_is_one_vote_per_wallet_asset_direction():
    """A wallet's size buys no extra vote: 2 wallets short $1000 each vs 6 long $10 each read
    LONG 6 / SHORT 2 — the notional engine read that as SHORT."""
    states = [
        {"address": "0xa", "openPositions": [{"coin": "BTC", "szi": 1.0}]},
        {"address": "0xb", "openPositions": [{"coin": "BTC", "szi": 2.0},
                                             {"coin": "ETH", "szi": -1.0, "positionValue": "1000"}]},
        {"address": "0xc", "openPositions": [{"coin": "xyz:NVDA", "szi": 3.0},
                                             {"coin": "BTC", "szi": 0.5}]},
        {"trader_address": "0xd", "open_positions": [{"asset": "ETH", "szi": -2.0}]},   # alt spellings
        {"address": "0xa", "openPositions": [{"coin": "BTC", "szi": 1.0}]},              # duplicate wallet
        {"address": "0xe", "openPositions": [{"coin": "BTC", "szi": 0.0}]},              # flat -> no vote
    ] + [{"address": f"0xl{i}", "openPositions": [{"coin": "ETH", "szi": 0.1, "positionValue": "10"}]}
         for i in range(6)]

    class _M:
        def call_tool(self, tool, args):
            assert tool == "discovery_get_trader_state"
            return {"data": {"traders": states}}

    hc = scan._cohort_headcount(_Ctx(_M()), ["0xa", "0xb", "0xc", "0xd", "0xe"], {"stateBatch": 40})
    assert (hc["BTC"]["long_n"], hc["BTC"]["short_n"]) == (3, 0)     # a, b, c ; dup a once ; flat e ignored
    assert (hc["ETH"]["long_n"], hc["ETH"]["short_n"]) == (6, 2)     # headcount, not dollars
    assert hc["NVDA"]["raw_coin"] == "xyz:NVDA"                       # emit the venue-prefixed name


# ── (c) the shipped config ──

def test_bands_stay_coherent_with_the_gate():
    """The smallest dominant side that can clear the gate is 65% of the 10-wallet min sample = 7 —
    that must band base, and the ladder must keep three distinct rungs so not every pick is apex."""
    floor = math.ceil(_INPUTS["tiltThreshold"] / 100 * 10)
    assert floor < _INPUTS["goodConsensus"] < _INPUTS["apexConsensus"], _INPUTS
    assert scan.band_for(floor, _INPUTS) == "base"
    assert scan.band_for(_INPUTS["goodConsensus"], _INPUTS) == "good"
    assert scan.band_for(_INPUTS["apexConsensus"], _INPUTS) == "apex"


def test_sizing_is_a_percent_and_clamped():
    inp = {"leverageTiers": {"apex": 4}, "marginPctTiers": {"apex": 12},
           "maxLeverage": 4, "maxMarginPct": 20}
    assert scan.sizing_for("apex", inp) == (4, 12.0)
    assert scan.sizing_for("apex", inp, venue_max=2)[0] == 2
    assert scan.sizing_for("apex", {"leverageTiers": {"apex": 99},
                                    "marginPctTiers": {"apex": 999},
                                    "maxLeverage": 4, "maxMarginPct": 20}) == (4, 20.0)
    lev, mgn = scan.sizing_for("base", _INPUTS)
    assert 1 <= lev <= _INPUTS["maxLeverage"] and 0 < mgn <= _INPUTS["maxMarginPct"]


# ── (d) scan() smoke against a fake ctx ──

class _MCP:
    """Canned proven cohort of 14 wallets. tick 1: BTC 8L/3S (73%), ETH 5L/6S (55%), SOL 3L (thin).
    tick 2+: two more wallets long BTC -> 10L/3S (77%, +2); ETH 6L/6S; SOL unchanged."""

    def __init__(self):
        self.tick = 1
        self.calls = []

    def _states(self):
        btc_long, eth_long = (10, 6) if self.tick >= 2 else (8, 5)
        out = []
        for i in range(14):
            pos = []
            if i < btc_long:
                pos.append({"coin": "BTC", "szi": "1.0", "entryPx": "60000"})
            elif i < btc_long + 3:
                pos.append({"coin": "BTC", "szi": "-1.0", "entryPx": "60000"})
            if i < eth_long:
                pos.append({"coin": "ETH", "szi": "2.0"})
            elif i < eth_long + 6:
                pos.append({"coin": "ETH", "szi": "-2.0"})
            if i < 3:
                pos.append({"coin": "SOL", "szi": "1.0"})
            out.append({"address": f"0x{i:02x}", "openPositions": pos})
        return out

    def call_tool(self, tool, args):
        self.calls.append(tool)
        if tool == "discovery_get_top_traders":
            if (args or {}).get("offset", 0) > 0:
                return {"success": True, "data": {"traders": []}}           # end pagination
            return {"success": True, "data": {"traders": [
                {"address": f"0x{i:02x}", "realizedProfitAndLoss": 5_000_000} for i in range(14)]}}
        if tool == "discovery_get_trader_state":
            return {"success": True, "data": {"traders": self._states()}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"assetPositions": []}, "xyz": {"assetPositions": []}}}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"1h": [{"c": "100"}], "4h": []}}}
        return {}


class _EmptyMCP:
    def call_tool(self, tool, args):
        if tool == "discovery_get_top_traders":
            return {"data": {"traders": []}}
        return {"data": {"assetPositions": []}}


def test_scan_seeds_on_the_first_tick_then_opens_on_growth_and_persists():
    ctx = _Ctx(_MCP())
    # tick 1: cold start — the read seeds the baseline, nothing opens, no price read is made
    assert scan.scan(dict(_INPUTS), ctx) == []
    last = ctx.state.last()
    assert len(last["cohort"]) == 14 and last["prev_tilts"]["BTC"] == {"long_n": 8, "short_n": 3}
    assert "market_get_asset_data" not in ctx.senpi_mcp.calls

    # tick 2: two more wallets long BTC (+2 at 77%) -> one open; ETH at 50% and thin SOL never qualify
    ctx.senpi_mcp.tick = 2
    out = scan.scan(dict(_INPUTS), ctx)
    assert len(out) == 1
    s = out[0]
    assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
    assert (s["asset"], s["direction"], s["leverage"], s["marginPct"]) == ("BTC", "LONG", 4, 11)  # 10 agree -> good
    assert s["data"]["consensusCount"] == 10 and s["data"]["band"] == "good"
    assert s["data"]["oneSidedness"] == 76.9 and s["data"]["delta"] == 2.0
    schema = yaml.safe_load(open(_RUNTIME))["scanners"][1]["signal_data_schema"]
    assert set(s["data"]) == set(schema)                   # an undeclared or null key is discarded by the intake
    assert not [k for k, v in s["data"].items() if v is None]
    last = ctx.state.last()
    assert last["prev_tilts"]["BTC"] == {"long_n": 10, "short_n": 3} and "BTC" in last["recent"]
    pend = last["accuracy"]["crypto"]["pending"]           # the ledger holds the signal for its 1h mark
    assert len(pend) == 1 and pend[0]["entry_price"] == 100.0 and pend[0]["direction"] == "LONG"

    # tick 3: same read -> delta 0 (+ the per-name debounce) -> nothing new
    assert scan.scan(dict(_INPUTS), ctx) == []


def test_scan_degrades_when_cohort_empty():
    ctx = _Ctx(_EmptyMCP())
    out = scan.scan(dict(_INPUTS), ctx)
    assert out == []                                       # nothing to follow -> empty, no crash
    assert ctx.state.last() is not None                    # still persists (cohort stays empty)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} PILOTFISH TESTS PASS")
