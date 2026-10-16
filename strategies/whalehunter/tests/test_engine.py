"""Whalehunter engine tests: the gate over fixture dicts (no network), the hit-rate ledger, the
headcount, the crowd-divergence size step, the shipped config, and a scan() smoke run per sleeve
against a fake ctx.
Run: python3 -m pytest strategies/whalehunter/tests -q  or  python3 strategies/whalehunter/tests/test_engine.py"""
import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME = {s: os.path.join(_HERE, "..", s, "runtime.yaml") for s in ("long", "short")}
sys.path.insert(0, os.path.join(_HERE, "..", "long", "scanners"))
for _m in ("scoring", "scan"):          # never inherit another package's module of the same name
    sys.modules.pop(_m, None)
import scoring  # noqa: E402
import scan  # noqa: E402


def _inputs(sleeve):
    return yaml.safe_load(open(_RUNTIME[sleeve]))["scanners"][1]["inputs"]


# ── (a) THE GATE — Phalanx's engine over fixture dicts ──

def test_gate_refuses_one_wallet_and_a_thin_lean_and_accepts_a_growing_one_sided_name():
    inp = {"tiltThreshold": 65, "deltaMin": 2}
    acc = scoring.new_accuracy_state()
    # +1: one wallet joined a one-sided name (13L/7S -> 14L/7S, 66.7%) — noise, not a rotation
    assert scan.fresh_picks({"BTC": {"long_n": 14, "short_n": 7}},
                            {"BTC": {"long_n": 13, "short_n": 7}}, acc, inp) == []
    # 30L/26S is 54% one-sided: never trades, however fast it grew (+4 here). The old notional
    # bias took a lean like this once the long side's dollars outweighed the short side's.
    assert scan.fresh_picks({"ETH": {"long_n": 30, "short_n": 26}},
                            {"ETH": {"long_n": 26, "short_n": 26}}, acc, inp) == []
    # +2 at 65%+ with >= 10 wallets positioned (13L/7S -> 15L/7S, 68.2%) -> fires, dominant side
    picks = scan.fresh_picks({"BTC": {"long_n": 15, "short_n": 7, "raw_coin": "BTC"}},
                             {"BTC": {"long_n": 13, "short_n": 7}}, acc, inp)
    assert [(p["asset"], p["direction"], p["count"], p["delta"]) for p in picks] == [("BTC", "LONG", 15, 2.0)]
    assert picks[0]["conviction"] == 68.2
    # under the 10-wallet min sample one_sidedness is 0 -> refused however aligned (5L/0S, +5)
    assert scan.fresh_picks({"SOL": {"long_n": 5, "short_n": 0}}, {}, acc, inp) == []
    # an unchanged read is delta 0 -> a name the cohort has stood in for days never re-fires
    assert scan.fresh_picks({"BTC": {"long_n": 15, "short_n": 7}},
                            {"BTC": {"long_n": 15, "short_n": 7}}, acc, inp) == []


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


# ── (b) Whalehunter's own sizing on the engine's reads ──

def test_crowd_divergence_and_the_size_ladder():
    smart = {"long_n": 10, "short_n": 3}                                   # 76.9% long
    assert scan.crowd_diverges(smart, {"long_n": 0, "short_n": 3}) is True     # crowd short -> diverges
    assert scan.crowd_diverges(smart, {"long_n": 4, "short_n": 1}) is False    # crowd long too
    assert scan.crowd_diverges(smart, None) is False                           # no crowd position: no claim
    assert scan.crowd_diverges({"long_n": 1, "short_n": 12}, {"long_n": 3, "short_n": 0}) is True  # short vs buying crowd
    # score: 4 for a gated name, +1 at >= 85% one-sided, +1 on divergence -> marginPct 12 / 15 / 18 (cap 25)
    inp = {"marginPct": 12, "maxMarginPct": 25, "maxConvictionScale": 2.0}
    assert scan.score_for(76.9, False) == 4 and scan.margin_pct_for(4, inp) == 12
    assert scan.score_for(76.9, True) == 5 and scan.margin_pct_for(5, inp) == 15
    assert scan.score_for(85.0, False) == 5
    assert scan.score_for(92.3, True) == 6 and scan.margin_pct_for(6, inp) == 18
    assert scan.margin_pct_for(6, {"marginPct": 20, "maxMarginPct": 25}) == 25       # never past the cap


