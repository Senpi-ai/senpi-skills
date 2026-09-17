#!/usr/bin/env python3
"""Tests for sweep.py — the one-process gather, on a fake call_tool returning the documented shapes.

The fixture mirrors the MCP response envelopes (`{"success": true, "data": {...}}`) with the field
names pinned from the MCP source types (market.tools.ts / leaderboard.types.ts / senpi.types.ts).
No real wallet addresses. Run: python3 -m pytest senpi-signals/scripts -q
"""
import json
import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import sweep  # noqa: E402

NOW = "2026-09-15T12:00:00+00:00"
LATER = "2026-09-15T13:10:00+00:00"       # 70 min later → the first run is the ~1h baseline
_W = ["0x%040x" % i for i in range(1, 21)]  # 20 synthetic wallets


def _inst(name, vol, oi, px, prev, funding, delisted=False):
    return {"name": name, "sz_decimals": 3, "max_leverage": 20, "is_delisted": delisted,
            "only_isolated": False, "margin_modes": ["CROSS", "ISOLATED"], "margin_mode": None,
            "context": {"coin": name, "funding": str(funding), "openInterest": str(oi),
                        "prevDayPx": str(prev), "dayNtlVlm": str(vol), "premium": "0",
                        "oraclePx": str(px), "markPx": str(px), "midPx": str(px)}}


def _positions_for(wallet):
    i = _W.index(wallet)
    pos = []
    if i < 14:
        pos.append({"coin": "BTC", "szi": "-0.5", "positionValue": "30000", "entryPx": "60000"})
    elif i < 16:
        pos.append({"coin": "BTC", "szi": "0.2", "positionValue": "12000", "entryPx": "60000"})
    if i < 10:
        pos.append({"coin": "ETH", "szi": "3", "positionValue": "9000", "entryPx": "3000"})
    if 10 <= i < 13:
        pos.append({"coin": "SOL", "szi": "-40", "positionValue": "6000", "entryPx": "150"})
    if i == 17:
        pos.append({"coin": "xyz:TSLA", "szi": "10", "positionValue": "3557", "entryPx": "355"})
    if i == 18:
        pos.append({"coin": "OUTSIDE", "szi": "1", "positionValue": "100", "entryPx": "100"})
    return pos


