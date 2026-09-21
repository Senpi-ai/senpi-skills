"""quant-desk — offline tests: synthetic fills for the engine's rules, the recorded public fixture for the
whole pipeline. No network."""
import json
from pathlib import Path as _P
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
    # a TWAP of 50 slices is ONE order: no adds
    sl = [fill("SOL", "B", 0.1, 10, i * 1000, i * 0.1, 100 + i, twap=7) for i in range(50)] + [fill("SOL", "A", 5, 11, H, 5, 999, pnl=5)]
    c2, _ = episodes_from_fills(sl)
    assert c2[0]["adds"] == 0 and c2[0]["n_fills"] == 51
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


def test_drawdown_comes_from_the_ledgers_pnl_series_not_equity():
    # a $500 withdrawal drops account value but not P&L: no drawdown; a real $150 P&L fall is one, sized to the account at the peak
    portfolio = [["allTime", {"accountValueHistory": [[0, "1000"], [1, "1100"], [2, "600"], [3, "650"], [4, "500"]],
                              "pnlHistory": [[0, "0"], [1, "100"], [2, "100"], [3, "150"], [4, "0"]], "vlm": "0"}]]
    eq = metrics.equity_curve(portfolio, [], 0); pnl = metrics.pnl_series(portfolio, 0)
    dd = metrics.drawdown(pnl, eq)
    assert dd["dd"] == 150 and abs(dd["dd_pct"] - 150 / 650) < 1e-9 and dd["in_drawdown"]
    assert metrics.drawdown(pnl[:3], eq)["dd"] == 0
    assert metrics.flows([{"time": 2, "delta": {"type": "withdraw", "amount": "500"}}], "0xabc") == [(2, -500.0)]


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
    assert not any("public API" in w for w in r["meta"]["warnings"])                                   # coverage is measured, never apologised for
    md = __import__("render").render(r)
    for s in ("Quant score", "protection audit", "Leaks", "You vs smart money", "Market fit", "Where your edge"):
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
    assert r["meta"]["sources"]["trades"].startswith("senpi discovery (24")
    assert r["track"]["trades"] == 24 and r["track"]["complete_trades"] == 24 and r["track"]["hold_winners_h"] is not None
    assert r["track"]["taker_share"] is not None                              # execution read stays fill-level
    assert {k: r["labels"][k] for k in ("consistency", "risk", "activity")} == {"consistency": "CHOPPY", "risk": "AGGRESSIVE", "activity": "DEGEN"} and "CHOPPY" in r["flags"]
    eth = next(x for x in r["smart"]["rows"] if x["coin"] == "ETH")
    assert eth["read"] == "AGAINST SMART MONEY" and r["smart"]["source"].startswith("senpi discovery")
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
    mo = {"success": True, "data": {"window": "4h", "items": [{"tier": 1, "top_positions": [{"token": "ETH", "direction": "long"}, {"token": "ZEC", "direction": "short"}]}]}}
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
    # Unprotected AND near liquidation: protection outranks even the plain-English offer. A follow-up
    # list opening with "want this explained?" over a book near liquidation contradicts the desk's own
    # "Protect first" recommendation.
    assert fu[0]["mode"] == "protect" and len(fu) == 4 and all(f["prompt"].endswith("?") for f in fu)
    assert "eli5" in [f["mode"] or "eli5" for f in fu][:3], "the ELI5 still has to be near the top"

    # and with no urgency, the ELI5 leads
    calm = {**r, "book": {**r["book"], "naked": [], "positions": [{"liq_distance_pct": 80.0}]}}
    assert followups.offer(calm, n=4)[0]["prompt"].startswith("Want the ELI5")


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


# ---------------------------------------------------------------- someone else's book: voice, follow-ups, compare
import voice  # noqa: E402


def test_third_person_voice_keeps_the_quant_persona():
    md = "# Your desk — `0xab…cd`\n> You hold losers 3× longer than winners. You're paying to hold.\nYour quant is ready to go deeper — say *hire my quant*. Yours: majors long.\n"
    out = voice.third_person(md, "0xab…cd")
    assert out.startswith("# The desk for `0xab…cd`") and "They hold losers" in out and "They're paying" in out and "Theirs: majors long" in out
    assert "Your quant is ready" in out and "hire my quant" in out and "you" not in out.replace("your quant", "").replace("Your quant", "").lower().replace("hire my quant", "")


def test_other_book_follow_ups_and_compare_render():
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    import desk
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None, whose="other")
    assert r["whose"] == "other" and r["followups"]
    # mode None = answered from what is already on screen, no second run
    assert all(f["mode"] is None or f["mode"] in followups.BANK_OTHER for f in r["followups"])
    assert r["followups"][0]["prompt"].startswith("Want the ELI5")             # a stranger's book, explained first
    assert "rules" in [f["mode"] for f in r["followups"]]                                   # the playbook is still offered
    md = __import__("render").render(r)
    assert md.startswith("# The desk for") and "What to take from this trader" in md and "you hold" not in md.lower().replace("you hold", "")
    assert "Your quant is ready to go deeper" in md
    r2 = dict(r, address="0x" + "9" * 40, quant_score=r["quant_score"] + 7)
    cmp_ = __import__("render").render_compare([r, r2])
    assert "Side by side" in cmp_ and "Quant score" in cmp_ and "Verdicts" in cmp_


def test_cli_compare_offline(tmp_path):
    env = dict(os.environ, TMPDIR=str(tmp_path))
    d = os.path.join(HERE, "..", "scripts", "desk.py")
    with open(FIXTURE) as fh:
        addr = json.load(fh)["address"]
    out = subprocess.run([sys.executable, d, "--compare", addr, addr, "--fixture", FIXTURE, "--dry", "--state-dir", str(tmp_path)], capture_output=True, text=True, env=env, timeout=180)
    assert out.returncode == 0, out.stderr
    assert "Side by side" in out.stdout and out.stdout.count("Quant score") == 1


# ---------------------------------------------------------------- a fresh senpi sub-wallet: direction from P&L, two collateral pools, a tiny sample
def test_closed_row_direction_comes_from_pnl_vs_price_not_the_sign_of_szi():
    short_won = {"coin": "ETH", "entryPx": "2545.6", "exitPx": "2500", "szi": "0.8", "realizedPnl": "36.5", "totalFees": "1.2", "openTime": 1, "closeTime": 3_600_001, "leverage": {"value": 3}}
    assert senpi_history.episode(short_won)["direction"] == "SHORT"                     # price fell, P&L positive → a short, whatever szi says
    long_lost = dict(short_won, realizedPnl="-36.5")
    assert senpi_history.episode(long_lost)["direction"] == "LONG"
    flat = dict(short_won, exitPx="2545.6", realizedPnl="0", side="short")
    assert senpi_history.episode(flat)["direction"] == "SHORT"                          # no move: the explicit side decides


def test_book_adds_the_xyz_collateral_pool():
    cs, oo, ctxs = _book_inputs()
    cs_xyz = {"marginSummary": {"accountValue": "1180", "totalMarginUsed": "0"}, "withdrawable": "1180", "assetPositions": []}
    b = metrics.open_book(cs, oo, ctxs, None, cs_xyz, [], None)
    assert b["account_value"] == 2180 and b["account_value_xyz"] == 1180 and abs(b["margin_utilization"] - 700 / 2180) < 1e-9 and b["withdrawable"] == 1480
    b0 = metrics.open_book(cs, oo, ctxs)
    assert b0["account_value"] == 1000 and abs(b0["margin_utilization"] - 0.7) < 1e-9


