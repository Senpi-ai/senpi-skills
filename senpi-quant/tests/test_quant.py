"""senpi-quant — offline tests: synthetic fills for the engine's rules, the recorded public fixture for the
whole pipeline. No network."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import hl_api  # noqa: E402
import market  # noqa: E402
import metrics  # noqa: E402
import score  # noqa: E402
import senpi_history  # noqa: E402
import smart_money  # noqa: E402
import timing  # noqa: E402
from roundtrips import episodes_from_fills, coverage  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "sample_trader.json")
H = 3_600_000


def fill(coin, side, sz, px, t, start, tid, dir_=None, pnl=0.0, fee=0.1, crossed=True, twap=None):
    return dict(coin=coin, side=side, sz=str(sz), px=str(px), time=t, startPosition=str(start), dir=dir_ or ("Open Long" if side == "B" else "Close Long"),
                closedPnl=str(pnl), hash="0x0", oid=tid, crossed=crossed, fee=str(fee), tid=tid, feeToken="USDC", twapId=twap)


# ---------------------------------------------------------------- round trips
def test_open_add_partial_close_close_is_one_episode():
    fs = [fill("ETH", "B", 1, 100, 0, 0, 1), fill("ETH", "B", 1, 110, H, 1, 2), fill("ETH", "A", 1, 120, 2 * H, 2, 3, pnl=15),
          fill("ETH", "A", 1, 130, 3 * H, 1, 4, pnl=25)]
    closed, opened = episodes_from_fills(fs)
    assert len(closed) == 1 and not opened
    e = closed[0]
    assert e["direction"] == "LONG" and e["adds"] == 1 and e["partial_closes"] == 1 and e["complete"] and e["win"]
    assert abs(e["entry_vwap"] - 105) < 1e-9 and abs(e["exit_vwap"] - 125) < 1e-9 and abs(e["realized"] - 40) < 1e-9
    assert e["hold_h"] == 3 and e["peak_size"] == 2 and e["peak_notional"] == 240      # peak size 2 at the $120 fill


def test_flip_closes_and_opens_at_the_same_fill():
    fs = [fill("SOL", "B", 2, 50, 0, 0, 1), fill("SOL", "A", 5, 60, H, 2, 2, dir_="Long > Short", pnl=20)]
    closed, opened = episodes_from_fills(fs)
    assert len(closed) == 1 and closed[0]["direction"] == "LONG" and closed[0]["realized"] == 20
    assert len(opened) == 1 and opened[0]["direction"] == "SHORT" and opened[0]["peak_size"] == 3 and opened[0]["entry_vwap"] == 60


def test_truncated_and_liquidated_flags():
    fs = [fill("BTC", "A", 1, 100, 0, 1, 1, pnl=-30, dir_="Liquidated Isolated Long")]
    closed, _ = episodes_from_fills(fs)
    assert closed[0]["truncated"] and closed[0]["liquidated"] and not closed[0]["complete"]


def test_gap_resyncs_and_never_invents_an_episode():
    # position 1 → (unobserved buy of 2) → observed start 3 → close 3
    fs = [fill("ETH", "B", 1, 100, 0, 0, 1), fill("ETH", "A", 3, 110, 2 * H, 3, 2, pnl=30)]
    closed, opened = episodes_from_fills(fs)
    assert len(closed) == 1 and not opened
    e = closed[0]
    assert e["unobserved_qty"] == 2 and not e["complete"] and e["close_observed"] and e["realized"] == 30
    assert coverage(closed) < 1.0
    # position 1 → (unobserved close) → observed fresh open from 0
    fs = [fill("ETH", "B", 1, 100, 0, 0, 1), fill("ETH", "B", 1, 90, 5 * H, 0, 2)]
    closed, opened = episodes_from_fills(fs)
    assert len(closed) == 1 and not closed[0]["close_observed"] and len(opened) == 1 and opened[0]["complete"] is False


def test_spot_fills_are_ignored():
    closed, opened = episodes_from_fills([fill("@107", "B", 1, 1, 0, 0, 1), fill("PURR/USDC", "B", 1, 1, 0, 0, 2)])
    assert not closed and not opened


# ---------------------------------------------------------------- metrics
def _book_inputs():
    cs = {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "700"}, "withdrawable": "300",
          "assetPositions": [{"position": {"coin": "ETH", "szi": "2", "entryPx": "100", "leverage": {"type": "cross", "value": 10}, "liquidationPx": "95",
                                           "marginUsed": "20", "unrealizedPnl": "10", "returnOnEquity": "0.5", "cumFunding": {"sinceOpen": "1"}}},
                             {"position": {"coin": "SOL", "szi": "-10", "entryPx": "50", "leverage": {"type": "isolated", "value": 5}, "liquidationPx": "60",
                                           "marginUsed": "100", "unrealizedPnl": "-5", "returnOnEquity": "-0.05", "cumFunding": {"sinceOpen": "0"}}}]}
    oo = [dict(coin="ETH", side="A", sz="1", isTrigger=True, triggerPx="98", triggerCondition="Price below 98"),   # stop for half the long
          dict(coin="ETH", side="A", sz="1", isTrigger=True, triggerPx="120", triggerCondition="Price above 120"),  # a take-profit, not a stop
          dict(coin="SOL", side="B", sz="10", isTrigger=True, triggerPx="55", triggerCondition="Price above 55")]  # full stop for the short
    ctxs = [{"universe": [{"name": "ETH"}, {"name": "SOL"}, {"name": "BTC"}]}, [{"markPx": "105", "funding": "0.0000125", "openInterest": "1000", "dayNtlVlm": "1"},
                                                                                {"markPx": "50", "funding": "-0.00001", "openInterest": "1000", "dayNtlVlm": "1"},
                                                                                {"markPx": "70000", "funding": "0", "openInterest": "1", "dayNtlVlm": "1"}]]
    return cs, oo, ctxs


def test_open_book_protection_and_funding():
    cs, oo, ctxs = _book_inputs()
    b = metrics.open_book(cs, oo, ctxs)
    eth, sol = b["positions"]
    assert eth["stop_covered_share"] == 0.5 and eth["take_profit"] and eth["stop_px"] == 98 and abs(eth["liq_distance_pct"] - 100 * 10 / 105) < 1e-9
    assert sol["stop_covered_share"] == 1.0 and not sol["take_profit"]
    assert b["partial"] == ["ETH"] and b["naked"] == [] and abs(b["margin_utilization"] - 0.7) < 1e-9
    assert eth["funding_per_day"] < 0 and sol["funding_per_day"] < 0      # long pays positive funding; short pays negative funding
    assert b["net_exposure"] == eth["notional"] - sol["notional"]


def test_track_record_and_costs():
    fs = [fill("ETH", "B", 1, 100, 0, 0, 1, fee=1), fill("ETH", "A", 1, 120, 10 * H, 1, 2, pnl=20, fee=1),
          fill("ETH", "B", 1, 100, 20 * H, 0, 3, fee=1, crossed=False), fill("ETH", "A", 1, 90, 60 * H, 1, 4, pnl=-10, fee=1)]
    closed, opened = episodes_from_fills(fs)
    funding = [{"time": 5 * H, "delta": {"coin": "ETH", "usdc": "-2"}}]
    tr = metrics.track_record(closed, opened, funding, {"userCrossRate": "0.0004", "userAddRate": "0.0001"}, 0)
    assert tr["trades"] == 2 and tr["win_rate"] == 0.5 and tr["profit_factor"] == 2 and tr["gross_realized"] == 10 and tr["fees"] == 4 and tr["funding"] == -2
    assert tr["net"] == 4 and abs(tr["cost_ratio"] - 0.6) < 1e-9 and tr["hold_winners_h"] is None      # below MIN_HOLD_N on purpose
    assert abs(tr["taker_share"] - 310 / 410) < 1e-9 and abs(tr["fee_recoverable"] - 310 * 0.0003) < 1e-9
    assert tr["coins"]["ETH"]["trades"] == 2 and tr["long"]["trades"] == 2 and tr["short"]["trades"] == 0


def test_drawdown_is_transfer_adjusted():
    portfolio = [["allTime", {"accountValueHistory": [[0, "1000"], [1, "1100"], [2, "600"], [3, "650"]], "pnlHistory": [[0, "0"], [1, "100"], [2, "100"], [3, "150"]], "vlm": "0"}]]
    ledger = [{"time": 2, "delta": {"type": "withdraw", "amount": "500"}}]
    eq = metrics.equity_curve(portfolio, metrics.flows(ledger, "0xabc"), 0)
    assert [v for _, v in eq] == [1000, 1100, 1100, 1150]
    assert metrics.drawdown(eq)["dd"] == 0
    eq2 = metrics.equity_curve(portfolio, [], 0)
    dd = metrics.drawdown(eq2)
    assert dd["dd"] == 500 and abs(dd["dd_pct"] - 500 / 1100) < 1e-9


def test_coverage_reads_the_wallets_own_daily_volume():
    fs = [fill("ETH", "B", 1, 100, 0, 0, 1, crossed=False), fill("ETH", "A", 1, 100, H, 1, 2, crossed=True)]
    closed, opened = episodes_from_fills(fs)
    cov = metrics.coverage(closed, opened, fs, {"dailyUserVlm": [{"date": "1970-01-01", "userCross": "200", "userAdd": "100"}]})
    assert cov["overall"] == 1.0 and cov["volume_ratio"]["maker"] == 1.0 and cov["volume_ratio"]["taker"] == 0.5
    gap = [fill("ETH", "B", 1, 100, 0, 0, 1), fill("ETH", "A", 3, 100, H, 3, 2)]                 # 2 units bought unseen
    closed, opened = episodes_from_fills(gap)
    assert abs(metrics.coverage(closed, opened)["overall"] - 400 / 600) < 1e-9


# ---------------------------------------------------------------- timing
def _candles(coin, prices, t0=0):
    return {coin: [[t0 + i * H, p, p * 1.01, p * 0.99, p, 1.0] for i, p in enumerate(prices)]}


def test_timing_chase_excursion_and_counterfactuals():
    # price runs 100→110 over 24h before entry (chased), peaks at 121 then fades to 100 by exit 60h later
    prices = [100 + i * 10 / 24 for i in range(25)] + [110 + 11 * min(1, i / 10) for i in range(11)] + [121 - 21 * min(1, i / 49) for i in range(50)]
    c = timing.load_candles(_candles("ETH", prices))
    ep = dict(coin="ETH", direction="LONG", entry_vwap=110.0, open_time=24 * H, close_time=84 * H, peak_size=1.0, realized=-10.0, hold_h=60.0, win=False, complete=True)
    rows = timing.per_trade([ep], c)
    r = rows[0]
    assert r["chased"] and abs(r["pre24"] - 0.1) < 1e-6 and r["mfe"] > 0.10 and r["mae"] < 0
    assert all(v is not None and v > 0 for v in r["lock_cf"].values())      # locking the peak beats the −10 realized
    assert r["cut_cf"]["12"] is not None and r["cut_cf"]["12"] > 0            # a 12h cut exits above the eventual −10
    s = timing.summarize(rows)
    assert s["chased_n"] == 1 and s["lock"]["robust"] is not None and s["cut"]["robust"] is not None


def test_counterfactual_is_rejected_when_not_robust():
    g = timing._grid({"12": [-100.0], "24": [50.0], "48": [-20.0]})
    assert g["robust"] is None
    g = timing._grid({"12": [100.0], "24": [50.0], "48": [-20.0]})
    assert g["robust"] == 50.0


# ---------------------------------------------------------------- market
def test_market_fit_classification():
    up = timing.load_candles(_candles("ETH", [100 * (1 + 0.001 * i) for i in range(800)]))
    r = market.coin_regime("ETH", up, {"funding": "0.0000125", "openInterest": "10", "markPx": "100"})
    assert r["trend"] == "UP" and abs(r["funding_bp_8h"] - 1.0) < 1e-9
    assert market.fit({"side": "LONG"}, r) == "WITH THE MARKET" and market.fit({"side": "SHORT"}, r) == "AGAINST THE MARKET"
    flat = timing.load_candles(_candles("ETH", [100 + (i % 3) for i in range(800)]))
    assert market.coin_regime("ETH", flat, {})["trend"] == "RANGING"


# ---------------------------------------------------------------- smart money
def test_cohort_reads():
    per = smart_money.public_positions({"a": {"assetPositions": [{"position": {"coin": "ETH", "szi": "1", "positionValue": "100"}}]},
                                         "b": {"assetPositions": [{"position": {"coin": "ETH", "szi": "1", "positionValue": "300"}}, {"position": {"coin": "SOL", "szi": "-1", "positionValue": "50"}}]},
                                         "c": {"assetPositions": [{"position": {"coin": "ETH", "szi": "-2", "positionValue": "100"}}]}})
    assert per["ETH"]["members"] == 3 and abs(per["ETH"]["bias"] - 0.6) < 1e-9 and per["SOL"]["members"] == 1
    book = {"positions": [{"coin": "ETH", "side": "SHORT", "leverage": 3}, {"coin": "SOL", "side": "SHORT", "leverage": 2}, {"coin": "BTC", "side": "LONG", "leverage": 1}]}
    cmp_ = smart_money.compare(per, book, [])
    reads = {r["coin"]: r["read"] for r in cmp_["rows"]}
    assert reads == {"ETH": "AGAINST SMART MONEY", "SOL": "NO COHORT VIEW", "BTC": "NO COHORT VIEW"} and cmp_["against"] == ["ETH"]


def test_senpi_cohort_with_entry_times_reads_late():
    class C:
        def mcp_call(self, tool, timeout=12, **kw):
            if tool == "discovery_get_top_traders":
                return {"success": True, "data": {"traders": [{"address": f"0x{i:040x}", "realizedProfitAndLoss": 2_000_000} for i in range(4)]}} if kw["offset"] == 0 else {"success": True, "data": {"traders": []}}
            return {"success": True, "data": {"traders": [{"openPositions": [{"coin": "ETH", "szi": "5", "positionValue": "1000", "startTime": 1_700_000_000}]} for _ in kw["trader_addresses"]]}}
    meta = {}
    addrs = smart_money.senpi_cohort(C(), meta)
    per = smart_money.senpi_positions(C(), addrs, meta)
    assert len(addrs) == 4 and per["ETH"]["members"] == 4 and per["ETH"]["entries_long"] == [1_700_000_000_000] * 4
    book = {"positions": [{"coin": "ETH", "side": "LONG", "leverage": 5}]}
    cmp_ = smart_money.compare(per, book, [{"coin": "ETH", "open_time": 1_700_000_000_000 + 10 * H}])
    assert cmp_["rows"][0]["read"] == "WITH — BUT LATE (+10h)" and cmp_["entry_lag_h"] == 10


# ---------------------------------------------------------------- senpi history mapping
def test_senpi_history_row_maps_to_the_episode_schema():
    row = {"closedOrderId": "0x1", "coin": "BTC", "coinDisplayName": "BTC", "entryPx": "42150.50", "exitPx": "43200.00", "leverage": {"type": "cross", "value": 5},
           "openTime": 1699564800000, "closeTime": 1699651200000, "szi": "-0.5", "realizedPnl": "-524.75", "marginUsed": "4215.05", "totalFills": "3", "totalFees": "8.43"}
    e = senpi_history.episode(row)
    assert e["direction"] == "SHORT" and e["hold_h"] == 24 and e["realized"] == -524.75 and e["fees"] == 8.43 and e["leverage"] == 5 and e["complete"] and not e["win"]
    assert e["entry_vwap"] == 42150.5 and e["peak_notional"] == 0.5 * 42150.5 and e["source"] == "senpi"


# ---------------------------------------------------------------- score
def test_dimensions_are_bounded_and_explained():
    cs, oo, ctxs = _book_inputs()
    book = metrics.open_book(cs, oo, ctxs)
    tr = dict(trades=20, complete_trades=20, wins=9, losses=11, win_rate=0.45, profit_factor=1.8, gross_realized=1000, fees=150, funding=-50, net=800, cost_ratio=0.2,
              payoff_ratio=2.2, hold_winners_h=10, hold_losers_h=30, hold_ratio=3.0, taker_share=0.8, liquidations=0, liquidation_loss=0, size_cv=0.4, size_max_over_median=2,
              coins={"ETH": {"volume_share": 0.6, "funding": -50}}, coverage=None, fee_recoverable=100, volume=100000, fee_rate_taker=0.0004, fee_rate_maker=0.0001, long_share=0.7)
    dd = {"dd_pct": 0.1, "in_drawdown": False}
    dims, q = score.dimensions(tr, book, dd, None, market.book_fit(book, {}, ctxs), None, [], [])
    assert set(dims) == set(score.WEIGHTS) and all(0 <= d["score"] <= 100 and d["line"] for d in dims.values()) and 0 <= q <= 100
    assert "3.0× longer" in dims["risk"]["line"]
    fl = score.flags(tr, book, dd, None, None, {"consistency": "CHOPPY"})
    assert "PARTIAL STOPS (1/2)" in fl and "HIGH MARGIN 70%" in fl and "CHOPPY" in fl
    v = score.verdict(tr, book, dims, [])
    assert v.endswith(".") and "—" in v


# ---------------------------------------------------------------- the whole pipeline, offline
def test_fixture_pipeline_end_to_end():
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    hl = hl_api.HLFixture(rec)
    import desk
    r = desk.analyze(rec["address"], hl, days=90, mcp=None, bench={"cost_ratio": 0.12, "n": 1, "computed_at": "test"})
    assert r["track"]["trades"] > 30 and r["book"]["positions"] and r["rank"]["rank"] == hl_api.weekly_rank(rec["hl::leaderboard"], rec["address"])["rank"]
    assert r["rank"]["of"] == len(rec["hl::leaderboard"]["leaderboardRows"])
    assert 0.3 < r["track"]["coverage"]["overall"] < 0.9                                            # the TWAP-slice gap, measured
    assert r["quant_score"] and r["archetype"] and r["verdict"] and r["flags"] and r["leaks"] and r["smart"]["rows"]
    assert any("returned" in w for w in r["meta"]["warnings"])                                         # the coverage caveat is stated
    md = __import__("render").render(r)
    for s in ("Quant score", "protection audit", "Leaks", "You vs smart money", "Market fit", "Where your edge", "Not financial advice"):
        assert s in md
    assert "1606" not in md and "3822" not in md                                                      # no absurd hold ratios on a thin sample


def test_cli_offline_runs_and_validates_addresses(tmp_path):
    env = dict(os.environ, TMPDIR=str(tmp_path))
    d = os.path.join(HERE, "..", "scripts", "desk.py")
    out = subprocess.run([sys.executable, d, "0xnope"], capture_output=True, text=True, env=env)
    assert out.returncode == 2 and "not a Hyperliquid address" in out.stdout
    with open(FIXTURE) as fh:
        addr = json.load(fh)["address"]
    out = subprocess.run([sys.executable, d, addr, "--fixture", FIXTURE, "--dry", "--state-dir", str(tmp_path), "--section", "protection"], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "protection audit" in out.stdout and "Quant score" not in out.stdout
    out = subprocess.run([sys.executable, d, addr, "--fixture", FIXTURE, "--dry", "--state-dir", str(tmp_path), "--json"], capture_output=True, text=True, env=env, timeout=120)
    doc = json.loads(out.stdout)
    assert doc["quant_score"] and "episodes" not in doc


# ---------------------------------------------------------------- the senpi-token path, offline (documented shapes)
def test_senpi_path_uses_discovery_history_and_cohort():
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    addr = rec["address"]
    now = rec["now_ms"]
    history = {"success": True, "data": {"closedPositions": [
        {"closedOrderId": f"0x{i}", "coin": "ETH", "coinDisplayName": "ETH", "entryPx": "2500", "exitPx": "2550" if i % 3 else "2450",
         "leverage": {"type": "cross", "value": 10}, "openTime": now - (i + 1) * 30 * H, "closeTime": now - i * 30 * H + 6 * H, "szi": "2",
         "realizedPnl": "100" if i % 3 else "-100", "marginUsed": "500", "totalFills": "4", "totalFees": "2.5"} for i in range(24)],
        "pageInfo": {"totalCount": 24, "hasNextPage": False}}}
    rec = dict(rec)
    rec[f"discovery_get_trader_history::{addr}"] = history
    rec["discovery_get_top_traders::ALL_TIME::0"] = {"success": True, "data": {"traders": [{"address": f"0x{i:040x}", "realizedProfitAndLoss": 5_000_000, "tcsLabel": "ELITE"} for i in range(6)]}}
    rec["discovery_get_top_traders::ALL_TIME::1000"] = {"success": True, "data": {"traders": []}}
    rec["discovery_get_top_traders::MONTHLY::0"] = {"success": True, "data": {"traders": [{"address": f"0x{i:040x}", "profitAndLoss": 900_000} for i in range(3, 9)]}}
    rec["market_get_funding_regime"] = {"success": True, "data": {"regime": "LONG_CROWDED", "extreme_count": 4, "regime_duration_hours": 12}}
    rec["leaderboard_get_markets"] = {"success": True, "data": {"markets": [{"token": "ETH", "dex": "", "direction": "short", "pct_of_top_traders_gain": 31.0, "trader_count": 9, "is_dominant_direction": True},
                                                                              {"token": "ZEC", "dex": "", "direction": "long", "pct_of_top_traders_gain": 12.0, "trader_count": 7, "is_dominant_direction": True}], "source_trader_count": 100, "window": "4h"}}
    rec["leaderboard_get_momentum_events"] = {"success": True, "data": {"events": [{"tier": 1, "tier_label": "Exceptional", "top_positions": [{"token": "ZEC", "direction": "long"}]}]}}
    rec[f"discovery_get_top_traders::{addr}"] = {"success": True, "data": {"traders": [{"address": addr, "tcsLabel": "CHOPPY", "riskLabel": "AGGRESSIVE", "activityLabel": "DEGEN"}]}}
    rec["discovery_get_trader_state::0x" + "0" * 40] = {"success": True, "data": {"traders": [{"address": f"0x{i:040x}", "openPositions": [{"coin": "ETH", "szi": "-4", "positionValue": "10000", "startTime": (now - 40 * H) // 1000}]} for i in range(6)]}}
    rec["discovery_get_trader_state::0x" + "0" * 39 + "3"] = {"success": True, "data": {"traders": [{"address": f"0x{i:040x}", "openPositions": [{"coin": "ZEC", "szi": "10", "positionValue": "12000", "startTime": (now - 3 * H) // 1000}]} for i in range(3, 9)]}}
    import desk
    hl = hl_api.HLFixture(rec)
    r = desk.analyze(addr, hl, days=90, mcp=desk._MCPFixture(rec), bench=None)
    assert r["meta"]["sources"]["trades"].startswith("Senpi discovery (24")
    assert r["track"]["trades"] == 24 and r["track"]["complete_trades"] == 24 and r["track"]["hold_winners_h"] is not None
    assert r["track"]["taker_share"] is not None                              # execution read stays fill-level
    assert {k: r["labels"][k] for k in ("consistency", "risk", "activity")} == {"consistency": "CHOPPY", "risk": "AGGRESSIVE", "activity": "DEGEN"} and "CHOPPY" in r["flags"]
    eth = next(x for x in r["smart"]["rows"] if x["coin"] == "ETH")
    assert eth["read"] == "AGAINST SMART MONEY" and r["smart"]["source"].startswith("Senpi discovery")
    assert [c["name"] for c in r["cohorts"]] == ["proven", "hot"]
    hot = r["cohorts"][1]; zec = next(x for x in hot["rows"] if x["coin"] == "ZEC")
    assert zec["read"].startswith("WITH") and hot["wallets"] == 6
    ctx = r["context"]
    assert ctx["funding_regime"]["regime"] == "LONG_CROWDED" and ctx["attention"]["markets"][0]["coin"] == "ETH" and ctx["attention"]["overlap"][0]["read"] == "AGAINST"
    assert "ZEC" in ctx["attention"]["with_momentum"]
    assert any(o["coin"] == "ZEC" for o in r["opportunities"]) and r["followups"] and r["strategy"]["statements"]
    md = __import__("render").render(r)
    assert "The proven cohort" in md and "The hot 30-day cohort" in md and "LONG_CROWDED" in md and "Your quant is ready to go deeper" in md


# ---------------------------------------------------------------- v2: taxonomy, strategy read, market context, cohorts, matches, follow-ups, deep modes
import deep  # noqa: E402
import followups  # noqa: E402
import opportunities  # noqa: E402
import strategy_read  # noqa: E402
import taxonomy  # noqa: E402


def _ctxs(names_oi):
    return [{"universe": [{"name": n} for n, _ in names_oi]}, [{"openInterest": str(oi), "markPx": "1", "prevDayPx": "1", "funding": "0", "dayNtlVlm": "1"} for _, oi in names_oi]]


def test_taxonomy_tiers_and_memes():
    ctxs = _ctxs([("BTC", 100), ("ETH", 90), ("HYPE", 80), ("SOL", 50), ("XRP", 40), ("kPEPE", 30), ("FARTCOIN", 20)] + [(f"MID{i}", 10 - i * 0.1) for i in range(14)] + [("OBSCURE", 1)])
    majors, large = taxonomy.crypto_tiers(ctxs)
    assert majors == {"BTC", "ETH", "HYPE"} and "SOL" in large
    assert taxonomy.classify("kPEPE", majors, large) == "memes" and taxonomy.classify("FARTCOIN", majors, large) == "memes"
    assert taxonomy.classify("OBSCURE", majors, large) == "alts" and taxonomy.classify("xyz:NVDA", majors, large) == "xyz_equities" and taxonomy.classify("xyz:GOLD", majors, large) == "xyz_commodities"


def _ep(coin, side, open_h, close_h, realized, win=None, notional=1000.0):
    return dict(coin=coin, direction=side, open_time=open_h * H, close_time=close_h * H, last_time=close_h * H, realized=realized, win=(realized > 0) if win is None else win,
                fees=1.0, volume=notional, taker_volume=notional, peak_notional=notional, complete=True, truncated=False, hold_h=close_h - open_h, adds=0, entry_vwap=1.0)


def test_strategy_read_hedged_book_and_receipts():
    ctxs = _ctxs([("BTC", 100), ("ETH", 90), ("SOL", 80), ("kPEPE", 10), ("WIF", 9)])
    closed = [_ep("kPEPE", "SHORT", 0, 30, -50), _ep("WIF", "SHORT", 5, 40, -20), _ep("kPEPE", "SHORT", 50, 60, -10), _ep("ETH", "LONG", 0, 35, 300), _ep("SOL", "LONG", 10, 45, 200), _ep("ETH", "LONG", 50, 70, 100)]
    book = {"positions": [{"coin": "ETH", "side": "LONG", "notional": 800.0}, {"coin": "kPEPE", "side": "SHORT", "notional": 200.0}], "net_exposure": 600.0, "account_value": 1000.0}
    tr = dict(long_share=0.5, adds_per_trade=0.0, hold_winners_h=30, hold_losers_h=20, trades=6)
    act = dict(active_days=10, twap_share=0.0)
    fp = strategy_read.fingerprint(closed, [], book, tr, act, None, {}, ctxs, [], 0, 80 * H)
    assert fp["simultaneity"]["both_share"] > 0.5 and fp["dead_sides"] and fp["dead_sides"][0]["label"] == "memecoins"
    assert fp["net_over_gross"] == 0.6 and fp["outcome_concentration"] > 1.0      # the three winners exceed the net: the rest is negative
    st = strategy_read.statements(fp, tr, book)
    assert any("longs and shorts at once" in x for x in st) and any("memecoins shorts have never paid" in x for x in st)
    cr = strategy_read.critique(fp, tr, book, None, None)
    assert any("Stop trading memecoins shorts" in c for c in cr)


def test_breadth_day_classification_and_regimes():
    names = [("BTC", 100), ("ETH", 90), ("HYPE", 80)] + [(f"ALT{i}", 10) for i in range(12)] + [("kPEPE", 5), ("WIF", 4), ("PUMP", 3)]
    ctxs = _ctxs(names)
    for c in ctxs[1]:
        c["prevDayPx"] = "1"; c["markPx"] = "0.97"       # everything down 3%
    b = market.breadth(ctxs, None)
    assert b["day"] == "risk_off" and b["share_up"] == 0.0 and b["groups"]["memes"]["down"] == 3
    daily = {"BTC": [[i * market.DAY_MS, 100 - i, 100 - i, 100 - i, 100 - i, 1] for i in range(6)], "ETH": [[i * market.DAY_MS, 50 - i, 50 - i, 50 - i, 50 - i, 1] for i in range(6)]}
    reg = market.daily_regimes(daily, ["BTC", "ETH"])
    assert all(v["label"] == "risk_off" for d, v in reg.items() if d > 0)
    closed = [_ep("BTC", "LONG", 30, 40, -10), _ep("BTC", "SHORT", 54, 60, 30)]
    rp = market.regime_performance(closed, reg)
    assert rp["cells"]["risk_off/SHORT"]["wins"] == 1 and rp["cells"]["risk_off/ALL"]["trades"] == 2


def test_attention_and_funding_regime_parsers():
    mk = {"success": True, "data": {"markets": [{"token": "ETH", "dex": "", "direction": "long", "pct_of_top_traders_gain": 38.0, "trader_count": 12, "is_dominant_direction": True},
                                                {"token": "ETH", "dex": "", "direction": "short", "pct_of_top_traders_gain": 0.2, "trader_count": 1, "is_dominant_direction": False},
                                                {"token": "GOLD", "dex": "xyz", "direction": "long", "pct_of_top_traders_gain": 9.0, "trader_count": 6, "is_dominant_direction": True}]}}
    mo = {"success": True, "data": {"events": [{"tier": 1, "top_positions": [{"token": "ETH", "direction": "long"}, {"token": "ZEC", "direction": "short"}]}]}}
    book = {"positions": [{"coin": "ETH", "side": "SHORT"}, {"coin": "ZEC", "side": "SHORT"}]}
    at = market.attention(mk, mo, book)
    assert at["markets"][0]["coin"] == "ETH" and at["markets"][1]["coin"] == "xyz:GOLD" and at["overlap"][0]["read"] == "AGAINST"
    assert at["with_momentum"] == ["ZEC"] and at["against_momentum"] == ["ETH"]
    assert market.funding_regime({"success": True, "data": {"regime": "LONG_CROWDED", "extreme_count": 7}})["regime"] == "LONG_CROWDED"
    assert market.funding_regime("NEUTRAL")["regime"] == "NEUTRAL"


def test_cohort_view_tilt_and_they_hold():
    ctxs = _ctxs([("BTC", 100), ("ETH", 90), ("HYPE", 80), ("SOL", 50), ("kPEPE", 5)])
    majors, large = taxonomy.crypto_tiers(ctxs)
    bks = [dict(address=f"0x{i:040x}", positions=[("ETH", 1000.0, None), ("kPEPE", -200.0, None)]) for i in range(5)] + [dict(address="0xz", positions=[("SOL", 500.0, None)])]
    book = {"positions": [{"coin": "kPEPE", "side": "LONG", "notional": 300.0, "leverage": 5}, {"coin": "SOL", "side": "LONG", "notional": 100.0, "leverage": 2}], "net_exposure": 400.0}
    cv = smart_money.cohort_view("proven", bks, book, [], majors, large)
    assert cv["wallets"] == 6 and cv["against"] == ["kPEPE"] and cv["they_hold"][0]["coin"] == "ETH" and cv["they_hold"][0]["members"] == 5
    assert cv["agreement"] is not None and cv["agreement"] < 0
    assert any(t["label"] == "memecoins" and t["bias"] < 0 for t in cv["tilt"])


def test_scout_ranks_by_cohort_tape_and_pattern():
    ctxs = _ctxs([("BTC", 100), ("ETH", 90), ("HYPE", 80), ("SOL", 50)])
    majors, large = taxonomy.crypto_tiers(ctxs)
    closed = [_ep("ETH", "LONG", i * 10, i * 10 + 5, 100) for i in range(5)]
    setups = {"best": [{"label": "ETH longs", "n": 5, "wins": 5, "realized": 500, "profit_factor": float("inf")}]}
    book = {"positions": []}
    breadth = {"assets": {"ETH": {"funding_bp_8h": -4.0, "change_pct": 1.0}, "SOL": {"funding_bp_8h": 25.0, "change_pct": 9.0}}}
    regimes = {"ETH": {"trend": "UP"}, "SOL": {"trend": "UP"}}
    cohorts = [{"name": "proven", "they_hold": [{"coin": "ETH", "members": 12, "bias": 0.9, "side": "LONG"}, {"coin": "SOL", "members": 8, "bias": 0.8, "side": "LONG"}], "rows": []}]
    opps = opportunities.scout(closed, setups, book, breadth, regimes, cohorts, None, majors, large)
    assert opps and opps[0]["coin"] == "ETH" and opps[0]["score"] > (opps[1]["score"] if len(opps) > 1 else 0)
    assert any("chase" in w for o in opps if o["coin"] == "SOL" for w in o["why"])


def test_followups_offer_protect_first_when_naked():
    r = {"book": {"naked": ["ETH"], "partial": [], "positions": [{"liq_distance_pct": 3.0}], "account_value": 1000, "funding_per_day": -5}, "track": {"trades": 40, "largest_loss": -100},
         "timing": {}, "leaks": [{"title": "You hold losers 3× longer"}], "cohorts": [{"against": ["ETH"], "they_hold": [1]}], "opportunities": [1], "setups": {"best": [1]}, "context": {}, "strategy": {"critique": ["x"]}}
    fu = followups.offer(r, n=4)
    assert fu[0]["mode"] == "protect" and len(fu) == 4 and all(f["prompt"].endswith("?") for f in fu)


def test_deep_modes_run_on_a_cached_analysis():
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    import desk
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None)
    candles = timing.load_candles(hl_api.HLFixture(rec).candles(sorted({p["coin"] for p in r["book"]["positions"]}), days=91))
    p = deep.protect(r, candles)
    assert p["rows"] and all(x["hard_stop_pct"] > 0 for x in p["rows"]) and p["total_risk_after"] < p["total_risk_now"]
    assert deep.funding_forecast(r)["rows"] and deep.compare_windows(r)["recent"] and deep.rules(r)["families"]
    rp = deep.replay(r, candles)
    assert rp is None or rp.get("empty") or rp["trades"] >= 1
    for mode in ("protect", "funding", "compare", "rules", "watch", "regime", "smart", "scout", "strategy"):
        data = {"protect": p, "funding": deep.funding_forecast(r), "compare": deep.compare_windows(r), "rules": deep.rules(r), "watch": deep.watch(r), "regime": deep.regime(r),
                "smart": {"cohorts": r["cohorts"]}, "scout": {"opportunities": r["opportunities"]}, "strategy": r["strategy"]}[mode]
        md = __import__("render").render_deep(mode, data, r)
        assert "Not financial advice" in md