def fake_call_tool(name, args, oi_btc=12000, fail=()):
    """The documented shapes, trimmed. `fail` names tools that raise (a dead service)."""
    if name in fail:
        raise RuntimeError(f"{name} HTTP 503")
    if name == "market_list_instruments":
        rows = [_inst("BTC", 500e6, oi_btc, 60000, 59000, 0.0000125),
                _inst("ETH", 200e6, 300000, 3000, 3060, -0.00002),
                _inst("SOL", 50e6, 900000, 150, 149, 0.00004),
                _inst("xyz:TSLA", 22e6, 122987, 355.79, 359.98, 0.00000625),
                _inst("THIN", 200e3, 10, 1, 1, 0.0),
                _inst("DEAD", 90e6, 10, 1, 1, 0.0, delisted=True)]
        return {"success": True, "data": {"instruments": rows, "count": len(rows)}}
    if name == "discovery_get_top_traders":
        assert args["time_frame"] == "ALL_TIME" and args["limit"] == 1000
        rows = [{"address": w, "realizedProfitAndLoss": 5_000_000 - i * 1000} for i, w in enumerate(_W)]
        return {"success": True, "data": {"traders": rows, "count": len(rows)}}
    if name == "discovery_get_trader_state":
        addrs = args["trader_addresses"]
        assert len(addrs) <= 50
        rows = [{"address": w, "openPositions": _positions_for(w),
                 "marginSummary": {"accountValue": "100000"}} for w in addrs]
        return {"success": True, "data": {"traders": rows, "count": len(rows)}}
    if name == "leaderboard_get_markets":
        mk = [{"token": "BTC", "dex": "", "direction": "long", "pct_of_top_traders_gain": 31.2,
               "token_price_change_pct_4h": 0.4, "day_notional_volume": 5e8, "trader_count": 40,
               "is_dominant_direction": True},
              {"token": "BTC", "dex": "", "direction": "short", "pct_of_top_traders_gain": 5.1,
               "token_price_change_pct_4h": 0.4, "day_notional_volume": 5e8, "trader_count": 9,
               "is_dominant_direction": False},
              {"token": "TSLA", "dex": "xyz", "direction": "short", "pct_of_top_traders_gain": 12.0,
               "token_price_change_pct_4h": -1.2, "day_notional_volume": 2.2e7, "trader_count": 14,
               "is_dominant_direction": True}]
        # the real shape: the MCP handler wraps its client's {markets: [...], source_trader_count, ...}
        # under `markets` again (createResponse(tool, 'read', { markets })), so rows sit at data.markets.markets
        return {"success": True, "data": {"markets": {"markets": mk, "source_trader_count": 500,
                                                      "window": "4h", "timestamp": 0}}}
    if name == "leaderboard_get_momentum_events":
        ev = [{"trader_id": "0x" + "ab" * 20, "tier": 2, "tier_label": "Tier 2", "delta_pnl": 3_000_000,
               "decision": "sent", "blocked_reason": None, "concentration": 0.8,
               "top_positions": [{"market": "ETH", "delta_pnl": 2_500_000, "direction": "long", "leverage": 5}],
               "detected_at": "2026-09-15T11:30:00Z"},
              {"trader_id": "0x" + "cd" * 20, "tier": 1, "tier_label": "Tier 1", "delta_pnl": 2_100_000,
               "decision": "blocked", "blocked_reason": "cooldown", "top_positions": [], "detected_at": NOW}]
        # same wrapping as the board: createResponse(tool, 'read', { events }) → data.events.events
        return {"success": True, "data": {"events": {"events": ev, "total_count": 2}}}
    if name == "market_get_cross_asset_flows":
        return {"success": True, "data": {
            "leader": {"asset": "BTC", "move_pct": 2.5, "direction": "LONG", "sm_aligned": True},
            "laggards": [{"asset": "SOL", "correlation_30d": 0.8, "expected_move_pct": 2.1,
                          "actual_move_pct": 0.7, "gap_pct": 1.4, "follow_rate": 0.9,
                          "sm_direction": "LONG", "confidence": 0.7},
                         {"asset": "ETH", "correlation_30d": 0.4, "expected_move_pct": 1.0,
                          "actual_move_pct": 0.2, "gap_pct": 0.8, "follow_rate": 0.5,
                          "sm_direction": "NEUTRAL", "confidence": 0.3}],
            "timestamp": 0}}
    raise AssertionError(f"unexpected tool {name}")


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SENPI_STATE_DIR", str(tmp_path))
    return tmp_path


