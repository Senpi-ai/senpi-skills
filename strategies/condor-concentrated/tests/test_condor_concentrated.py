"""condor-concentrated: condor's apex sniper as ONE bet at 80%/10x, confirmed by the
proven cohort instead of the 4h gain board.

Jason, 2026-10-12, after his agent built and deployed this live: fork condor, include XYZ,
score 4h<->24h and 1h<->4h alignment, 80% margin, use senpi-signals' definition of smart
money, remove the time cuts.

THE BUG THIS FILE EXISTS FOR. The first build of this package shipped:

    for k in ("realizedPnl", "realized_pnl", "profit_and_loss_realized", "pnl_realized"):

The field is `realizedProfitAndLoss` and it was not in that list, so `_realized_pnl` read
0.0 for every trader, nothing cleared the >= $1M floor, and the cohort was empty on every
tick — while logging "no traders with >= $1M realized", which reads like a market fact
rather than a broken read. Measured the same day: 368 wallets had >= $1M all-time realized
and 229 of them held open positions, so the cohort was never actually empty. A $600 live
deployment could not have fired once.

Every other scanner in this repo that reads this endpoint puts `realizedProfitAndLoss`
FIRST (athena, athena-x, athena-concentrated, chimp, gibbon, gorilla, orangutan, raptor,
shadow, squid). That ordering is now pinned here.

Run: python3 -m pytest strategies/condor-concentrated/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(PKG, "main", "scanners"))

import scan      # noqa: E402
import scoring   # noqa: E402


def _rt(pkg="condor-concentrated"):
    with open(os.path.join(ROOT, "strategies", pkg, "main", "runtime.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _manifest(pkg="condor-concentrated"):
    with open(os.path.join(ROOT, "strategies", pkg, "strategy.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _inputs(rt):
    return next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]


class _MCP:
    """Records calls and replays canned responses keyed by tool name."""
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        v = self.responses.get(tool)
        if isinstance(v, Exception):
            raise v
        return v


# ── 1. the field name — the bug that shipped ────────────────────────────────────────────

def test_realized_pnl_reads_the_real_api_field_first():
    """A row carrying ONLY `realizedProfitAndLoss` must resolve. This is the exact shape the
    live endpoint returns, and the shipped build read 0.0 from it."""
    assert scan._realized_pnl({"realizedProfitAndLoss": 2_500_000}) == 2_500_000.0


def test_the_canonical_field_order_is_pinned():
    import inspect
    src = inspect.getsource(scan._realized_pnl)
    order = [k for k in ("realizedProfitAndLoss", "realized_profit_and_loss",
                         "profit_and_loss_realized", "realizedPnl", "realized_pnl")
             if f'"{k}"' in src]
    assert order[0] == "realizedProfitAndLoss", (
        "realizedProfitAndLoss must be tried FIRST — every other scanner in this repo does, "
        "and omitting it is what made this package structurally unable to fire")
    assert len(order) == 5, f"the canonical 5-name tuple is incomplete: {order}"


def test_every_alternate_spelling_still_resolves():
    for k in ("realized_profit_and_loss", "profit_and_loss_realized",
              "realizedPnl", "realized_pnl"):
        assert scan._realized_pnl({k: 1_234_567}) == 1_234_567.0, k


def test_a_row_with_no_recognised_field_reads_zero_not_a_crash():
    assert scan._realized_pnl({"somethingElse": 9}) == 0.0


# ── 2. the cohort: builds, and degrades honestly ────────────────────────────────────────

def _trader(addr, realized):
    return {"address": addr, "realizedProfitAndLoss": realized}


def _state(addr, positions):
    return {"address": addr, "openPositions": positions}


def test_the_cohort_builds_from_a_realistic_response():
    mcp = _MCP({
        "discovery_get_top_traders": {"data": {"traders": [
            _trader("0xa", 5_000_000), _trader("0xb", 2_000_000), _trader("0xc", 1_500_000),
            _trader("0xd", 900_000),                      # under the floor — excluded
        ]}},
        "discovery_get_trader_state": {"data": {"traders": [
            _state("0xa", [{"coin": "BTC", "szi": "1.0"}]),
            _state("0xb", [{"coin": "BTC", "szi": "2.0"}]),
            _state("0xc", [{"coin": "BTC", "szi": "-1.0"}]),
        ]}},
    })
    ctx = type("C", (), {"senpi_mcp": mcp, "wallet": "0xw", "state": None})()
    cohort = scan.fetch_proven_cohort(ctx, {}, {}, 1000.0)
    assert cohort["BTC"]["long_count"] == 2
    assert cohort["BTC"]["short_count"] == 1
    assert cohort["BTC"]["total_positioned"] == 3
    assert cohort["BTC"]["direction"] == "LONG"
    assert abs(cohort["BTC"]["conviction_pct"] - 66.666) < 0.01


def test_the_under_floor_trader_is_not_queried():
    mcp = _MCP({
        "discovery_get_top_traders": {"traders": [_trader("0xa", 5_000_000),
                                                  _trader("0xd", 900_000)]},
        "discovery_get_trader_state": {"traders": []},
    })
    ctx = type("C", (), {"senpi_mcp": mcp, "wallet": "0xw", "state": None})()
    scan.fetch_proven_cohort(ctx, {}, {}, 1000.0)
    state_calls = [a for t, a in mcp.calls if t == "discovery_get_trader_state"]
    assert state_calls and state_calls[0]["trader_addresses"] == ["0xa"]


def test_an_all_zero_response_is_reported_as_a_broken_READ_not_an_empty_market(capsys):
    """The distinction the shipped bug erased. If no row carries a readable realized PnL,
    that is a field-read failure, and the log must not claim the market has no winners."""
    mcp = _MCP({"discovery_get_top_traders": {"traders": [
        {"address": "0xa", "someUnknownField": 5_000_000},
        {"address": "0xb", "someUnknownField": 9_000_000},
    ]}})
    ctx = type("C", (), {"senpi_mcp": mcp, "wallet": "0xw", "state": None})()
    assert scan.fetch_proven_cohort(ctx, {}, {}, 1000.0) == {}
    err = capsys.readouterr().err
    assert "FIELD READ is broken" in err, err


def test_a_failed_read_keeps_the_cached_cohort_rather_than_emptying_it():
    cached = {"ts": 999.0, "data": {"BTC": {"direction": "LONG", "total_positioned": 20,
                                            "conviction_pct": 75.0}}}
    mcp = _MCP({"discovery_get_top_traders": RuntimeError("upstream 500")})
    ctx = type("C", (), {"senpi_mcp": mcp, "wallet": "0xw", "state": None})()
    out = scan.fetch_proven_cohort(ctx, {}, cached, 1_000_000.0)   # cache far past its TTL
    assert out == cached["data"], "a read failure must not degrade to an empty cohort"


def test_a_fresh_cache_is_served_without_any_read():
    cached = {"ts": 1000.0, "data": {"ETH": {"direction": "SHORT"}}}
    mcp = _MCP({})
    ctx = type("C", (), {"senpi_mcp": mcp, "wallet": "0xw", "state": None})()
    out = scan.fetch_proven_cohort(ctx, {"cohortCacheTtlSeconds": 900}, cached, 1100.0)
    assert out == cached["data"]
    assert mcp.calls == []


# ── 3. XYZ actually works (the join, not just the ban) ──────────────────────────────────

def test_an_xyz_board_row_is_rekeyed_to_match_the_universe():
    """The board sends a BARE token plus dex="xyz"; instruments name it "XYZ:SP500". Without
    the re-prefix the join misses every XYZ asset and the removed ban changes nothing."""
    mcp = _MCP({"leaderboard_get_markets": {"data": {"markets": [
        {"token": "SP500", "dex": "xyz", "token_price_change_pct_4h": 2.0},
        {"token": "BTC", "token_price_change_pct_4h": 1.5},
    ]}}})
    ctx = type("C", (), {"senpi_mcp": mcp, "wallet": "0xw", "state": None})()
    pm = scan.fetch_price_changes(ctx, {})
    assert "XYZ:SP500" in pm, f"xyz row was not re-prefixed: {sorted(pm)}"
    assert "BTC" in pm
    assert "SP500" not in pm


def test_xyz_is_no_longer_banned_from_the_universe():
    import inspect
    assert not hasattr(scan, "_is_xyz"), "the XYZ ban helper should be gone, not just unused"
    assert "XYZ" not in inspect.getsource(scan.fetch_universe).split('"""')[2]


