"""Starling consensus-engine tests. Pure/deterministic; plus a scan() smoke run
against a fake ctx (no network). Run: python3 -m pytest strategies/starling/tests -q
or plain `python3 strategies/starling/tests/test_engine.py`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scoring  # noqa: E402


# ── (a) consensus_counts: DISTINCT wallets per (asset, direction) ──

def test_consensus_counts_distinct_wallets_and_tolerant_shapes():
    states = [
        {"traderAddress": "0xa", "openPositions": [{"coin": "BTC", "szi": 1.0}]},
        {"traderAddress": "0xb", "openPositions": [{"coin": "BTC", "szi": 2.0},
                                                   {"coin": "ETH", "szi": -1.0}]},
        {"traderAddress": "0xc", "openPositions": [{"coin": "xyz:NVDA", "szi": 3.0},
                                                   {"coin": "BTC", "szi": 0.5}]},
        {"trader_address": "0xd", "open_positions": [{"asset": "ETH", "szi": -2.0}]},  # alt keys
        {"traderAddress": "0xa", "openPositions": [{"coin": "BTC", "szi": 1.0}]},  # DUP wallet
        {"traderAddress": "0xe", "openPositions": [{"coin": "BTC", "szi": 0.0}]},  # flat -> ignored
    ]
    c = scoring.consensus_counts(states)
    assert c["BTC"]["LONG"] == 3      # a, b, c ; dup a not double-counted ; flat e ignored
    assert c["BTC"]["SHORT"] == 0
    assert c["ETH"]["SHORT"] == 2     # b, d (via alt spellings open_positions/asset)
    assert c["ETH"]["LONG"] == 0
    assert c["NVDA"]["LONG"] == 1     # bare-upper strips the xyz: prefix for counting
    assert scoring.consensus_counts([]) == {}
    assert scoring.consensus_counts(None) == {}


def test_name_map_preserves_venue_prefix():
    states = [{"traderAddress": "0xc", "openPositions": [
        {"coin": "xyz:NVDA", "szi": 3.0}, {"coin": "BTC", "szi": 1.0}]}]
    nm = scoring.name_map(states)
    assert nm["NVDA"] == "xyz:NVDA"   # emit the tradeable prefixed name, not bare
    assert nm["BTC"] == "BTC"


def test_direction_of_from_szi_then_side_fallback():
    assert scoring.direction_of({"szi": 5}) == "LONG"
    assert scoring.direction_of({"szi": -5}) == "SHORT"
    assert scoring.direction_of({"szi": 0}) is None
    assert scoring.direction_of({"side": "SELL"}) == "SHORT"   # fallback only when szi absent
    assert scoring.direction_of({"direction": "long"}) == "LONG"
    assert scoring.direction_of({}) is None


# ── (b) fresh_picks: fires on newly-formed / rising, NOT on stale standing ──

def test_fresh_picks_forming_growing_and_stale():
    inp = {"minConsensus": 3}
    cur = {"BTC": {"LONG": 3, "SHORT": 0}}
    # newly FORMED (prev absent) -> fires
    assert scoring.fresh_picks(cur, {}, inp) == [{"asset": "BTC", "direction": "LONG", "count": 3}]
    # STALE standing consensus (prev == cur >= min) -> NO pick  (the core guarantee)
    assert scoring.fresh_picks(cur, {"BTC": {"LONG": 3, "SHORT": 0}}, inp) == []
    # GROWING (prev 3 -> cur 5) -> fires
    grew = scoring.fresh_picks({"BTC": {"LONG": 5}}, {"BTC": {"LONG": 3}}, inp)
    assert grew == [{"asset": "BTC", "direction": "LONG", "count": 5}]
    # crossing the bar from below (prev 2 < min -> cur 4) -> fires
    assert scoring.fresh_picks({"BTC": {"LONG": 4}}, {"BTC": {"LONG": 2}}, inp)[0]["count"] == 4
    # below the bar -> never
    assert scoring.fresh_picks({"BTC": {"LONG": 2}}, {}, inp) == []
    # SHRINKING but still above bar (prev 5 -> cur 4) -> NO pick (not rising)
    assert scoring.fresh_picks({"BTC": {"LONG": 4}}, {"BTC": {"LONG": 5}}, inp) == []


def test_fresh_picks_sorted_by_count_desc():
    inp = {"minConsensus": 3}
    cur = {"BTC": {"LONG": 4, "SHORT": 0}, "ETH": {"LONG": 0, "SHORT": 7}, "SOL": {"LONG": 3}}
    picks = scoring.fresh_picks(cur, {}, inp)
    assert [p["count"] for p in picks] == [7, 4, 3]      # apex ETH first
    assert picks[0] == {"asset": "ETH", "direction": "SHORT", "count": 7}


# ── band + sizing (fleet caps) ──

def test_band_and_sizing_caps():
    inp = {"apexConsensus": 6, "goodConsensus": 4, "minConsensus": 3,
           "leverageTiers": {"apex": 5, "good": 4, "base": 3},
           "marginPctTiers": {"apex": 14, "good": 10, "base": 7},
           "maxLeverage": 5, "maxMarginPct": 25}
    assert scoring.band_for(6, inp) == "apex"
    assert scoring.band_for(5, inp) == "good"
    assert scoring.band_for(4, inp) == "good"
    assert scoring.band_for(3, inp) == "base"
    lev, mgn = scoring.sizing_for("apex", inp)
    assert lev == 5 and mgn == 14
    lev, mgn = scoring.sizing_for("apex", inp, venue_max=3)
    assert lev == 3                                        # clamped to venue max
    lev, mgn = scoring.sizing_for("base", inp)
    assert 1 <= lev <= 5 and 0 < mgn <= 25
    # oversized tier clamps to fleet caps, never overshoots
    lev, mgn = scoring.sizing_for("apex", {"leverageTiers": {"apex": 99},
                                           "marginPctTiers": {"apex": 999},
                                           "maxLeverage": 5, "maxMarginPct": 25})
    assert lev == 5 and mgn == 25


def test_traders_of_and_realized_tolerant():
    assert scoring.traders_of([1, 2]) == [1, 2]
    assert scoring.traders_of({"traders": [1]}) == [1]
    assert scoring.traders_of({"data": [2]}) == [2]
    assert scoring.traders_of({"results": [3]}) == [3]
    assert scoring.traders_of({"nope": 1}) == []
    assert scoring.realized({"realizedPnl": 5}) == 5.0
    assert scoring.realized({"realized_profit_and_loss": 7}) == 7.0
    assert scoring.realized({"nope": 1}) == 0.0
    assert scoring.trader_address({"address": "0xAbC"}) == "0xabc"
    assert scoring.trader_address({"trader_address": "0xDeF"}) == "0xdef"
    assert scoring.trader_address({}) == ""


# ── (c) scan() smoke against a fake ctx (no network) ──

class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)


class _MCP:
    """Canned: 4 proven traders; 3 of them freshly long BTC, 1 short ETH, 1 long SOL."""
    def call_tool(self, tool, args):
        if tool == "discovery_get_top_traders":
            if (args or {}).get("offset", 0) > 0:
                return {"data": {"traders": []}}           # end pagination
            return {"data": {"traders": [
                {"address": "0xa", "realizedPnl": 5_000_000},
                {"address": "0xb", "realizedPnl": 3_000_000},
                {"address": "0xc", "realizedPnl": 2_000_000},
                {"address": "0xd", "realizedPnl": 1_500}]}}
        if tool == "discovery_get_trader_state":
            return {"data": {"traders": [
                {"traderAddress": "0xa", "openPositions": [{"coin": "BTC", "szi": 1.2}]},
                {"traderAddress": "0xb", "openPositions": [{"coin": "BTC", "szi": 3.0}]},
                {"traderAddress": "0xc", "openPositions": [{"coin": "BTC", "szi": 0.4},
                                                           {"coin": "ETH", "szi": -2.0}]},
                {"traderAddress": "0xd", "openPositions": [{"coin": "SOL", "szi": 9.0}]}]}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"assetPositions": []}}
        return {}


class _EmptyMCP:
    def call_tool(self, tool, args):
        if tool == "discovery_get_top_traders":
            return {"data": {"traders": []}}
        return {"data": {"assetPositions": []}}


class _Ctx:
    def __init__(self, mcp=None):
        self.wallet = "0xstarling"
        self.state = _State()
        self.senpi_mcp = mcp or _MCP()


_INPUTS = {
    "cohortRefreshHours": 12, "smartMinRealizedUsd": 1000, "cohortCap": 120,
    "pageSize": 1000, "maxPages": 6, "stateBatch": 50,
    "minConsensus": 3, "goodConsensus": 4, "apexConsensus": 6,
    "maxSlots": 6, "recentSignalTtlSeconds": 21600,
    "leverageTiers": {"apex": 5, "good": 4, "base": 3},
    "marginPctTiers": {"apex": 14, "good": 10, "base": 7},
    "maxLeverage": 5, "maxMarginPct": 25,
}


def test_scan_smoke_returns_valid_signals_and_persists():
    import scan
    ctx = _Ctx()
    out = scan.scan(dict(_INPUTS), ctx)
    assert isinstance(out, list)
    assert len(out) == 1                                   # only BTC has >=3 fresh agreeing wallets
    s = out[0]
    assert set(("asset", "direction", "marginPct", "leverage", "data")) <= set(s)
    assert s["asset"] == "BTC" and s["direction"] == "LONG"
    assert 0 < s["marginPct"] <= 25 and 1 <= s["leverage"] <= 5
    assert s["data"]["consensusCount"] == 3 and s["data"]["band"] == "base"
    assert s["data"]["reasons"] == ["3 smart-money wallets just went long"]
    # state persisted: cohort + snapshot + recent
    last = ctx.state.last()
    assert last is not None and len(last["cohort"]) == 4
    assert last["last_snapshot"]["BTC"]["LONG"] == 3
    assert "BTC" in last["recent"]

    # second tick: identical snapshot -> STALE standing consensus (+ recent debounce) -> no new opens
    out2 = scan.scan(dict(_INPUTS), ctx)
    assert out2 == []


def test_scan_degrades_when_cohort_empty():
    import scan
    ctx = _Ctx(mcp=_EmptyMCP())
    out = scan.scan(dict(_INPUTS), ctx)
    assert out == []                                       # nothing to follow -> empty, no crash
    assert ctx.state.last() is not None                    # still persists (cohort stays empty)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} STARLING TESTS PASS")