def test_current_json_carries_the_full_metric_set(state_dir):
    """One sweep gathers the FULL asset_metrics shape every detector reads."""
    rep = sweep.run(fake_call_tool, now=NOW)
    cur = json.load(open(state_dir / "signals" / "current.json"))
    btc = cur["asset_metrics"]["BTC"]
    for k in ("oi", "price", "price_change_pct", "smart_dir", "smart_share", "smart_long_n",
              "smart_short_n", "crowd_dir", "funding_pctile", "funding_annualized_pct",
              "notional_vol", "dex"):
        assert btc.get(k) is not None, k
    assert btc["smart_source"] == "proven_cohort" and btc["smart_share_kind"] == "cohort_pct"
    assert btc["smart_dir"] == "short" and (btc["smart_long_n"], btc["smart_short_n"]) == (2, 14)
    assert btc["smart_share"] == 70.0                       # 14 of 20 sampled wallets
    assert btc["crowd_dir"] == "long" and btc["crowd_source"] == "board_4h"
    assert btc["hot_4h_share"] == 31.2 and btc["price_change_pct"] == 0.4
    assert len(btc["smart_positions"]) == 16 and btc["smart_positions"][_W[0]] == -0.5   # BASE units, signed
    assert cur["asset_metrics"]["xyz:TSLA"]["dex"] == "xyz"
    assert "THIN" not in cur["asset_metrics"] and "DEAD" not in cur["asset_metrics"]
    assert cur["asset_metrics"]["ETH"]["crowd_source"] == "funding_sign"   # not on the board → proxy
    assert cur["asset_metrics"]["ETH"]["crowd_dir"] == "short"             # negative funding ⇒ shorts pay
    assert round(cur["asset_metrics"]["BTC"]["funding_annualized_pct"], 2) == 10.95   # 0.0000125/h × 8760
    assert rep["reads"] == 6 and cur["reads_failed"] == 0
    dets = {e["detector"] for e in cur["events"]}
    assert dets == {"momentum_event", "cross_asset_laggard"}
    assert all("0x" + "ab" * 20 != e.get("concrete_entity") for e in cur["events"])  # shortened, never raw
    assert rep["result"]["coverage"]["smart_money_lens"] == "ok"
    assert cur["wallets"][_W[0]] == {"realized_pnl_usd": 5_000_000.0}          # who a whale is, for the feed
    assert len(cur["wallets"]) == 20
    assert rep["coverage"]["board_4h"] == "ok (2 universe names on the board, 500.0 traders aggregated)"
    assert rep["coverage"]["momentum"].startswith("ok (1 sent events")
    assert (state_dir / "signals" / "signals.md").is_file()
    assert not (state_dir / "signals" / "state.json").exists(), "2.0 keeps no history"


def test_a_failed_read_lands_in_coverage_not_an_exception(state_dir):
    rep = sweep.run(lambda n, a: fake_call_tool(n, a, fail=("leaderboard_get_markets", "market_get_cross_asset_flows")),
                    now=NOW)
    cov = rep["coverage"]
    assert cov["board_4h"].startswith("failed:") and cov["cross_asset"].startswith("failed:")
    assert cov["cohort"].startswith("ok") and cov["universe"].startswith("ok")
    btc = rep["current"]["asset_metrics"]["BTC"]
    assert btc["crowd_source"] == "funding_sign" and "price_change_pct" not in btc   # degraded, honestly
    assert rep["current"]["reads_failed"] == 2 and rep["reads"] == 6
    assert (state_dir / "signals" / "signals.md").is_file()


def test_a_sweep_is_one_reading_so_a_second_sweep_compares_nothing(state_dir):
    """2.0 is a one-time job with no compare: a second sweep 70 min later with BTC OI +25% must NOT
    fire oi_surge (there is no earlier reading), and back-to-back sweeps of the same market rank the same."""
    first = sweep.run(fake_call_tool, now=NOW)
    md_first = (state_dir / "signals" / "signals.md").read_text()
    rep = sweep.run(lambda n, a: fake_call_tool(n, a, oi_btc=15000), now=LATER)
    res = rep["result"]
    assert res["diff_baseline_ts"] is None
    fired = {(s["asset"], s["detector"]) for s in res["trade"] + res["social"]}
    assert not {d for _, d in fired} & sweep.score.HISTORY_DETECTORS, fired
    assert ("BTC", "sm_divergence") in fired          # 70% of the cohort short vs a long crowd: a standing state
    again = sweep.run(fake_call_tool, now=NOW)
    assert (state_dir / "signals" / "signals.md").read_text() == md_first
    assert again["result"]["trade"] == first["result"]["trade"]
    assert sorted(p.name for p in (state_dir / "signals").iterdir()) == ["current.json", "signals.md"]


