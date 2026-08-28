"""Pilotfish engine tests. Pure math + a scan() smoke run against a fake ctx (no network).
Run: python3 strategies/pilotfish/tests/test_engine.py"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402


def _w(addr, coin, szi, px=100.0):
    return {"address": addr, "openPositions": [{"coin": coin, "szi": szi, "entryPx": px}]}


def _book(coin, shorts, longs, px=100.0):
    return [_w(f"0xs{i}", coin, -1, px) for i in range(shorts)] + \
           [_w(f"0xl{i}", coin, 1, px) for i in range(longs)]


# ── bias is DOLLAR-weighted net/gross, not a headcount ──

def test_bias_is_net_over_gross_notional_not_a_headcount():
    # 2 wallets short $1000 each vs 6 wallets long $10 each: headcount says LONG, dollars say SHORT
    states = [_w("0xa", "ETH", -10, 100.0), _w("0xb", "ETH", -10, 100.0)] + \
             [_w(f"0xl{i}", "ETH", 0.1, 100.0) for i in range(6)]
    d = scoring.cohort_bias(states)["ETH"]
    assert d["n_short"] == 2 and d["n_long"] == 6, d      # headcount majority is LONG
    assert d["bias"] < 0, "dollar-weighted net must read SHORT"
    assert -1.0 <= d["bias"] <= 1.0


def test_duplicate_wallet_rows_are_counted_once():
    states = _book("ETH", 3, 1) + _book("ETH", 3, 1)      # same wallets seen twice
    d = scoring.cohort_bias(states)["ETH"]
    assert d["members"] == 4, d


# ── the three rules this engine exists to enforce ──

def test_the_minority_side_is_unpickable():
    """A 13-vs-29 minority long once fired AND banded apex. Reading the net makes that
    impossible by construction — there is only ever one side to pick."""
    cur = scoring.cohort_bias(_book("ETH", 29, 13))
    prev = scoring.cohort_bias(_book("ETH", 29, 5))       # long leg grew 5 -> 13
    picks = scoring.accumulating(cur, prev, {})
    assert all(p["direction"] != "LONG" for p in picks), picks
    assert picks == [] or picks[0]["direction"] == "SHORT"


def test_standing_conviction_is_ignored_but_widening_fires():
    """Per-leg freshness discarded a flat-but-dominant short as 'stale' and traded the
    noisy long leg. The derivative is taken on the NET, so a widening short DOES fire."""
    prev = scoring.cohort_bias(_book("ETH", 6, 4))        # bias -0.2
    cur = scoring.cohort_bias(_book("ETH", 10, 2))        # bias -0.667 — widening
    picks = scoring.accumulating(cur, prev, {})
    assert len(picks) == 1 and picks[0]["direction"] == "SHORT"
    assert picks[0]["delta"] > 0.1 and picks[0]["bias"] < picks[0]["prev_bias"]
    assert scoring.accumulating(cur, cur, {}) == [], "standing conviction is already priced"
    # narrowing conviction is not an entry either
    assert scoring.accumulating(prev, cur, {}) == []


def test_a_side_flip_counts_the_whole_new_lean():
    prev = scoring.cohort_bias(_book("ETH", 1, 9))        # strongly long
    cur = scoring.cohort_bias(_book("ETH", 9, 1))         # now strongly short
    picks = scoring.accumulating(cur, prev, {})
    assert picks and picks[0]["direction"] == "SHORT"
    assert picks[0]["delta"] == abs(picks[0]["bias"])     # the whole lean is new


def test_a_ratio_without_a_sample_is_not_evidence():
    """4-vs-1 and 400-vs-100 are both '80% one-sided'; only one is a fact about the market."""
    assert scoring.sample_shrink(4) < 0.4 and scoring.sample_shrink(200) > 0.9
    assert scoring.sample_shrink(0) == 0.0 and scoring.sample_shrink(None) == 0.0
    tiny = scoring.cohort_bias(_book("SOL", 2, 0))
    assert scoring.accumulating(tiny, {}, {}) == [], "below minMembers is not evidence"
    small = scoring.cohort_bias(_book("SOL", 5, 0))
    big = scoring.cohort_bias(_book("SOL", 60, 0))
    assert scoring.conviction(big["SOL"]) > scoring.conviction(small["SOL"])
    # identical ratio, different sample -> the bigger sample must score higher
    ps = scoring.accumulating(small, {}, {})[0]["score"]
    pb = scoring.accumulating(big, {}, {})[0]["score"]
    assert pb > ps, (ps, pb)


def test_a_split_cohort_says_nothing():
    even = scoring.cohort_bias(_book("ETH", 6, 6))
    assert scoring.accumulating(even, {}, {}) == []


def test_sizing_is_a_percent_and_clamped():
    inp = {"leverageTiers": {"apex": 4}, "marginPctTiers": {"apex": 12},
           "maxLeverage": 4, "maxMarginPct": 20}
    assert scoring.sizing_for("apex", inp) == (4, 12.0)
    assert scoring.sizing_for("apex", inp, venue_max=2)[0] == 2
    assert scoring.sizing_for("apex", {"leverageTiers": {"apex": 99},
                                       "marginPctTiers": {"apex": 999},
                                       "maxLeverage": 4, "maxMarginPct": 20}) == (4, 20.0)


# ── scan() smoke ──

class _State:
    def __init__(self, seed=None): self._log = [seed] if seed else []
    def last(self): return self._log[-1] if self._log else None
    def append(self, d): self._log.append(d)


class _MCP:
    def __init__(self, shorts=10, longs=2):
        self.shorts, self.longs = shorts, longs
    def call_tool(self, tool, args):
        if tool == "discovery_get_top_traders":
            if (args or {}).get("offset", 0) > 0:
                return {"data": {"traders": []}}
            return {"data": {"traders": [{"address": f"0xs{i}", "realizedPnl": 5e6}
                                         for i in range(self.shorts)] +
                                        [{"address": f"0xl{i}", "realizedPnl": 5e6}
                                         for i in range(self.longs)]}}
        if tool == "discovery_get_trader_state":
            addrs = set((args or {}).get("trader_addresses") or [])
            return {"data": {"traders": [w for w in _book("ETH", self.shorts, self.longs)
                                         if w["address"] in addrs]}}
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"assetPositions": []}}
        return {}


class _Ctx:
    def __init__(self, mcp, seed=None):
        self.senpi_mcp, self.wallet, self.state = mcp, "0xpilot", _State(seed)


_INPUTS = {"cohortRefreshHours": 12, "minRealizedUsd": 1e6, "cohortCap": 80, "pageSize": 500,
           "maxPages": 4, "stateBatch": 40, "biasLookbackHours": 6, "minBias": 0.25,
           "minBiasDelta": 0.10, "minMembers": 4, "maxSlots": 5,
           "recentSignalTtlSeconds": 21600, "minScore": 0,
           "leverageTiers": {"apex": 4, "good": 3, "base": 2},
           "marginPctTiers": {"apex": 12, "good": 9, "base": 6},
           "maxLeverage": 4, "maxMarginPct": 20}


def test_cold_start_builds_history_and_opens_nothing():
    """With no snapshot old enough to diff against there is no derivative — and inventing
    one would be exactly the fabricated-baseline bug. Silence is the correct answer."""
    ctx = _Ctx(_MCP())
    assert scan.scan(dict(_INPUTS), ctx) == []
    assert ctx.state.last()["history"], "it must still record a snapshot for next time"


def test_opens_when_conviction_widened_against_a_real_snapshot():
    old = {"ts": time.time() - 7 * 3600,
           "bias": scoring.cohort_bias(_book("ETH", 6, 4))}     # 7h ago, bias -0.2
    seed = {"cohort": [f"0xs{i}" for i in range(10)] + [f"0xl{i}" for i in range(2)],
            "last_refresh": time.time(), "recent": {}, "history": [old]}
    ctx = _Ctx(_MCP(shorts=10, longs=2), seed=seed)             # now -0.667: widened
    out = scan.scan(dict(_INPUTS), ctx)
    assert out and out[0]["direction"] == "SHORT", out
    s = out[0]
    assert 0 < s["marginPct"] <= 20 and 1 <= s["leverage"] <= 4
    assert s["data"]["biasDelta"] > 0.1 and s["data"]["members"] >= 4


def test_degrades_when_cohort_is_empty():
    class _NoCohort:
        def call_tool(self, tool, args):
            if tool == "discovery_get_top_traders":
                return {"data": {"traders": []}}
            return {}
    assert scan.scan(dict(_INPUTS), _Ctx(_NoCohort())) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} PILOTFISH TESTS PASS")
