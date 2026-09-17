"""Starling engine tests: the gate over fixture dicts (no network), the hit-rate ledger, the
headcount, the shipped config, and a scan() smoke run against a fake ctx.
Run: python3 -m pytest strategies/starling/tests -q  or  python3 strategies/starling/tests/test_engine.py"""
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


# ── (a) THE GATE — Phalanx's engine over fixture dicts ──

def test_gate_refuses_one_wallet_and_a_thin_lean_and_accepts_a_growing_one_sided_name():
    inp = {"tiltThreshold": 65, "deltaMin": 2}
    acc = scoring.new_accuracy_state()
    # +1: one wallet joined a one-sided name (13L/7S -> 14L/7S, 66.7%) — noise, not a rotation
    assert scan.fresh_picks({"BTC": {"long_n": 14, "short_n": 7}},
                            {"BTC": {"long_n": 13, "short_n": 7}}, acc, inp) == []
    # 30L/26S is 54% one-sided: never trades, however fast it grew (+4 here). The old
    # absolute-margin gate took this at the apex band.
    assert scan.fresh_picks({"ETH": {"long_n": 30, "short_n": 26}},
                            {"ETH": {"long_n": 26, "short_n": 26}}, acc, inp) == []
    # +2 at 65%+ with >= 10 wallets positioned (13L/7S -> 15L/7S, 68.2%) -> fires, dominant side
    picks = scan.fresh_picks({"BTC": {"long_n": 15, "short_n": 7, "raw_coin": "BTC"}},
                             {"BTC": {"long_n": 13, "short_n": 7}}, acc, inp)
    assert [(p["asset"], p["direction"], p["count"], p["delta"]) for p in picks] == [("BTC", "LONG", 15, 2.0)]
    assert picks[0]["conviction"] == 68.2
    # under the 10-wallet min sample one_sidedness is 0 -> refused however aligned (5L/0S, +5)
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
        self.wallet = "0xstarling"
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
        def call_tool(self, tool, args):
            assert tool == "discovery_get_trader_state"
            return {"data": {"traders": states}}

    hc = scan._cohort_headcount(_Ctx(_M()), ["0xa", "0xb", "0xc", "0xd", "0xe"], {"stateBatch": 50})
    assert (hc["BTC"]["long_n"], hc["BTC"]["short_n"]) == (3, 0)     # a, b, c ; dup a once ; flat e ignored
    assert (hc["ETH"]["long_n"], hc["ETH"]["short_n"]) == (0, 2)     # b, d
    assert hc["NVDA"]["raw_coin"] == "xyz:NVDA"                       # emit the venue-prefixed name


# ── (c) the shipped config ──

def test_ratchet_locks_gains_only_and_never_a_loss():
    """Starling takes longer-term bets, so the exit must never convert a winner into a loser.

    Two invariants, both learned the hard way from live trades:
      1. phase1 TRAILING must stay disabled. It trails from HIGH-WATER, so once price moved our
         way the floor followed it and could sit BELOW entry — a ratchet that locks a LOSS. A live
         ZEC short hit +8.5% ROE, bounced 2.8% off its low, and the trail closed it at -5.9%.
         With phase1 disabled the only floor beneath entry is the FIXED max-loss.
      2. No tier may lock 0% of high-water. A breakeven exit still pays fees, so "scratch" is a
         loss. Every rung must protect a positive ROE or it should not exist.
    """
    preset = yaml.safe_load(open(_RUNTIME))["exit"]["dsl_preset"]
    p1, tiers = preset["phase1"], preset["phase2"]["tiers"]

    assert p1["enabled"] is False, "phase1 trailing must stay off — it can ratchet into a loss"
    # the runtime REQUIRES max_loss_pct when phase1 is disabled; it is the only sub-entry floor
    assert p1.get("max_loss_pct", 0) > 0, "max_loss_pct is the sole downside floor once phase1 is off"

    assert tiers, "a gains-only ratchet still needs rungs"
    for t_ in tiers:
        assert t_["lock_hw_pct"] > 0, f"tier {t_} locks breakeven/negative — fees make that a loss"
    trigs = [t_["trigger_pct"] for t_ in tiers]
    locks = [t_["lock_hw_pct"] for t_ in tiers]
    assert trigs == sorted(trigs) and len(set(trigs)) == len(trigs), trigs
    assert locks == sorted(locks), locks          # never lock LESS at a higher peak

    # NO-SCRATCH INVARIANT: the smallest gain the ratchet can ever exit on must be worth taking.
    # A floor at +3% ROE is ~$8 on a $260 margin against ~$2 of fees — churn, not a win. The point
    # of the strategy is longer-term bets, so nothing may exit between the max-loss and a real gain.
    lowest_lockable = tiers[0]["trigger_pct"] * tiers[0]["lock_hw_pct"] / 100
    assert lowest_lockable >= 8, (
        f"first rung can exit at only +{lowest_lockable:.1f}% ROE — that is a scratch after fees")

    # …and the stall cut stays OFF: it closes at MARKET with no floor, so on a longer-hold book it
    # realises a small loss on a position that simply hadn't moved yet. weak_peak is a CHOP tool.
    assert preset["weak_peak_cut"]["enabled"] is False