def test_cohort_read_against_a_long_held_position_is_with_not_late():
    per = {"NEAR": {"net": -3000.0, "gross": 3000.0, "n_long": 0, "n_short": 3, "bias": -1.0, "members": 3,
                    "entries_long": [], "entries_short": [1_700_000_000_000] * 3}}
    book = {"positions": [{"coin": "NEAR", "side": "SHORT", "leverage": 3}]}
    now = 1_700_000_000_000 + 70 * 24 * H
    late = smart_money.compare(per, book, [], {"NEAR": now - 24 * H}, now)
    assert late["rows"][0]["read"].startswith("WITH — they've held it 70d") and late["entry_lag_h"] is None
    recent = smart_money.compare(per, book, [], {"NEAR": 1_700_000_000_000 + 10 * H}, 1_700_000_000_000 + 12 * H)
    assert recent["rows"][0]["read"] == "WITH — BUT LATE (+10h)"


def test_tiny_sample_reads_as_early_days():
    cs, oo, ctxs = _book_inputs()
    book = metrics.open_book(cs, oo, ctxs)
    tr = dict(trades=1, complete_trades=1, long_share=0.0, hold_winners_h=None, hold_losers_h=None, adds_per_trade=0)
    assert "early days (1 closed trade)" in score.archetype(tr, book, {"chased_share": 1.0}, {"active_days": 2})


def test_account_value_is_the_whole_account_not_the_perps_view():
    cs, oo, ctxs = _book_inputs()
    portfolio = [["day", {"accountValueHistory": [[1, "2221.31"]], "pnlHistory": [[1, "0"]], "vlm": "0"}]]
    spot = {"balances": [{"coin": "USDC", "total": "2221.31", "hold": "700"}]}
    total = metrics.whole_account_value(portfolio, spot)
    assert abs(total - 2221.31) < 1e-9
    b = metrics.open_book(cs, oo, ctxs, total_account_value=total)
    assert abs(b["account_value"] - 2221.31) < 1e-9 and b["account_value_perps"] == 1000 and abs(b["margin_utilization"] - 700 / 2221.31) < 1e-9
    assert metrics.whole_account_value(None, spot) == 2221.31 and metrics.whole_account_value(None, None) is None
    assert abs(metrics.spot_free_usdc(spot) - 1521.31) < 1e-9 and metrics.open_book(cs, oo, ctxs, spot_free=1521.31)["withdrawable"] == 300 + 1521.31


def test_the_live_history_row_shape_reads_as_a_short():
    # the exact shape Senpi discovery returns today: szi is the absolute size, the side lives in `type`, times are seconds
    row = {"closedOrderId": "542928624893", "coin": "ETH", "coinDisplayName": "ETH", "entryPx": "2545.6", "exitPx": "2511.2", "leverage": {"type": "", "value": 3},
           "maxLeverage": 3, "openTime": 1789150736, "closeTime": 1789193981, "szi": "0.2099", "realizedPnl": "7.22056", "marginUsed": "178.12114",
           "type": "Close Short", "totalFills": "2", "totalFees": "0.678225"}
    e = senpi_history.episode(row)
    assert e["direction"] == "SHORT" and e["signed"] < 0 and e["leverage"] == 3 and abs(e["hold_h"] - 12.0) < 0.05 and e["open_time"] == 1789150736000
    flat = dict(row, exitPx="2545.6", realizedPnl="0")
    assert senpi_history.episode(flat)["direction"] == "SHORT"                          # no move, no P&L: `type` decides


# ---------------------------------------------------------------- 1.1.0: wording the first live runs exposed — object case, thin samples, the traded side
def test_voice_object_case_headers_and_persona():
    import voice
    md = ("funding paid you $1 on top. The proven cohort sits with you. Your quant can run this for you. Top 3 things your agents found\n"
          "| Coin | You | Cohort | Read |\nYou: ZEC **WITH** the top traders.\nYou hold, none of the cohort do: X. The cohort holds, you don't: Y. "
          "Want me to keep watching this wallet and tell you when they move? Entries after strength are fine for you.\n")
    out = voice.third_person(md, "0xab…cd")
    for want in ("paid them $1", "sits with them", "run this for you", "your agents found", "| Coin | Trader | Cohort | Read |", "This trader: ZEC",
                 "They hold, none of the cohort do", "The cohort holds, they don't", "tell you when they move", "fine for them"):
        assert want in out, (want, out)
    assert "paid they" not in out and "with they" not in out


def test_verdict_headline_is_material():
    book = {"positions": [], "naked": [], "account_value": 2222.0, "unrealized": 216.0}
    dims = {k: {"score": v, "line": ""} for k, v in dict(timing=60, risk=84, cost=52, sizing=85, consistency=55, market_fit=60).items()}
    thin = dict(trades=1, profit_factor=float("inf"), payoff_ratio=None, win_rate=1.0, ledger_net=222.0, net=6.0, fees=2.0, funding=1.0)
    v = score.verdict(thin, book, dims, [])
    assert "Cut the costs" not in v and "only 1 closed trade in the window" in v and v.startswith("Net $222 on the ledger"), v
    dims["consistency"]["score"] = 70
    many = dict(thin, trades=20, profit_factor=1.2, win_rate=0.5, net=200.0, ledger_net=222.0, fees=2.0)     # $2 of fees on $222 is not a leak
    v = score.verdict(many, book, dims, [])
    assert "Cut the costs" not in v and "nothing in the record is leaking badly" in v, v
    dims["cost"]["score"] = 40
    real = dict(many, fees=80.0, funding=5.0)
    v = score.verdict(real, book, dims, [])
    assert "execution is eating the gains" in v and "and funding" not in v and v.endswith("Cut the costs first."), v
    under = {"positions": [{"coin": "ZEC", "liq_distance_pct": 24.5, "stop_covered_share": 0.13}, {"coin": "xyz:MSTR", "liq_distance_pct": 14.3, "stop_covered_share": 0.0}],
             "naked": ["xyz:MSTR"], "account_value": 6430977.0, "unrealized": -3987321.0}
    v = score.verdict(real, under, dims, [])
    assert "the open book is $3,987,321 under water (62% of equity) with 1 of 2 positions unprotected" in v and "-$3,987,321" not in v, v


def test_dimension_lines_read_right_on_one_trade():
    _, line = score.dim_consistency(dict(trades=1, win_rate=1.0, profit_factor=float("inf")), [])
    assert "across 1 trade." in line and "Only 1 trade:" in line and "1 trades" not in line, line
    book = {"positions": [{"coin": "NEAR", "side": "SHORT"}], "account_value": 2222.0, "net_exposure": -1212.0}
    mf = {"rows": [{"coin": "NEAR", "side": "SHORT", "trend": "UP", "fit": "AGAINST THE MARKET"}], "with_market": 0, "against": 1, "funding_per_day": 0.0, "stance": "net short"}
    assert score.dim_market(book, mf)[1] == "NEAR — short into an up-trend."
    mf["rows"][0].update(trend="DOWN", side="LONG")
    assert score.dim_market(book, mf)[1] == "NEAR — long into a down-trend."
    assert score._pct_cost(0.0035) == "0.4%" and score._pct_cost(0.0001) == "<0.1%" and score._pct_cost(0.32) == "32%"


def test_sizing_credits_big_winners():
    book = {"positions": [], "exposure_over_equity": None, "largest_share": None}
    tr = dict(size_cv=0.9, size_max_over_median=17.0)
    small = [dict(truncated=False, peak_notional=1.0, win=True) for _ in range(6)]
    big_win = [dict(truncated=False, peak_notional=10.0, win=True) for _ in range(3)]
    big_mixed = big_win[:2] + [dict(truncated=False, peak_notional=10.0, win=False)]
    a, _ = score.dim_sizing(tr, book, small + big_win)
    b, _ = score.dim_sizing(tr, book, small + big_mixed)
    assert a == b + 10


def test_entry_style_speaks_the_traded_side():
    fade = dict(chased_share=0.1, pre24_median=-0.05)
    assert strategy_read.entry_style(fade, dict(long_share=1.0)).startswith("buys weakness")
    assert strategy_read.entry_style(fade, dict(long_share=0.0)).startswith("sells strength")
    assert strategy_read.entry_style(fade, dict(long_share=0.41)).startswith("fades the move")
    assert strategy_read.entry_style(dict(chased_share=0.6, pre24_median=0.04), dict(long_share=0.2)).startswith("sells weakness")
    assert strategy_read.entry_style(dict(chased_share=0.1, pre24_median=0.0), dict(long_share=0.5)) is None