# ── (c) fake ctx (no network) ──

class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)


class _Ctx:
    def __init__(self, mcp):
        self.wallet = "0xwh"
        self.state = _State()
        self.senpi_mcp = mcp


def test_cohort_headcount_is_one_vote_per_wallet_asset_direction():
    states = [
        {"traderAddress": "0xa", "openPositions": [{"coin": "BTC", "szi": 1.0}]},
        {"traderAddress": "0xb", "openPositions": [{"coin": "BTC", "szi": 2.0},
                                                   {"coin": "ETH", "szi": -1.0}]},
        {"traderAddress": "0xc", "openPositions": [{"coin": "xyz:NVDA", "szi": 3.0},
                                                   {"coin": "BTC", "szi": 0.5}]},
        {"trader_address": "0xd", "open_positions": [{"asset": "ETH", "szi": -2.0}]},   # alt spellings
        {"traderAddress": "0xa", "openPositions": [{"coin": "BTC", "szi": 1.0}]},        # duplicate wallet
        {"traderAddress": "0xe", "openPositions": [{"coin": "BTC", "szi": 0.0}]},        # flat -> no vote
    ]

    class _M:
        def __init__(self, bare_list):
            self.bare_list = bare_list

        def call_tool(self, tool, args):
            assert tool == "discovery_get_trader_state"
            return {"data": states} if self.bare_list else {"data": {"traders": states}}

    for bare_list in (False, True):        # the old reader dropped a bare-list response outright
        hc = scan._cohort_headcount(_Ctx(_M(bare_list)), ["0xa", "0xb", "0xc", "0xd", "0xe"], {})
        assert (hc["BTC"]["long_n"], hc["BTC"]["short_n"]) == (3, 0)     # a, b, c ; dup a once ; flat e ignored
        assert (hc["ETH"]["long_n"], hc["ETH"]["short_n"]) == (0, 2)     # b, d
        assert hc["NVDA"]["raw_coin"] == "xyz:NVDA"                       # emit the venue-prefixed name


# ── (d) the shipped config ──

def test_shipped_config_runs_the_engine_gate_in_both_sleeves():
    for sleeve in ("long", "short"):
        inp = _inputs(sleeve)
        assert inp["direction"] == sleeve.upper()
        assert (inp["tiltThreshold"], inp["deltaMin"]) == (65, 2)
        assert not {"biasThreshold", "crowdDivergenceMin", "requireGrowing", "cohortMinScore"} & set(inp)
        assert inp["marginPct"] == 12 and inp["maxMarginPct"] == 25        # sizing unchanged (golden stays 75)


# ── (e) scan() smoke against a fake ctx, per sleeve ──

class _MCP:
    """Canned ranking: 14 proven wallets (realized $5M) + 3 crowd wallets ($50k), all read on the
    first tick (page 1 is empty -> the chunked build completes). tick 1: BTC 8L/3S (73%), ETH 5L/6S
    (55%), SOL 3L (thin); the crowd is short BTC. tick 2+: two more smart wallets long BTC ->
    10L/3S (77%, +2); ETH 6L/6S; SOL unchanged."""

    def __init__(self, crowd_szi=-1.0):
        self.tick = 1
        self.calls = []
        self.crowd_szi = crowd_szi

    def _states(self, addrs):
        btc_long, eth_long = (10, 6) if self.tick >= 2 else (8, 5)
        out = []
        for a in addrs:
            i, pos = int(a[2:], 16), []
            if i >= 100:                                        # a crowd wallet
                pos.append({"coin": "BTC", "szi": self.crowd_szi})
            else:
                if i < btc_long:
                    pos.append({"coin": "BTC", "szi": 1.0})
                elif i < btc_long + 3:
                    pos.append({"coin": "BTC", "szi": -1.0})
                if i < eth_long:
                    pos.append({"coin": "ETH", "szi": 2.0})
                elif i < eth_long + 6:
                    pos.append({"coin": "ETH", "szi": -2.0})
                if i < 3:
                    pos.append({"coin": "SOL", "szi": 1.0})
            out.append({"traderAddress": a, "openPositions": pos})
        return out

    def call_tool(self, tool, args):
        self.calls.append(tool)
        if tool == "discovery_get_top_traders":
            if (args or {}).get("offset", 0) > 0:
                return {"data": {"traders": []}}                # end pagination
            rows = [{"address": f"0x{i:02x}", "realizedPnl": 5_000_000} for i in range(14)]
            rows += [{"address": f"0x{i:02x}", "realizedPnl": 50_000} for i in range(100, 103)]
            return {"data": {"traders": rows}}
        if tool == "discovery_get_trader_state":
            return {"data": {"traders": self._states(args["trader_addresses"])}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"assetPositions": []}, "xyz": {"assetPositions": []}}}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"1h": [{"c": "100"}], "4h": []}}}
        return {}