def test_bands_stay_coherent_with_the_gate():
    """The smallest dominant side that can clear the gate is 65% of the 10-wallet min sample = 7 —
    that must band base, and the ladder must keep three distinct rungs so not every pick is apex."""
    inputs = yaml.safe_load(open(_RUNTIME))["scanners"][1]["inputs"]
    floor = math.ceil(inputs["tiltThreshold"] / 100 * 10)
    assert floor < inputs["goodConsensus"] < inputs["apexConsensus"], inputs
    assert scan.band_for(floor, inputs) == "base"
    assert scan.band_for(inputs["goodConsensus"], inputs) == "good"
    assert scan.band_for(inputs["apexConsensus"], inputs) == "apex"


def test_band_and_sizing_caps():
    inp = {"apexConsensus": 15, "goodConsensus": 10,
           "leverageTiers": {"apex": 5, "good": 4, "base": 3},
           "marginPctTiers": {"apex": 14, "good": 10, "base": 7},
           "maxLeverage": 5, "maxMarginPct": 25}
    assert scan.band_for(15, inp) == "apex"
    assert scan.band_for(14, inp) == "good"
    assert scan.band_for(10, inp) == "good"
    assert scan.band_for(7, inp) == "base"
    lev, mgn = scan.sizing_for("apex", inp)
    assert lev == 5 and mgn == 14
    lev, mgn = scan.sizing_for("apex", inp, venue_max=3)
    assert lev == 3                                        # clamped to venue max
    lev, mgn = scan.sizing_for("base", inp)
    assert 1 <= lev <= 5 and 0 < mgn <= 25
    # oversized tier clamps to fleet caps, never overshoots
    lev, mgn = scan.sizing_for("apex", {"leverageTiers": {"apex": 99},
                                        "marginPctTiers": {"apex": 999},
                                        "maxLeverage": 5, "maxMarginPct": 25})
    assert lev == 5 and mgn == 25


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
                pos.append({"coin": "BTC", "szi": 1.0})
            elif i < btc_long + 3:
                pos.append({"coin": "BTC", "szi": -1.0})
            if i < eth_long:
                pos.append({"coin": "ETH", "szi": 2.0})
            elif i < eth_long + 6:
                pos.append({"coin": "ETH", "szi": -2.0})
            if i < 3:
                pos.append({"coin": "SOL", "szi": 1.0})
            out.append({"traderAddress": f"0x{i:02x}", "openPositions": pos})
        return out

    def call_tool(self, tool, args):
        self.calls.append(tool)
        if tool == "discovery_get_top_traders":
            if (args or {}).get("offset", 0) > 0:
                return {"data": {"traders": []}}           # end pagination
            return {"data": {"traders": [{"address": f"0x{i:02x}", "realizedPnl": 5_000_000}
                                         for i in range(14)]}}
        if tool == "discovery_get_trader_state":
            return {"data": {"traders": self._states()}}
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


_INPUTS = {
    "cohortRefreshHours": 12, "smartMinRealizedUsd": 1000, "cohortCap": 120,
    "pageSize": 1000, "maxPages": 6, "stateBatch": 50,
    "tiltThreshold": 65, "deltaMin": 2, "goodConsensus": 10, "apexConsensus": 15,
    "maxSlots": 6, "recentSignalTtlSeconds": 21600,
    "leverageTiers": {"apex": 5, "good": 4, "base": 3},
    "marginPctTiers": {"apex": 14, "good": 10, "base": 7},
    "maxLeverage": 5, "maxMarginPct": 25,
}


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
    assert (s["asset"], s["direction"], s["leverage"], s["marginPct"]) == ("BTC", "LONG", 4, 10)  # 10 agree -> good
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
    print(f"\nALL {len(fns)} STARLING TESTS PASS")