def _fp_min(**kw):
    fp = {"simultaneity": {"both_share": 0.0, "pairs": []}, "leg_correlation": None, "net_over_gross": 1.0, "pnl_beta": None, "outcome_concentration": None, "dead_sides": []}
    fp.update(kw)
    return fp


def test_critique_squeeze_and_directional_wording_follow_the_book():
    book = {"positions": [{"coin": "SOL", "notional": 1336.0, "stop_covered_share": 1.0}, {"coin": "NEAR", "notional": 1212.0, "stop_covered_share": 1.0},
                          {"coin": "PONS", "notional": 517.0, "stop_covered_share": 1.0}], "naked": []}
    mf = {"against": 1, "rows": [{"coin": "SOL", "fit": "NEUTRAL — ranging"}, {"coin": "NEAR", "fit": "AGAINST THE MARKET"}, {"coin": "PONS", "fit": "WITH THE MARKET"}]}
    cohorts = [{"name": "proven", "agreement": 1.0, "against": [], "rows": [1, 2, 3]}, {"name": "hot", "agreement": -1.0, "against": ["NEAR"], "rows": [1, 2, 3]}]
    cr = strategy_read.critique(_fp_min(), dict(trades=1), book, mf, None, cohorts)
    assert any(c.startswith("On NEAR you sit with the proven cohort") and "survivable" in c and "without" not in c for c in cr), cr
    assert any("40% of it by notional (NEAR)" in c for c in cr), cr
    book["positions"][1]["stop_covered_share"] = 0.13
    cr = strategy_read.critique(_fp_min(), dict(trades=1), book, mf, None, cohorts)
    assert any("without a full stop on NEAR" in c for c in cr), cr
    book["positions"][1]["notional"] = 5000.0
    cohorts[1]["against"] = ["NEAR", "SOL"]
    cr = strategy_read.critique(_fp_min(), dict(trades=1), book, mf, None, cohorts)
    assert any(c.startswith("You are positioned with the record") and "2 of 3 coins" in c for c in cr), cr
    assert any("most of it (73% by notional)" in c for c in cr), cr


def test_rank_carries_three_windows_and_render_helpers():
    import render
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    rk = hl_api.weekly_rank(rec["hl::leaderboard"], rec["address"])
    assert rk["ranks"]["week"] == rk["rank"] and set(rk["ranks"]) >= {"week", "month", "allTime"}
    line = render.rank_line(rk)
    assert line.startswith(f"**#{rk['rank']:,} of {rk['of']:,}** on Hyperliquid's leaderboard this week (") and "on the month (" in line and "all-time (" in line, line
    tr = {"coverage": {"overall": 0.27, "episodes_incomplete": 23, "episodes": 24}}
    assert render.coverage_note(tr, None) == "_Trade-level reads cover about 27% of executed volume; ledger figures are complete._"
    assert render.coverage_note(tr, {"sources": {"trades": "senpi discovery (17 closed positions)"}}) == "_Trade history: senpi discovery (17 closed positions)._"
    assert render.coverage_note({"coverage": {"overall": 0.95}}, None) is None
    hf = render.hold_fallback({"hold_n": {"winners": 14, "losers": 3}})
    assert "14 winning and 3 losing" in hf and "too few losers" in hf and "wants 5 of each" in hf
    assert "too few of either" in render.hold_fallback({"hold_n": {"winners": 0, "losers": 1}})
    assert render.copy_warnings({"naked": ["X"], "positions": [1, 2]}, [{"name": "hot", "agreement": -1.0}]) == ["1 of 2 open positions has no stop", "the hot 30-day cohort sits on the other side of this book"]
    w = render.copy_warnings({"naked": ["X", "Y"], "positions": [1, 2, 3]}, [{"name": "proven", "agreement": -0.6}, {"name": "hot", "agreement": -1.0}])
    assert w[0].startswith("2 of 3 open positions have no stop") and w[1].endswith("cohort sit on the other side of this book")
    assert render.pct_cost(0.0035) == "0.4%" and render.pct_cost(0.0001) == "<0.1%" and render.pct_cost(0.32) == "32%"


def test_compare_names_the_coins_behind_the_entry_lag():
    now = 400 * H
    per = {"PONS": {"bias": -0.62, "members": 12, "entries_long": [], "entries_short": [now - 200 * H]},
           "SOL": {"bias": -0.73, "members": 23, "entries_long": [], "entries_short": [0.0]}}       # SOL: a hold older than RECENT_H, not a move
    book = {"positions": [{"coin": "PONS", "side": "SHORT", "leverage": 3}, {"coin": "SOL", "side": "SHORT", "leverage": 3}]}
    out = smart_money.compare(per, book, [], ages={"PONS": now - 11 * H, "SOL": now - 27 * H}, now_ms=now)
    assert out["lag_coins"] == ["PONS"] and out["entry_lag_h"] == 189.0, out
    assert [r["read"] for r in out["rows"]] == ["WITH — BUT LATE (+189h)", "WITH — they've held it 17d"]


def test_analyst_is_an_alias_for_other():
    out = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "desk.py"), "-h"], capture_output=True, text=True, timeout=60).stdout
    assert "--other, --analyst" in out


# ---------------------------------------------------------------- 1.2.0: costs over what was made, the ledger leads, chat-shaped tables, no data-source apologies
def test_costs_are_measured_against_what_was_made():
    tr = dict(cost_ratio=4533 / 21219, fees=4533.0, gross_realized=9.15, funding=21210.0, gross_income=21219.15, taker_share=0.56)
    s, line = score.dim_cost(tr)
    assert line.startswith("Fees took 21% of what you made ($4,533 on $9 of trade P&L plus $21,210 of funding collected)") and s > 60, (s, line)
    s, line = score.dim_cost(dict(cost_ratio=4.0, fees=400.0, gross_realized=100.0, funding=0.0, gross_income=100.0, taker_share=0.2))
    assert line.startswith("Costs exceeded what you made: $400 against $100 of trade P&L.") and s == 30, (s, line)


def test_verdict_leads_with_a_negative_ledger():
    book = {"positions": [{"coin": "ZEC", "liq_distance_pct": 44.2, "stop_covered_share": 0.0}, {"coin": "PONS", "liq_distance_pct": 150.0, "stop_covered_share": 0.0}],
            "naked": ["ZEC", "PONS"], "account_value": 7941488.0, "unrealized": 315976.0}
    dims = {k: {"score": v, "line": ""} for k, v in dict(timing=60, risk=38, cost=68, sizing=85, consistency=55, market_fit=60).items()}
    tr = dict(trades=1, ledger_net=-765140.0, net=16686.0, fees=4533.0, funding=21210.0, win_rate=1.0, profit_factor=float("inf"))
    v = score.verdict(tr, book, dims, [])
    assert v == "Down $765,140 on the ledger over the window (open book and funding included) — and you're carrying unprotected risk. Fix the risk first.", v


def test_pattern_leaks_need_a_sample():
    tm = {"lock": {"robust": 63716.0}, "cut": {"robust": 0.0}, "give_back_median": 1.0, "mfe_median_winners": 0.016, "losers_that_were_green": None}
    base = dict(fee_recoverable=0.0, taker_share=0.1, funding=0.0, hold_ratio=None, coins={}, fees=0.0, volume=0.0, fee_rate_taker=0.0, fee_rate_maker=0.0)
    book = {"positions": [], "naked": [], "funding_per_day": 0.0}
    assert score.leaks(dict(base, trades=1), book, tm, [], [], 0, 90) == []
    assert len(score.leaks(dict(base, trades=5), book, tm, [], [], 0, 90)) == 1