# ── 4. the 24h factor, and its missing-data behaviour ───────────────────────────────────

def test_the_24h_change_is_computed_from_prevdaypx():
    mcp = _MCP({"market_list_instruments": {"instruments": [{
        "name": "BTC",
        "context": {"openInterest": "100000", "markPx": "110", "prevDayPx": "100",
                    "dayNtlVlm": "50000000", "funding": "0"},
    }]}})
    ctx = type("C", (), {"senpi_mcp": mcp, "wallet": "0xw", "state": None})()
    u = scan.fetch_universe(ctx, {})
    assert len(u) == 1
    assert abs(u[0]["p24h"] - 10.0) < 1e-9, u[0]


def test_a_missing_prevdaypx_scores_as_no_alignment_not_a_fabricated_direction():
    mcp = _MCP({"market_list_instruments": {"instruments": [{
        "name": "BTC",
        "context": {"openInterest": "100000", "markPx": "110",
                    "dayNtlVlm": "50000000", "funding": "0"},
    }]}})
    ctx = type("C", (), {"senpi_mcp": mcp, "wallet": "0xw", "state": None})()
    assert scan.fetch_universe(ctx, {})[0]["p24h"] == 0.0


# ── 5. the scorer: direction is price-led, the cohort can only veto ─────────────────────