def test_no_cohort_engine_degrades_to_market_pulse_with_a_warning(state_dir, monkeypatch):
    monkeypatch.setattr(sweep, "_smartmoney", lambda: (_ for _ in ()).throw(ImportError("not installed")))
    rep = sweep.run(fake_call_tool, now=NOW)
    assert rep["coverage"]["cohort"].startswith("unavailable:")
    assert rep["result"]["coverage"]["smart_money_lens"] == "NO DATA"
    assert rep["reads"] == 4


def test_cli_prints_the_read_budget(state_dir, monkeypatch, capsys):
    class _Client:
        def mcp_call(self, tool, timeout=12, **kw):
            return fake_call_tool(tool, kw)
    fake_mod = type(sys)("mcp_client")
    fake_mod.MCPClient = _Client
    monkeypatch.setitem(sys.modules, "mcp_client", fake_mod)
    monkeypatch.setattr(sweep, "skill_scripts", lambda name, marker: str(SCRIPTS))
    assert sweep.main(["--now", NOW]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "reads=6"
    assert json.loads("\n".join(out[:-1]))["generated"] == NOW


def _cli_client(monkeypatch, fail=(), oi_btc=12000):
    class _Client:
        def mcp_call(self, tool, timeout=12, **kw):
            return fake_call_tool(tool, kw, oi_btc=oi_btc, fail=fail)
    fake_mod = type(sys)("mcp_client")
    fake_mod.MCPClient = _Client
    monkeypatch.setitem(sys.modules, "mcp_client", fake_mod)
    monkeypatch.setattr(sweep, "skill_scripts", lambda name, marker: str(SCRIPTS))


def test_print_feed_is_the_feed_and_nothing_about_the_engine(state_dir, monkeypatch, capsys):
    """What an agent runs. Its output is what it presents, so coverage lines, read counts and the
    summary never reach it — the agent kept turning them into a footer for the user."""
    _cli_client(monkeypatch)
    assert sweep.main(["--now", NOW, "--print-feed"]) == 0
    cap = capsys.readouterr()
    assert cap.out.strip() == (state_dir / "signals" / "signals.md").read_text().strip()
    assert cap.err == ""
    for leak in ("[coverage]", "reads=", "[sweep]", "[wrote", "Not measured"):
        assert leak not in cap.out, leak


def test_print_feed_never_carries_a_history_detector(state_dir, monkeypatch, capsys):
    """The 2.0/v2 line at the surface an agent reads. A --print-feed run 70 min after another, with BTC OI
    up 25%, has nothing to compare against: no v2 detector reaches what it prints, and the standing
    smart-money read still does. Passing a --state to score.py from rank() makes this fail."""
    seen, real_feed_text = [], sweep.feed_text
    monkeypatch.setattr(sweep, "feed_text", lambda rep: seen.append(rep) or real_feed_text(rep))
    _cli_client(monkeypatch)
    assert sweep.main(["--now", NOW, "--print-feed"]) == 0
    _cli_client(monkeypatch, oi_btc=15000)
    assert sweep.main(["--now", LATER, "--print-feed"]) == 0
    printed = capsys.readouterr().out.rstrip("\n")
    second = seen[-1]["result"]
    fired = {s["detector"] for s in second["trade"] + second["social"]}
    assert "sm_divergence" in fired and not fired & sweep.score.HISTORY_DETECTORS, fired
    assert second["diff_baseline_ts"] is None
    assert printed.endswith(real_feed_text(seen[-1]))          # what it printed is that result, verbatim
    assert sorted(p.name for p in (state_dir / "signals").iterdir()) == ["current.json", "signals.md"]


def test_print_feed_names_a_failed_source_in_one_plain_line(state_dir, monkeypatch, capsys):
    _cli_client(monkeypatch, fail=("leaderboard_get_markets",))
    assert sweep.main(["--now", NOW, "--print-feed"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("_Not measured this run: the 4h leaderboard._")
    assert "leaderboard_get_markets" not in out