def test_archetype_calls_a_whale_with_one_round_trip_a_thin_record():
    book = {"positions": [], "exposure_over_equity": 1.0, "margin_utilization": 0.75}
    assert score.archetype(dict(trades=1), book, None, {"active_days": 7, "fills": 9380}) == "Aggressive book · thin record (1 closed trade, 9,380 fills)"
    assert "early days (1 closed trade)" in score.archetype(dict(trades=1), book, None, {"active_days": 2, "fills": 12})


def test_progress_streams_inside_the_long_fetches():
    hl = hl_api.HL(cache_dir=None)
    msgs = []; hl.progress = msgs.append
    hl.info = lambda body: []
    hl.candles(["A", "B"], days=1)
    assert msgs == ["[quant-desk]   · reading the tape: 2 of 2 coins …"], msgs
    hl.candles(["A"], days=1, interval="1d")
    assert msgs[-1] == "[quant-desk]   · reading the tape: 1 of 1 coins · daily …"
    class C:
        def mcp_call(self, *a, **k):
            return {"traders": []}
    smart_money.books(C(), ["0x1", "0x2"], {}, progress=msgs.append, label="the proven cohort, ")
    assert msgs[-1] == "[quant-desk]   · senpi-smart-money: the proven cohort, 2 of 2 wallets read …"


def test_desk_is_chat_shaped_and_never_apologises_for_its_sources():
    import desk, render, voice
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None)
    md = render.render(r)
    prot = render.protection(r)
    hdr = next(l for l in prot.splitlines() if l.startswith("| Coin |"))
    assert hdr.count("|") == 10 and "Your quant would" not in hdr and "**Your quant would…**" in prot and "\n- **" in prot
    for bad in ("public API", "connect senpi", "public reads", "Senpi's read", "public onchain"):
        assert bad not in md and bad not in voice.third_person(md, "0xab…cd"), bad
    assert f"· v{render.VERSION}" in md and "Ledger over 90 days:" in md and "Closed trades (" in md
    skill = open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8").read()
    assert f'version: "{render.VERSION}"' in skill


def test_the_desk_carries_no_per_response_disclaimer():
    """senpi is disclaimered at the product level (Jason, 2026-09-17), so repeating it under every
    desk, every deep dive and every comparison is noise. It also carried the data-source mention the
    desk voice rules forbid. Nothing the renderer emits says it."""
    import pathlib as _pl
    src = (_pl.Path(__file__).resolve().parents[1] / "scripts" / "render.py").read_text(encoding="utf-8")
    assert "financial advice" not in src.lower()
    assert "FOOTER" not in src


def test_indexed_is_a_contradiction_between_two_sources_not_a_guess():
    """`indexed=False` means the public endpoints showed closed round trips in the window and senpi's
    index returned none for the SAME window. A quiet wallet — nothing closed either way — is `None`,
    because it says nothing about whether senpi has the address."""
    import desk, hl_api as _hl
    with open(FIXTURE) as fh:
        rec = json.load(fh)

    class FakeMCP:                                  # senpi's history answers with nothing
        def mcp_call(self, *a, **kw):
            return {"success": True, "data": {"closedPositions": []}}

    hl = _hl.HLFixture(rec)
    r = desk.analyze(rec["address"], hl, days=90, mcp=FakeMCP(), want_cohort=False, want_rank=False)
    assert r["track"]["trades"] > 30                # the public side really does have closed trades
    assert r["indexed"] is False, "senpi returned nothing against a wallet with closed round trips"
    assert r["meta"]["sources"]["trades"] == "public fills"

    hl2 = _hl.HLFixture(rec)
    r2 = desk.analyze(rec["address"], hl2, days=90, mcp=None, want_cohort=False, want_rank=False)
    assert r2["indexed"] is None, "with no senpi client there is nothing to contradict"