ASSET = {"coin": "BTC", "oi_usd": 5_000_000, "volume_24h": 1e8, "price": 100.0,
         "funding": 0.0, "p24h": 5.0}
UP = {"p4h": 3.0, "p1h": 1.0, "c15m": 2.0}
DOWN = {"p4h": -3.0, "p1h": -1.0, "c15m": 2.0}       # v2-quirk: velocity floor same sign
BULL_COHORT = {"direction": "LONG", "total_positioned": 60, "conviction_pct": 85.0}


def test_a_clean_long_scores_and_carries_the_new_factors():
    sig = scoring.evaluate_trend_continuation(ASSET, UP, BULL_COHORT, None, 14)
    assert sig and sig["direction"] == "LONG"
    assert any("24H_ALIGNED" in r for r in sig["reasons"]), sig["reasons"]
    assert any("1H_4H" in r for r in sig["reasons"]), sig["reasons"]
    assert any("COHORT_STRONGLY_TILTED" in r for r in sig["reasons"]), sig["reasons"]


def test_the_cohort_vetoes_a_signal_it_disagrees_with():
    bear = dict(BULL_COHORT, direction="SHORT")
    assert scoring.evaluate_trend_continuation(ASSET, UP, bear, None, 14) is None


def test_no_cohort_means_no_signal():
    assert scoring.evaluate_trend_continuation(ASSET, UP, None, None, 14) is None


def test_a_thin_cohort_is_rejected():
    thin = dict(BULL_COHORT, total_positioned=3)
    assert scoring.evaluate_trend_continuation(ASSET, UP, thin, None, 14) is None


def test_low_conviction_is_rejected():
    weak = dict(BULL_COHORT, conviction_pct=55.0)
    assert scoring.evaluate_trend_continuation(ASSET, UP, weak, None, 14) is None


def test_direction_comes_from_PRICE_not_from_the_cohort():
    """The non-circularity claim: flip the price action and the signal flips, so long as the
    cohort agrees. The cohort is a veto, never the source of direction."""
    bear_cohort = {"direction": "SHORT", "total_positioned": 60, "conviction_pct": 85.0}
    down = scoring.evaluate_trend_continuation(dict(ASSET, p24h=-5.0), DOWN, bear_cohort, None, 14)
    assert down and down["direction"] == "SHORT"


def test_a_24h_disagreement_scores_lower_than_agreement():
    agree = scoring.evaluate_trend_continuation(dict(ASSET, p24h=5.0), UP, BULL_COHORT, None, 14)
    disagree = scoring.evaluate_trend_continuation(dict(ASSET, p24h=-5.0), UP, BULL_COHORT, None, 14)
    assert agree["score"] > disagree["score"], "the 24h factor is not affecting the score"


# ── 6. the config: flat sizing, no clocks, the stated risk ──────────────────────────────

def test_sizing_is_flat_80_for_every_score_above_the_floor():
    tiers = scan._coerce_tiers(_inputs(_rt())["leverageTiers"])
    for score in (13, 15, 17, 19):
        lev, margin = scoring.get_sizing_for_score(score, tiers)
        assert (lev, margin) == (10, 80), f"score {score} sized {lev}x/{margin}%"


def test_no_clock_closes_anything():
    d = _rt()["exit"]["dsl_preset"]
    for clock in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert clock in d, f"{clock} must be declared, not defaulted"
        assert d[clock]["enabled"] is False, f"{clock} is enabled"


def test_the_stop_is_15pct_roe_and_the_halt_absorbs_two_of_them():
    rt = _rt()
    i, d, g = _inputs(rt), rt["exit"]["dsl_preset"], rt["risk"]["guard_rails"]
    stop_roe = d["phase1"]["max_loss_pct"]
    assert stop_roe == 15, "the per-position stop must be 15% ROE"
    cost = stop_roe * i["marginPct"] / 100.0          # % of the whole wallet
    assert abs(cost - 12.0) < 0.01, f"one stop costs {cost:.2f}% of equity"
    absorbed = g["drawdown_halt_pct"] / cost
    assert 2.0 <= absorbed < 3.0, (
        f"the halt absorbs {absorbed:.1f} stops — if this moved, the card's risk copy is stale")
    assert g["drawdown_reset_on_day_rollover"] is False