def _state_reads(mcp):
    return mcp.calls.count("discovery_get_trader_state")


def test_long_sleeve_seeds_on_the_first_tick_then_opens_on_growth_and_persists():
    inputs, ctx = _inputs("long"), _Ctx(_MCP())
    # tick 1: the cohort completes, the read seeds the baseline, nothing opens, no price read, no crowd read
    assert scan.scan(dict(inputs), ctx) == []
    last = ctx.state.last()
    assert len(last["cohorts"]["smart"]) == 14 and len(last["cohorts"]["crowd"]) == 3
    assert last["prev_tilts"]["BTC"] == {"long_n": 8, "short_n": 3}
    assert "market_get_asset_data" not in ctx.senpi_mcp.calls and _state_reads(ctx.senpi_mcp) == 1

    # tick 2: two more wallets long BTC (+2 at 77%) -> one open; the crowd is short -> +1 -> 15%.
    # ETH at 50% and thin SOL never qualify.
    ctx.senpi_mcp.tick = 2
    out = scan.scan(dict(inputs), ctx)
    assert len(out) == 1
    s = out[0]
    assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
    assert (s["asset"], s["direction"], s["leverage"], s["marginPct"]) == ("BTC", "LONG", 3, 15)
    assert (s["data"]["score"], s["data"]["smartMembers"], s["data"]["divergence"]) == (5, 10, True)
    assert s["data"]["oneSidedness"] == 76.9 and s["data"]["delta"] == 2.0
    schema = yaml.safe_load(open(_RUNTIME["long"]))["scanners"][1]["signal_data_schema"]
    assert set(s["data"]) == set(schema)                   # an undeclared or null key is discarded by the intake
    assert not [k for k, v in s["data"].items() if v is None]
    assert _state_reads(ctx.senpi_mcp) == 3               # smart + the crowd, read once a name cleared the gate
    last = ctx.state.last()
    assert last["prev_tilts"]["BTC"] == {"long_n": 10, "short_n": 3} and "BTC" in last["recent"]
    pend = last["accuracy"]["crypto"]["pending"]           # the ledger holds the signal for its 1h mark
    assert len(pend) == 1 and pend[0]["entry_price"] == 100.0 and pend[0]["direction"] == "LONG"

    # tick 3: same read -> delta 0 (+ the per-name debounce) -> nothing new, and no crowd read
    assert scan.scan(dict(inputs), ctx) == []
    assert _state_reads(ctx.senpi_mcp) == 4


def test_a_crowd_on_the_same_side_gives_no_size_step():
    inputs, ctx = _inputs("long"), _Ctx(_MCP(crowd_szi=1.0))
    scan.scan(dict(inputs), ctx)
    ctx.senpi_mcp.tick = 2
    out = scan.scan(dict(inputs), ctx)
    assert [(s["asset"], s["marginPct"], s["data"]["score"], s["data"]["divergence"]) for s in out] \
        == [("BTC", 12, 4, False)]


def test_short_sleeve_keeps_only_its_own_direction():
    inputs, ctx = _inputs("short"), _Ctx(_MCP())
    assert scan.scan(dict(inputs), ctx) == []              # seeds
    ctx.senpi_mcp.tick = 2
    assert scan.scan(dict(inputs), ctx) == []              # the BTC rotation is LONG -> not this sleeve's
    assert _state_reads(ctx.senpi_mcp) == 2               # and the crowd was never read


def test_scan_degrades_when_the_cohort_is_empty():
    class _EmptyMCP:
        def call_tool(self, tool, args):
            return {"data": {"traders": []}}

    ctx = _Ctx(_EmptyMCP())
    assert scan.scan(dict(_inputs("long")), ctx) == []     # nothing to follow -> empty, no crash
    assert ctx.state.last() is not None                    # still persists (cohort stays empty)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} WHALEHUNTER TESTS PASS")