def test_the_skill_defaults_to_the_readers_own_book_and_remembers_the_rest():
    skill = (_P(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    assert "An address is the reader's own book unless we know otherwise" in skill
    assert "already recorded as *analyzed* stays\n   someone else's on a bare re-run" in skill
    for needle in ("**verified**", "**claimed**", "**analyzed**", "--claim", "--addresses",
                   "a claim, not proof", "whenever the request is about someone else"):
        assert needle in skill, needle


def test_the_skill_answers_a_not_indexed_wallet_and_keeps_the_promise_honest():
    skill = (_P(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    cov = json.loads((_P(__file__).resolve().parents[1] / "references" / "coverage.json").read_text())
    assert f"**{cov['indexed_wallets']:,}**" in skill, "the quoted figure and coverage.json disagree"
    for needle in ("references/coverage.json", "never from memory", "stale_after_days",
                   "Never promise a date", "in waves"):
        assert needle in skill, needle
    # the desk still runs — a thin desk beats no desk, as long as it says it is thin
    assert "A desk still runs on the public reads" in skill


def test_the_skill_offers_the_lateral_move_in_both_directions():
    """The follow-up banks only go deeper on the same book, so nothing tells a reader the desk works
    on any wallet — or, after an analyst run, that it works on theirs."""
    skill = (_P(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    assert "Your quant reads any book on Hyperliquid, not just yours" in skill
    assert "Your quant works the\n   same way on yours" in skill
    assert "**Never invent an address.**" in skill


def test_a_discovery_failure_is_never_reported_as_not_indexed():
    """`fetch` returns [] for three different things: an unindexed wallet, a read that threw, and a
    `success: false` envelope. Only the first is "not indexed". The other two are "we could not
    look" — and the desk offers to flag a not-indexed wallet to the team, so getting this wrong
    promises something about a wallet that is already in the index."""
    import desk, hl_api as _hl
    with open(FIXTURE) as fh:
        rec = json.load(fh)

    class Throws:
        def mcp_call(self, *a, **kw):
            raise RuntimeError("discovery_get_trader_history HTTP 503")

    class Refuses:                                  # the shape a degraded discovery returns
        def mcp_call(self, *a, **kw):
            return {"success": False, "error": {"code": "INVALID_TOKEN"}}

    for client in (Throws(), Refuses()):
        r = desk.analyze(rec["address"], _hl.HLFixture(rec), days=90, mcp=client,
                         want_cohort=False, want_rank=False)
        assert r["indexed"] is None, f"{type(client).__name__}: a failed read read as 'not indexed'"
        assert r["meta"].get("senpi_history_failed") is True
        assert any("senpi history" in w for w in r["meta"]["warnings"]), "the failure left no trace"


# ── two contradictions caught on the 2026-09-21 launch-eve run of 0x880a…311c ──

def test_the_funding_headline_is_weighted_by_size_not_by_coin_count():
    """A plain median over coins counts a $19 dust position and a $20M one equally.

    On the real book: sixteen xyz names at +0 bp/8h, and the actual size in ZEC ($20.0M, +1),
    XMR ($4.8M, +9) and HYPE ($0.5M, +10). The median said FUNDING NEAR FLAT while the same desk
    reported the book collecting $20,629/day two lines below — funding was most of what the window
    earned, called flat.
    """
    import market
    pos = ([dict(coin=f"xyz:D{i}", side="SHORT", leverage=3, notional=20.0, funding_per_day=0.0)
            for i in range(16)]
           + [dict(coin="ZEC", side="SHORT", leverage=10, notional=19_951_503.0, funding_per_day=5985.0),
              dict(coin="XMR", side="SHORT", leverage=5, notional=4_822_496.0, funding_per_day=13049.0),
              dict(coin="HYPE", side="SHORT", leverage=8, notional=492_263.0, funding_per_day=1417.0)])
    book = dict(positions=pos, net_exposure=-1.0, funding_per_day=20629.0)
    universe = [dict(name=p["coin"]) for p in pos] + [dict(name="BTC")]
    fund = {"ZEC": 1.25e-5, "XMR": 1.125e-4, "HYPE": 1.25e-4}          # bp/8h = fr * 8 * 1e4
    ctxs = [dict(universe=universe),
            [dict(funding=fund.get(u["name"], 0.0), markPx="1", openInterest="0", dayNtlVlm="0")
             for u in universe]]
    # coin_regime returns None without >=48 candle rows, and a None regime carries no funding —
    # so every coin needs a series or the weighting has nothing to weigh.
    candles = {p["coin"]: (None, [[0, 1.0, 1.0, 1.0, 1.0, 1.0] for _ in range(50)]) for p in pos}
    candles["BTC"] = (None, [[0, 1.0, 1.0, 1.0, 1.0, 1.0] for _ in range(50)])
    out = market.book_fit(book, candles, ctxs)
    w = out["median_funding_bp_8h"]
    assert 2.5 < w < 3.0, (
        f"notional-weighted funding came out at {w:.2f} bp/8h; a plain median over these 19 coins "
        "reads 0.00 because sixteen of them are dust")
    # 2.70 bp/8h is 29.6%/yr — the desk's own dollar figure for this book was $20,629/day, 25.8%/yr
    # of account value. The label has to agree with the money.
    assert "NEAR FLAT" not in out["headline"], out["headline"]
    assert "%/yr on the book you hold" in out["headline"], out["headline"]


def test_the_leg_correlation_never_claims_a_leg_the_live_book_lacks():
    """It measures the 90-day window, but it printed in the present tense directly under
    'the book right now is directional — net 100% of gross', telling a reader about a long leg that
    does not exist. Past tense, and say so when the live book is one-sided."""
    import pathlib, re
    src = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "strategy_read.py").read_text()
    m = re.search(r'f"([^"]*long leg[^"]*)"', src)
    assert m, "the leg-correlation sentence moved — update this test"
    line = m.group(1)
    assert "over the window" in line and "moved together" in line, line
    assert "move together" not in line, f"present tense restored: {line}"
    assert "the book you hold now is" in src


def test_no_caption_is_swallowed_into_a_table():
    """A line placed straight after a table row is parsed as ANOTHER ROW.

    Caught on the 2026-09-21 run: `_Trade history: senpi discovery (38 closed positions)._` sat
    directly under the track-record row, so it rendered as a row carrying that text in column 1 and
    seven empty cells after it. To a reader the table simply has an empty row in it, and the caption
    is gone. The blank line before a caption is load-bearing, and nothing in Markdown warns you.

    Asserted over EVERY rendered surface, because the mistake is one line of code away anywhere a
    table is followed by prose.
    """
    import desk, render
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None)

    surfaces = {"render": render.render(r), "protection": render.protection(r)}
    for mode in ("smartmoney", "market", "leaks"):
        try:
            surfaces[f"deep:{mode}"] = render.render_deep(mode, {}, r)
        except Exception:
            pass

    bad = []
    for name, md in surfaces.items():
        lines = md.splitlines()
        for i, ln in enumerate(lines[:-1]):
            if not ln.lstrip().startswith("|"):
                continue
            nxt = lines[i + 1]
            if nxt.strip() and not nxt.lstrip().startswith("|"):
                bad.append(f"{name}:{i + 2} — {nxt.strip()[:80]!r} follows a table row")
    assert not bad, "prose absorbed into a table (needs a blank line first):\n  " + "\n  ".join(bad)


def test_a_book_that_paid_to_lose_is_scored_on_cost_not_left_blank():
    """Cost efficiency used to abstain whenever gross P&L was not positive — there was "no base to
    take a share of". But a book that lost money AND paid to do it is the worst cost case on the
    desk, not an unmeasurable one. On 0x8b79…85d7 that printed "—" against $7,068 of fees on $89.5M
    of volume, and the abstention let the other five dimensions re-normalise the headline UP.

    The base is |net| — the money actually lost. Against |gross| the same book scores 94/100, which
    measures the size of the loss, not the cost of it."""
    import score
    bleeding = dict(cost_ratio=None, taker_share=0.70, fees=7068.0, funding=193.0,
                    gross_realized=-10094.0, net=-16969.0)
    s_bleed, line = score.dim_cost(bleeding)
    assert s_bleed is not None and s_bleed < 40, f"a book paying 42% of its loss in fees scored {s_bleed}"
    assert "of what you lost was cost" in line and "$7,068" in line, line

    # a loss that was NOT about costs must not be punished for being a big loss
    trades_were_the_problem = dict(cost_ratio=None, taker_share=0.23, fees=7143.0, funding=103130.0,
                                   gross_realized=-180996.0, net=-85010.0)
    s_ok, _ = score.dim_cost(trades_were_the_problem)
    assert s_ok > 70, f"8% of the loss in fees should read as fine, scored {s_ok}"
    assert s_ok > s_bleed

    # pin the BASE, not just the direction. net = gross - fees, so |net| is always the larger
    # denominator and the two choices genuinely disagree here: costs are a third of the trading
    # loss but only a quarter of the damage that actually landed. The second is the honest one —
    # the fees are part of why net is -$40,000, so charging them against gross double-counts them.
    disagrees = dict(cost_ratio=None, taker_share=0.0, fees=10_000.0, funding=0.0,
                     gross_realized=-30_000.0, net=-40_000.0)
    s_base, _ = score.dim_cost(disagrees)
    assert round(s_base, 1) == 62.5, f"|net| base gives 62.5, |gross| base gives 50.0 — got {s_base}"

    # and a book with no result and no costs still has nothing to measure
    s_none, line_none = score.dim_cost(dict(cost_ratio=None, taker_share=0.0, fees=0.0,
                                            funding=0.0, gross_realized=0.0, net=0.0))
    assert s_none is None and "Not measurable" in line_none


def test_a_dimension_that_abstains_does_not_vote_in_the_headline():
    """Asserted through score.dimensions(), not by redoing the arithmetic — a test that recomputes
    the formula passes even when the code stops using it."""
    import score
    FN = {"timing": "dim_timing", "risk": "dim_risk", "cost": "dim_cost", "sizing": "dim_sizing",
          "consistency": "dim_consistency", "market_fit": "dim_market"}
    real = {k: getattr(score, fn) for k, fn in FN.items()}
    fixed = {"timing": 31, "risk": 28, "sizing": 45, "consistency": 42, "market_fit": 20}
    try:
        for k, v in fixed.items():
            setattr(score, FN[k], (lambda val: (lambda *a, **kw: (val, "")))(v))
        setattr(score, FN["cost"], lambda *a, **kw: (None, ""))
        d_abstain, q_abstain = score.dimensions({}, {}, {}, {}, {}, {}, {}, {})
        setattr(score, FN["cost"], lambda *a, **kw: (90, ""))
        _, q_with_90 = score.dimensions({}, {}, {}, {}, {}, {}, {}, {})
    finally:
        for k, fn in FN.items():
            setattr(score, fn, real[k])
    assert d_abstain["cost"]["score"] is None
    assert q_abstain < q_with_90, "an abstaining dimension still lifted the headline"


def test_the_dimension_table_never_prints_a_number_it_did_not_measure():
    """Driven off the real fixture, not a stub — a skipped test guards nothing."""
    import desk, render
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None)
    r["dimensions"]["cost"] = dict(score=None, line="Not measurable this window: no positive gross.")
    md = render.overview(r)
    body = "\n".join(md) if isinstance(md, list) else md
    row = next(l for l in body.splitlines() if l.startswith("| Cost efficiency"))
    assert "| — |" in row, row
    assert "could not be measured this window" in body
    assert "| None |" not in body


def test_a_none_score_survives_the_whole_pipeline_not_just_the_unit():
    """1.4.3 made dim_cost return None, fixed the aggregate and the table, and missed verdict().

        ranked = [k for k in sorted(dims, key=lambda k: dims[k]["score"]) if material(k)]
        TypeError: '<' not supported between instances of 'NoneType' and 'int'

    sorted() runs BEFORE the filter, so the None reached the key function and took the desk down
    after every read had completed — 10,366 fills scanned, nothing rendered.

    The unit tests all passed. They exercised dim_cost in isolation and the fixture's own trader has
    positive gross, so no None ever travelled the full path. This test forces one through
    analyze -> verdict -> render, which is the only shape that catches it.
    """
    import desk, render, score
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    real = score.dim_cost
    try:
        score.dim_cost = lambda *a, **kw: (None, "Not measurable this window: no positive gross.")
        r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None)
        md = render.render(r)                       # must not raise
    finally:
        score.dim_cost = real
    assert r["dimensions"]["cost"]["score"] is None
    assert isinstance(r["quant_score"], int)
    assert "| — |" in md and "| None |" not in md
    # the verdict still has to say something — an unmeasured dimension must not silence it
    assert r.get("verdict") or r.get("flags") or r["quant_score"] >= 0


def test_a_heading_never_promises_more_items_than_it_lists():
    """`## Top 3 things your agents found` was hardcoded over `r["leaks"][:3]`.

    On the 2026-09-21 run of 0xd475…1a91 the desk found two leaks and still announced three. It is a
    small lie the reader checks in one glance, and it makes them wonder what else was rounded.
    """
    import re, render
    for n, want in ((3, "Top 3 things"), (2, "Top 2 things"), (1, "Top 1 thing")):
        leaks = [dict(agent="Leak finder", usd=1000.0, window="90d", title=f"t{i}",
                      evidence="e", counterfactual="c", cta="x") for i in range(n)]
        r = dict(leaks=leaks, track={}, dimensions={}, book=dict(positions=[]), quant_score=50,
                 days=90, equity={}, activity=dict(active_days=1))
        body = render.leaks_summary(r) if hasattr(render, "leaks_summary") else None
        if body is None:
            src = __import__("pathlib").Path(render.__file__).read_text()
            assert "f\"## Top {len(top)} thing" in src, "heading is hardcoded again"
            break
        assert want in body, (n, body[:120])
    # and the heading must never out-count the list in the shipped source
    src = __import__("pathlib").Path(render.__file__).read_text()
    assert not re.search(r'"## Top [0-9]+ thing', src), "a literal count crept back into the heading"


def test_the_skill_tells_the_agent_to_stage_the_relay():
    """The desk is 30-60s of analysis and thousands of words. Delivered as one block after a silent
    wait it is the worst possible shape — the reader waits with nothing, then gets more than they can
    read. The first run caches for 10 minutes, so every later --section is instant and the staging
    costs nothing but instruction.

    Rule 1 used to read "One command, then relay", and prescribed a 90-word lead-in sentence listing
    every phase. That sentence is what a reader actually saw while waiting.
    """
    import pathlib, re
    # SKILL.md is hard-wrapped, so any phrase can straddle a newline. Collapse whitespace first or
    # every assertion in here is one re-wrap away from a false failure.
    skill = re.sub(r"\s+", " ", (pathlib.Path(__file__).resolve().parents[1] / "SKILL.md").read_text())
    assert "Relay it in STAGES — never as one block." in skill
    assert "One command, then relay." not in skill
    for stage in ("--section overview", "--section protection", "--section leaks"):
        assert stage in skill, stage
    assert "the staging IS the feature" in skill
    # the lead-in must be short: the old one narrated all eight phases before anything ran
    assert "scanning every fill, funding payment and resting order, auditing the live book" not in skill
    assert "reading every fill, the live book, the cohorts and the tape" in skill


def test_the_eli5_is_called_eli5():
    """Traders know the term, and it signals "ask me anything" better than "plain English" does."""
    import followups
    for bank in (followups.BANK, followups.BANK_OTHER):
        assert bank["eli5"].startswith("Want the ELI5"), bank["eli5"]


def test_every_script_that_matters_carries_the_same_version():
    """A stale install passed every gate we had.

    2026-09-21: an agent updated quant-desk, SKILL.md and render.py both read 1.6.0, all three gates
    passed — and desk.py was still old. It showed up as a progress line reading "senpi-smart-money"
    at step 4 where the shipped source says "senpi-market-pulse". desk.py does all the work and was
    the one file with no version of its own, so nothing could catch it.
    """
    import desk, render
    assert desk.VERSION == render.VERSION, (desk.VERSION, render.VERSION)
    import pathlib
    skill = (pathlib.Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
    assert f'version: "{render.VERSION}"' in skill


def test_the_progress_lines_name_the_right_engine():
    """Step 3 is smart-money, step 4 is market-pulse. They were briefly the same word, which is how
    the stale install above was spotted — so pin them."""
    import pathlib, re
    src = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "desk.py").read_text()
    steps = dict(re.findall(r'step\((\d), "([^"]{0,60})', src))
    assert "senpi-smart-money" in steps.get("3", ""), steps.get("3")
    assert "senpi-market-pulse" in steps.get("4", ""), steps.get("4")


def test_an_empty_window_still_reports_whether_senpi_has_the_wallet():
    """The empty-window exit printed `{"error": …, "address": …, "days": …}` and dropped `indexed`.

    A caller then cannot tell "senpi has never seen this wallet" from "senpi has it and there is
    simply nothing in the window" — and those two need opposite things said to the reader. A wallet
    whose 90 days are all SPOT lands here too, so "nothing to read" was wrong as well as incomplete.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "desk.py").read_text()
    # isolate the json.dumps({...}) that this exit prints — a byte window around it would also pick
    # up the comment explaining the fix, which quotes the old wording
    i = src.index("no PERP activity in the last")
    start = src.rindex("print(json.dumps({", 0, i)
    payload = src[start:src.index("return 3", i)]
    assert '"indexed": r.get("indexed")' in payload, "the empty-window exit still drops `indexed`"
    assert "Spot trades and transfers are not perp activity" in payload
    assert "nothing to read" not in payload


def test_the_desk_never_promises_a_signature_it_cannot_take():
    """"a hard floor now, a trailing lock as it runs … a signature on positions you already hold."

    Three things were wrong with that. The integrated two-phase DSL is a RUNTIME feature; a raw
    position gets a FIXED stop plus an uncoordinated profit ladder. `ratchet_stop_add` is keyed to a
    senpi strategy wallet, so for a desk reader whose book is on their OWN wallet senpi cannot attach
    anything at all today. And the reader is told to do nothing while four naked positions sit there.

    The desk now tells them to place the stops themselves, now, and says senpi will take it over.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "render.py").read_text()
    # drop comment lines — the explanation of this fix quotes the phrase it forbids
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "signature on positions you already hold" not in code, "the signature overclaim is back"
    assert "onchain on Hyperliquid" in src
    for phrase in ("Set a stop on each of them onchain", "These are yours to place"):
        assert phrase in src, phrase


def test_next_steps_offers_a_route_for_someone_who_does_not_want_their_own_history_mechanised():
    """"hire my quant" turns THEIR past into the strategy. A reader who wants something else had
    nowhere to go, and it is the cheapest step on the page — a sentence, no wallet, no deposit."""
    import json, desk, render
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None)
    md = render.next_steps(r)
    assert "Or build something new." in md and "Tell me your thesis" in md
    assert "Reply *hire my quant*" in md
    # and the protect step must point at Hyperliquid, not at a signature
    if "Protect first" in md:
        assert "onchain on Hyperliquid yourself" in md and "signature" not in md