def test_one_slot_and_one_entry_per_day():
    rt, mf = _rt(), _manifest()
    assert rt["strategy"]["slots"] == 1
    assert _inputs(rt)["maxPositions"] == 1
    assert rt["risk"]["guard_rails"]["max_entries_per_day"] == 1
    assert mf["catalog"]["max_slots"] == 1
    assert mf["catalog"]["leverage_max"] == 10


def test_it_declares_itself_a_variant_of_condor():
    assert _manifest()["catalog"]["varies"] == "condor"


def test_the_shared_gates_match_the_parent_so_only_the_intended_things_moved():
    """condor and this fork must agree on the gates that define the edge. If a 3TF floor or
    the macro gate drifts, this stops being a sizing/confirmation variant."""
    mine, parent = _inputs(_rt()), _inputs(_rt("condor"))
    for key in ("universeSize", "minOiUsd", "minScore", "min4hMagnitude",
                "min1hMagnitude", "min15mVelocity", "macroGateThresholdPct"):
        assert mine[key] == parent[key], f"{key} drifted from condor: {mine[key]} vs {parent[key]}"


def test_the_board_no_longer_supplies_direction():
    """condor gated on the board's `is_dominant_direction` and its gain share. Both are gone;
    the board is market data only."""
    import inspect
    src = inspect.getsource(scan.fetch_price_changes)
    assert "is_dominant" not in src
    assert "gain_share" not in src
    assert "pct_of_top_traders_gain" not in src


# ── 7. the emit path — the gap that shipped a live KeyError ────────────────────────────

def _emit_mcp():
    """Shaped like the live responses: a strong mover the proven cohort is long."""
    def inst(coin, px=100.0):
        return {"name": coin, "context": {"openInterest": "100000", "markPx": str(px),
                                          "prevDayPx": str(px * 0.95), "dayNtlVlm": "500000000",
                                          "funding": "0.0003"}}
    def row(tok, p4h):
        return {"token": tok, "dex": "", "token_price_change_pct_4h": p4h,
                "token_price_change_pct_1h": p4h / 4, "contribution_pct_change_15m": 2.5,
                "contribution_pct_change_1h": 1.0}
    return _MCP({
        "market_list_instruments": {"data": {"instruments": [inst("BTC"), inst("ETH", 50.0)]}},
        "leaderboard_get_markets": {"data": {"markets": [row("BTC", 5.0), row("ETH", 4.0)]}},
        "discovery_get_top_traders": {"data": {"traders": [
            {"address": f"0x{i:040x}", "realizedProfitAndLoss": 5_000_000} for i in range(60)]}},
        "discovery_get_trader_state": {"data": {"traders": [
            {"address": f"0x{i:040x}", "openPositions": [{"coin": "BTC", "szi": "1.0"}]}
            for i in range(60)]}},
        "strategy_get_clearinghouse_state": {"data": {
            "main": {"assetPositions": [], "withdrawable": "5000",
                     "marginSummary": {"accountValue": "5000"}},
            "xyz": {"assetPositions": [], "withdrawable": "5000",
                    "marginSummary": {"accountValue": "5000"}}}},
        "strategy_get_asset_trading_limits": {"data": {"max_leverage": 20}},
    })


class _St:
    def __init__(self): self._l = []
    def last(self): return self._l[-1] if self._l else None
    def append(self, d): self._l.append(d)


def _emit_run():
    ctx = type("C", (), {"senpi_mcp": _emit_mcp(), "wallet": "0xw", "state": _St()})()
    return scan.scan(dict(_inputs(_rt())), ctx)


def test_a_qualifying_candidate_emits_without_raising():
    """Before the fix this raised KeyError('sm_gain_share') — on a live $600 wallet, six
    ticks running, the moment the cohort finally produced a candidate."""
    out = _emit_run()
    assert out, "no signal emitted from a board + cohort that should qualify"
    assert out[0]["direction"] == "LONG"


def test_every_emitted_key_is_declared_and_the_24h_factor_reaches_the_signal():
    sig = _emit_run()[0]
    declared = set(next(s for s in _rt()["scanners"]
                        if s.get("type") == "external_scanner")["signal_data_schema"])
    undeclared = set(sig["data"]) - declared
    assert not undeclared, f"emitted but not declared: {sorted(undeclared)}"
    assert "priceChange24hPct" in sig["data"], (
        "the 24h factor is declared in the schema and scored, so it must reach the signal")
    assert sig["data"]["cohortConvictionPct"] > 0
    assert "smGainSharePct" not in sig["data"], "a board-era key survived the migration"