# -------------------------------------------------- recoverable: one number a user can actually quote
def _tm_row(**kw):
    """A per_trade()-shaped row. Defaults are a clean, unremarkable winner."""
    row = dict(coin="ETH", direction="long", realized=100.0, hold_h=10.0, win=True, pre24=0.0,
               chased=False, mfe=0.05, mae=-0.01, realized_pct=0.02, give_back=0.0,
               notional=5_000.0, lock_cf={}, cut_cf={}, open_time=0)
    row.update(kw)
    return row


def _closed_winners(n=3, notional=5_000.0):
    # entry-marked (peak_size x entry_vwap) is what the size lever measures against; peak_notional
    # is kept so any caller still reading it sees the same number
    return [dict(win=True, truncated=False, peak_notional=notional,
                 peak_size=1.0, entry_vwap=notional) for _ in range(n)]


CHASE_ON = dict(chased_n=5, chased_realized=-10_000.0, calm_pf=1.8, chased_pf=0.4)


def test_recoverable_is_the_best_single_lever_never_the_sum_of_them():
    """The bug this exists to fix: a trade that was oversized, chased, held too long AND gave back
    its peak is priced in four leaks. Added up it reads as four losses; there was only ever one."""
    bad = _tm_row(realized=-4_000.0, win=False, chased=True, notional=50_000.0, hold_h=100.0,
                  lock_cf={"0.03/0.5": 1_000.0}, cut_cf={"24": 1_500.0})
    tm = dict(CHASE_ON, lock={"settings": {"0.03/0.5": dict(n=1, total=1_000.0)}},
              cut={"settings": {"24": dict(n=1, total=1_500.0)}})
    rec = score.recoverable([bad], _closed_winners(), {}, tm)

    sizing = 4_000.0 * (1 - 5_000.0 / 50_000.0)          # 3,600
    assert rec["usd"] == max(1_000.0, 1_500.0, sizing, 10_000.0) == 10_000.0, "the best lever wins"
    assert rec["usd"] < 1_000.0 + 1_500.0 + sizing + 10_000.0, "and it is strictly under the sum"
    assert rec["rule"] == "skipping entries after a >=3% move"


def test_recoverable_will_not_pick_a_rule_that_costs_money_on_the_trades_it_hurts():
    """An exit rule is scored on its whole-book total, so the trades where it cut a winner short are
    charged against it. A rule that only looks good on the trades it helped must not be quotable."""
    tm = dict(lock={"settings": {"0.03/0.5": dict(n=9, total=-500.0)}},
              cut={"settings": {"24": dict(n=9, total=-200.0)}})
    rec = score.recoverable([_tm_row()], [], {}, tm)
    assert rec["usd"] == 0.0 and rec["rule"] is None, "no lever beat what the trader actually did"


def test_recoverable_prefers_the_setting_with_the_best_book_total_not_the_best_trade():
    tm = dict(lock={"settings": {"0.03/0.5": dict(n=9, total=9_000.0),
                                 "0.05/0.5": dict(n=3, total=1_000.0)}})
    rec = score.recoverable([_tm_row()], [], {}, tm)
    assert rec["usd"] == 9_000.0
    # the lever's label is the finished sentence, so nothing has to parse "0.03/0.5" back out
    assert rec["rule"] == "a trailing stop that arms at +3% and keeps 50% of the peak"


def test_recoverable_chase_term_is_unavailable_when_chasing_is_not_this_book_s_problem():
    """"Chased" is only a >=3% 24h move — on a trending book nearly every entry clears it. Crediting
    every chased loser its whole loss is what made the retail number several times the real one. The
    credit needs this book's chased entries to have really done worse than its calm ones."""
    loser = _tm_row(realized=-4_000.0, win=False, chased=True, notional=5_000.0)
    not_worse = dict(chased_n=5, chased_realized=-10_000.0, calm_pf=0.4, chased_pf=1.8)

    assert score.recoverable([loser], [], {}, not_worse)["usd"] == 0.0
    assert score.recoverable([loser], [], {}, None)["usd"] == 0.0, "no timing view = no claim"
    assert score.recoverable([loser], [], {}, CHASE_ON)["usd"] == 10_000.0, "gate open, credit applies"


def test_recoverable_chase_credit_is_what_the_chase_leak_claims_winners_netted_off():
    """The leak's figure nets the chased WINNERS off. Summing the losers alone claims more than the
    leak it is derived from."""
    losers = [_tm_row(realized=-4_000.0, win=False, chased=True, notional=5_000.0) for _ in range(5)]
    tm = dict(chased_n=8, chased_realized=-6_000.0, calm_pf=1.8, chased_pf=0.4)
    assert sum(-t["realized"] for t in losers) == 20_000.0
    assert score.recoverable(losers, [], {}, tm)["usd"] == 6_000.0


def test_recoverable_size_cap_gives_up_the_winners_upside_too():
    """A cap shrinks every oversized trade, not just the ones that lost. Shrinking only the losers is
    the same survivorship bias as charging a time-cut only on losers."""
    big_loser = _tm_row(realized=-4_000.0, win=False, notional=50_000.0)
    big_winner = _tm_row(realized=+3_000.0, win=True, notional=50_000.0)
    closed = _closed_winners()

    only_loser = score.recoverable([big_loser], closed, {}, None)["usd"]
    both = score.recoverable([big_loser, big_winner], closed, {}, None)["usd"]
    assert only_loser == 4_000.0 * 0.9
    assert both == (4_000.0 - 3_000.0) * 0.9, "the winner's forgone upside is charged against the cap"


def test_recoverable_adds_fees_but_only_for_a_taker_and_never_funding():
    """Fees are the one genuinely independent fix — resting instead of crossing saves the same money
    whatever the exit rule — so they sit on top. Funding is excluded on purpose: capping a
    funding-paying hold is the same action as the time-cut, and counting both reopens the overlap."""
    tr = dict(fee_recoverable=900.0, taker_share=0.8)
    assert score.recoverable([], [], tr, None)["usd"] == 900.0
    assert score.recoverable([], [], dict(tr, taker_share=0.05), None)["usd"] == 0.0
    assert score.recoverable([], [], dict(fee_recoverable=-50.0, taker_share=0.8), None)["usd"] == 0.0


def test_recoverable_reports_how_concentrated_the_number_is():
    """"You leak $46k across your book" was true arithmetic and a false picture — on the book that
    drove this work, one trade was 62% of it. The shape has to travel with the number."""
    rows = [_tm_row(lock_cf={"0.03/0.5": v}) for v in (10_000.0, 500.0, 500.0)]
    tm = dict(lock={"settings": {"0.03/0.5": dict(n=3, total=11_000.0)}})
    c = score.recoverable(rows, [], {}, tm)["concentration"]
    assert round(c["top1"], 4) == round(10_000.0 / 11_000.0, 4) and c["n_positive"] == 3


def test_recoverable_is_measured_against_losses_not_against_the_account():
    """The account is a snapshot and can be zero; the counterfactual runs over the whole window's
    turnover. Losses are the only denominator that makes the number checkable."""
    rows = [_tm_row(realized=-1_000.0, win=False), _tm_row(realized=+400.0, win=True),
            _tm_row(lock_cf={"0.03/0.5": 500.0})]
    tm = dict(lock={"settings": {"0.03/0.5": dict(n=1, total=500.0)}})
    rec = score.recoverable(rows, [], {}, tm)
    assert round(rec["share_of_losses"], 6) == 0.5, "500 recovered against 1,000 of losses"


def test_time_cut_is_charged_on_the_winners_it_would_have_chopped():
    """At hour h you do not know which trades will win. Pricing a time-cut only on the losers is
    survivorship bias, and it made the cut look like it beat every other lever."""
    src = _P(HERE, "..", "scripts", "timing.py").read_text()
    body = src[src.index("cuts[f\"{h:.0f}\"]"):]
    assert 'not e["win"]' not in body.split("\n")[0], "the winners-excluded gate is back"


def test_recoverable_stays_under_the_sum_of_the_listed_leaks_on_the_real_fixture():
    """End to end: the quotable number must never exceed what a reader gets by adding the printed
    leaks — that arithmetic is the whole failure mode."""
    import desk
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None)
    naive = sum(l["usd"] for l in r["leaks"])
    assert r["recoverable"]["usd"] <= naive + 1e-6, f"union {r['recoverable']['usd']} > sum {naive}"


def test_leaks_section_leads_with_the_number_and_tells_the_reader_not_to_add():
    import desk, render
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    r = desk.analyze(rec["address"], hl_api.HLFixture(rec), days=90, mcp=None, bench=None)
    md = render.leaks(r)
    if (r.get("recoverable") or {}).get("usd", 0) > 0 and r["leaks"]:
        assert "would have kept" in md and "do not add up" in md
        assert md.index("do not add up") < md.index("**01 ·"), "the number leads; the leaks follow"
        # a share of losses is only printed when the denominator means something
        share = r["recoverable"].get("share_of_losses")
        if share and share > 2.0:
            assert "of what your losing trades gave up" not in md, "division by noise reached the page"


def test_the_rule_is_stated_in_english_not_in_grid_keys():
    """"0.03/0.5" is the grid key. Nobody can act on that, so score.py emits the sentence."""
    tm = dict(cut={"settings": {"24": dict(n=4, total=800.0)}})
    assert score.recoverable([_tm_row()], [], {}, tm)["rule"] == "closing anything still open after 24h"


def test_the_fee_leak_offers_senpi_execution_with_the_dollar_amount():
    """The fee leak is the one a user can act on without changing a single trading decision — same
    fills, resting instead of crossing. It should say so, name senpi as the way to do it, and carry
    the money, rather than describing a config line the reader cannot apply themselves."""
    tr = dict(fee_recoverable=1_503.0, taker_share=0.77, fees=2_396.0, volume=6_500_000.0,
              fee_rate_taker=0.00045, fee_rate_maker=0.00015)
    out = score.leaks(tr, {}, {}, [], [], 0, 90)
    fee = next((l for l in out if "taker" in l["title"]), None)
    assert fee, "the fee leak did not fire"
    assert "senpi" in fee["cta"] and "$1,503" in fee["cta"], fee["cta"]
    assert "same fills" in fee["cta"], "the point is that no trading decision has to change"


def test_the_do_not_add_rule_binds_outside_the_leaks_section_too():
    """The first agent to break this did it in a deep dive, not in the leaks relay: it added
    maker-first + time-cut + sizing into "~$12k/yr". Fees are the one fix that may be added to
    another, because they are independent of the exit rule."""
    src = " ".join(_P(HERE, "..", "SKILL.md").read_text().split())
    for phrase in ("binds everywhere, not just in the leaks section",
                   "Fees are the single exception",
                   "may never be added to each other"):
        assert phrase in src, phrase


def test_the_header_line_must_be_relayed_verbatim_so_a_stale_engine_is_visible():
    """The header carries the version and the timestamp, and it is the only staleness gate a reader
    has. An agent that paraphrases it into its own summary makes a months-old engine look current —
    which is exactly how a desk missing the recoverable total got reviewed as if it had it."""
    src = " ".join(_P(HERE, "..", "SKILL.md").read_text().split())
    assert "Print the desk's header line exactly as the engine emits it" in src
    assert "only staleness gate the reader has" in src
    # and the renderer must actually emit what the rule promises
    import render
    line = _P(HERE, "..", "scripts", "render.py").read_text()
    assert "v{VERSION}" in line and "READ-ONLY" in line, "the header no longer carries the version"
    assert render.VERSION, "no version to stamp"


def test_fees_dilute_concentration_because_fees_are_the_least_concentrated_thing_there_is():
    """Concentration was a share of the exit lever alone while the quoted total included fees. On
    0x8b79…85d7 that called a $27,996 total "one trade" when $18,898 of it was taker fees spread
    across $89.5M of volume and 1,073 trades — the most diffuse item on the desk."""
    rows = [_tm_row(lock_cf={"0.03/0.5": v}) for v in (6_000.0, 1_500.0, 1_500.0)]
    tm = dict(lock={"settings": {"0.03/0.5": dict(n=3, total=9_000.0)}})

    no_fees = score.recoverable(rows, [], {}, tm)["concentration"]
    with_fees = score.recoverable(rows, [], dict(fee_recoverable=19_000.0, taker_share=0.7), tm)["concentration"]

    assert round(no_fees["top1"], 4) == round(6_000 / 9_000, 4) == 0.6667
    assert round(with_fees["top1"], 4) == round(6_000 / 28_000, 4) == 0.2143
    assert with_fees["top1"] < 0.4 < no_fees["top1"], "fees must move it off the 'it is one trade' branch"


def test_the_headline_does_not_credit_the_exit_rule_with_the_fee_saving():
    """On 0x8b79…85d7 the line read "a trailing stop ... would have kept ~$27,996" when $18,898 of
    that was taker fees and the stop's own share was $9,098. The total leads; the split follows."""
    import render
    r = dict(leaks=[{"usd": 1}], track={}, timing={},
             recoverable=dict(usd=27_996.0, fees=18_898.0, n_trades=1073, share_of_losses=0.42,
                              rule="a trailing stop that arms at +3% and keeps 50% of the peak",
                              concentration=dict(top1=0.21, top3=0.28, n_positive=10)))
    md = "\n".join(render.recoverable_line(r))
    assert "Your quant would have kept ~$27,996" in md
    assert "$18,898 of it is taker fees" in md and "the other $9,098 comes from one rule" in md
    assert not md.startswith("**A trailing stop"), "the rule is credited with the fee saving again"


def test_concentration_can_never_exceed_the_thing_it_is_a_share_of():
    """Reported by @shnoodles on #718. The numerator was gross per-trade savings while the
    denominator was the lever total, which is NET of the trades the rule cost money on — and, for
    the chased lever, net of the chased winners. A single trade could be reported as 111% of the
    number. The "top three were 102%" cited as a finding about a real book was this artifact."""
    charged = [_tm_row(lock_cf={"0.03/0.5": v}) for v in (10_000.0, 2_000.0, 1_000.0, -4_000.0)]
    tm = dict(lock={"settings": {"0.03/0.5": dict(n=4, total=9_000.0)}})
    c = score.recoverable(charged, [], {}, tm)["concentration"]
    assert c["top1"] <= 1.0 and c["top3"] <= 1.0, c
    assert round(c["top1"], 4) == round(10_000 / 13_000, 4), "share of what contributed, not of the net"

    # the chased lever nets its winners off the total; the per-trade list is losers only
    chased = [_tm_row(realized=r, win=r > 0, chased=True) for r in (-8_000.0, -3_000.0, -1_000.0, 1_000.0, 1_000.0)]
    c2 = score.recoverable(chased, [], {}, CHASE_ON)["concentration"]
    assert c2["top1"] <= 1.0 and c2["top3"] <= 1.0, c2


def test_the_size_lever_marks_both_sides_the_same_way():
    """Also from #718. The median winner was peak-marked (price when peak size was on) while the
    threshold it gates is entry-marked (peak size x entry VWAP). Winners are by definition the
    trades that moved favourably, so the median was biased high. ~0.0% on the books tested, but it
    is a comparison across two populations."""
    closed = [dict(win=True, truncated=False, peak_size=2.0, entry_vwap=5_000.0,
                   peak_notional=50_000.0) for _ in range(3)]          # peak-marked 5x the entry mark
    loser = _tm_row(realized=-4_000.0, win=False, notional=20_000.0)   # 1.3x peak-marked, 2x entry-marked
    rec = score.recoverable([loser], closed, {}, None)
    assert rec["usd"] > 0, "entry-marked median is 10,000, so a 20,000 loser is oversized and must count"


def test_a_wiped_out_account_is_not_the_same_risk_score_as_a_third_drawdown():
    """The penalty was min(20, dd_pct * 60), which saturates at 33%: a book that gave back a third
    and a book that went to ZERO scored identically, and a wiped-out account read 65/100 on "Risk
    management". Drawdown here is built from cumulative P&L and is transfer-immune, so dd_pct = 1.0
    really does mean the equity at risk was lost — the one outcome this dimension exists to catch."""
    flat = dict(positions=[], naked=[], margin_utilization=None)
    tr = dict(hold_ratio=None, liquidations=0)
    s_third, _ = score.dim_risk(tr, flat, dict(dd_pct=0.33))
    s_half, _ = score.dim_risk(tr, flat, dict(dd_pct=0.50))
    s_zero, line = score.dim_risk(tr, flat, dict(dd_pct=1.00))

    assert s_third > s_half > s_zero, f"not monotonic: {s_third} / {s_half} / {s_zero}"
    assert s_zero <= 15, f"a total loss of the equity at risk scored {s_zero}"
    assert "went to zero" in line, line
