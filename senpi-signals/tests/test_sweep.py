#!/usr/bin/env python3
"""Tests for sweep.py — the one-process gather, on a fake call_tool returning the documented shapes.

The fixture mirrors the MCP response envelopes (`{"success": true, "data": {...}}`) with the field
names pinned from the MCP source types (market.tools.ts / leaderboard.types.ts / senpi.types.ts).
No real wallet addresses. Run: python3 -m pytest senpi-signals/scripts -q
"""
import json
import os
import pathlib
import re
import sys
import time

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


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Redirect the sweep's output directory at EVERY test in this file, requested or not.

    `sweep.run()` always writes current.json + signals.md, and with SENPI_STATE_DIR unset that
    default is `~/.openclaw/senpi-state/signals` — a real agent's live state. Three whale-age tests
    called `sweep.run()` without asking for this fixture and so overwrote a real machine's cached
    feed with 6-instrument fixture data stamped 2026-09-15, which any consumer reading that file
    would have presented as a current market read.

    autouse rather than three added arguments: the trap is that writing is a SIDE EFFECT of the
    call under test, so a new test only avoids it by remembering a fixture it has no other reason
    to want. Opting out is not a thing any test here needs."""
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
    # each dead source is RETRIED before it is written off, so the failure count is per attempt
    assert rep["current"]["reads_failed"] == 2 * sweep.READ_ATTEMPTS
    assert rep["reads"] == 6 + 2 * (sweep.READ_ATTEMPTS - 1)
    assert (state_dir / "signals" / "signals.md").is_file()


def test_a_transient_failure_is_retried_before_a_source_is_written_off(state_dir):
    """Every read here is an idempotent GET. One 503 used to delete a whole lens from the run — and
    a lens that was never read is indistinguishable, in the feed, from one that looked and found
    nothing. So a source is only dark after it has failed every attempt."""
    budget = {"leaderboard_get_markets": 1}          # fails once, then serves
    def flaky(name, args):
        if budget.get(name):
            budget[name] -= 1
            raise RuntimeError(f"{name} HTTP 503")
        return fake_call_tool(name, args)
    rep = sweep.run(flaky, now=NOW)
    assert budget["leaderboard_get_markets"] == 0
    assert rep["coverage"]["board_4h"].startswith("ok"), rep["coverage"]["board_4h"]
    assert rep["current"]["asset_metrics"]["BTC"]["crowd_source"] == "board_4h"   # the real read won


def test_the_retry_covers_the_cohort_reads_the_vendored_engine_makes(state_dir):
    """The retry was added to `_read()`, which wraps only the four market/leaderboard calls. The
    cohort is gathered by the vendored engine calling `Client.mcp_call` DIRECTLY, so half the reads —
    including the smart-money lens this skill is named after — were still one blip from going dark.
    Counting attempts is the only way to see it: the run succeeds either way, it just loses a lens."""
    attempts = {}
    budget = {"discovery_get_top_traders": 1}      # one transport blip, then it serves
    def flaky(name, args):
        attempts[name] = attempts.get(name, 0) + 1
        if budget.get(name):
            budget[name] -= 1
            raise RuntimeError(f"{name} HTTP 503")
        return fake_call_tool(name, args)
    rep = sweep.run(flaky, now=NOW)
    assert attempts["discovery_get_top_traders"] >= 2, attempts     # it was retried at all
    assert rep["coverage"]["cohort"].startswith("ok"), rep["coverage"]["cohort"]
    assert rep["current"]["asset_metrics"]["BTC"].get("smart_dir"), "the smart-money lens went dark"


def test_a_dark_universe_is_an_outage_not_a_quiet_market(state_dir):
    """`market_list_instruments` is the one read everything hangs off. When it dies there are no
    assets to score, so score.py renders its quiet-market line — and SKILL.md teaches the agent that
    a quiet read is a correct answer to present as-is. A total outage would be reported to the user
    as a calm market, at exit code 0. It is an error, and it has to look like one."""
    rep = sweep.run(lambda n, a: fake_call_tool(n, a, fail=("market_list_instruments",)), now=NOW)
    assert sweep.universe_is_dark(rep["coverage"])
    feed = sweep.feed_text(rep)
    assert "could not read the market this run" in feed
    assert "outage, not a quiet market" in feed
    assert "quiet read is a correct answer" not in feed     # never the calm-market line
    assert "Nothing notable stands out" not in feed
    # the brief closes EVERY market pulse — an outage must not print there as a quiet market either
    brief = sweep.brief_text(rep, 3)
    assert "outage, not a quiet market" in brief
    assert "nothing strong right now" not in brief and "top reads right now" not in brief
    # and a healthy-but-empty run is still allowed to say so
    assert not sweep.universe_is_dark(sweep.run(fake_call_tool, now=NOW)["coverage"])


def test_two_sweeps_sharing_an_out_dir_never_present_each_others_universe(state_dir, monkeypatch):
    """The chip and the sweep inside every market pulse share one out dir. Both used to write
    current.json and then re-read it, so if a second run landed in between, the first RANKED the
    second's universe and presented assets it had never read — golden rule 1 broken by a filesystem
    race, silently, exit 0. Run B here starts and finishes between A's write and A's read."""
    inner = {}
    real_rank = sweep.rank

    def other_universe(name, args):
        resp = fake_call_tool(name, args)
        if name == "market_list_instruments":
            resp["data"]["instruments"].append(_inst("ONLYINB", 400e6, 5000, 10.0, 9.0, 0.00009))
        return resp

    def racing_rank(*a, **kw):
        if not inner.get("started"):              # exactly once, and B must not re-enter this
            inner["started"] = True
            inner["rep"] = sweep.run(other_universe, now=NOW)
        return real_rank(*a, **kw)

    monkeypatch.setattr(sweep, "rank", racing_rank)
    rep_a = sweep.run(fake_call_tool, now=NOW)

    assert "ONLYINB" in inner["rep"]["current"]["asset_metrics"]        # B really did read it
    assert "ONLYINB" not in rep_a["current"]["asset_metrics"]           # A never did
    assert "ONLYINB" not in json.dumps(rep_a["result"])                 # so A cannot have ranked it
    assert "ONLYINB" not in sweep.feed_text(rep_a)                      # nor present it
    # and no per-run scratch file is left behind
    leftovers = [f for f in os.listdir(state_dir / "signals") if f.startswith(".")]
    assert leftovers == [], leftovers


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


def test_brief_is_the_top_trade_reads_one_line_each(state_dir, monkeypatch, capsys):
    """What senpi-market-pulse closes with: the same sweep, printed as a title plus the top N trade reads,
    one line each, in the trade feed's order — no legend, no news feed, nothing about the engine."""
    seen, real_run = [], sweep.run
    monkeypatch.setattr(sweep, "run", lambda *a, **k: seen.append(real_run(*a, **k)) or seen[-1])
    _cli_client(monkeypatch)
    assert sweep.main(["--now", NOW, "--brief", "2"]) == 0
    cap = capsys.readouterr()
    lines = cap.out.strip().splitlines()
    trade = seen[-1]["result"]["trade"]
    assert trade, "the fixture should produce at least one trade read"
    assert lines[0] == "**🔭 Senpi Signals — top reads right now**"
    items = lines[1:]
    assert len(items) == min(2, len(trade))
    for line, s in zip(items, trade):
        assert line.startswith(f"- {sweep.score.badge(s['trade_score'])} **{s['trade_score']}** · `{s['asset']}` — ")
        assert sweep.score.trade_read(s) in line
    assert cap.err == ""
    for leak in ("How to read this", "Market news", "[coverage]", "reads=", "[sweep]", "cred"):
        assert leak not in cap.out, leak
    assert sorted(p.name for p in (state_dir / "signals").iterdir()) == ["current.json", "signals.md"]


def test_brief_says_so_when_nothing_clears_the_second_band():
    """Two 🟡 reads under a header that says "top reads right now" reads as a recommendation of the best
    of a weak lot (live, Sep 17: 63.4 and 57.9). Say what the band means instead."""
    weak = [{"asset": "HYPE", "trade_score": 63.4, "detector": "sm_divergence", "direction": "short",
             "numbers": ["cohort 60% short"], "dex": ""},
            {"asset": "ZEC", "trade_score": 57.9, "detector": "sm_divergence", "direction": "short",
             "numbers": ["cohort 58% short"], "dex": ""}]
    out = sweep.score.render_brief(weak, 3)
    assert out.splitlines()[0] == "**🔭 Senpi Signals — nothing strong right now, context only**"
    assert "`HYPE`" in out and "`ZEC`" in out                      # the reads still print
    strong = [dict(weak[0], trade_score=71.0)]
    assert sweep.score.render_brief(strong, 3).splitlines()[0] == "**🔭 Senpi Signals — top reads right now**"


def test_brief_on_a_quiet_market_is_one_plain_line():
    assert sweep.score.render_brief([], 3) == "**🔭 Senpi Signals:** nothing notable stands out right now."


def test_brief_names_a_failed_source_in_one_plain_line(state_dir, monkeypatch, capsys):
    _cli_client(monkeypatch, fail=("leaderboard_get_markets",))
    assert sweep.main(["--now", NOW, "--brief", "3"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("_Not measured this run: the 4h leaderboard._")
    assert "leaderboard_get_markets" not in out


# ──────────────────────────────────────────────── the per-read budget (it has to reach the socket)
def test_the_budget_a_read_asks_for_reaches_the_socket(state_dir, monkeypatch, capsys):
    """`Client.mcp_call(tool, timeout=..., **kw)` bound `timeout` to its own parameter and forwarded
    only `kw`, so every read silently ran on the transport's 12s default — including the cohort
    reads the vendored engine asks 20s for. The MCP server's own upstream budget is 20s, so a 12s
    client budget abandons every read that takes 12-20s before the server can answer it, and the
    server keeps working a query it can no longer deliver. On a slow upstream that is the whole
    smart-money lens, on about half the runs. Counting is the only way to see it: the sweep still
    lands either way, it just loses the lens it is named after."""
    seen = {}

    class _Client:
        def mcp_call(self, tool, timeout=12, **kw):
            seen.setdefault(tool, []).append(timeout)
            return fake_call_tool(tool, kw)

    fake_mod = type(sys)("mcp_client")
    fake_mod.MCPClient = _Client
    monkeypatch.setitem(sys.modules, "mcp_client", fake_mod)
    assert sweep.main(["--now", NOW]) == 0
    capsys.readouterr()
    for tool in sweep.COHORT_TOOLS:
        # the budget must OUTLIVE the server's 20s upstream budget, or the client always gives up first
        assert seen[tool] and min(seen[tool]) > 20, (tool, seen.get(tool))
    for tool in ("market_list_instruments", "leaderboard_get_markets",
                 "leaderboard_get_momentum_events", "market_get_cross_asset_flows"):
        assert set(seen[tool]) == {sweep.READ_TIMEOUT_S}, (tool, seen[tool])   # short reads stay short


def test_the_budget_is_never_sent_to_the_server_as_a_tool_argument(state_dir):
    """`args` is the tool-arguments payload the MCP server receives, so a budget folded into it
    would be sent as a tool argument. A `call_tool(name, args)` that knows nothing about budgets —
    the runtime's, and this fixture's — is still called with two arguments and still reads."""
    calls = []

    def two_arg(name, args):
        calls.append((name, dict(args)))
        return fake_call_tool(name, args)

    rep = sweep.run(two_arg, now=NOW)
    assert rep["coverage"]["cohort"].startswith("ok"), rep["coverage"]["cohort"]
    assert calls and all("timeout" not in args for _, args in calls), calls


def test_a_call_tool_that_can_carry_a_budget_is_given_one(state_dir):
    budgets = {}

    def with_budget(name, args, timeout=None):
        budgets[name] = timeout
        return fake_call_tool(name, args)

    sweep.run(with_budget, now=NOW)
    assert budgets["discovery_get_top_traders"] == sweep.COHORT_TIMEOUT_S
    assert budgets["discovery_get_trader_state"] == sweep.COHORT_TIMEOUT_S
    assert budgets["market_list_instruments"] == sweep.READ_TIMEOUT_S


# ──────────────────────────────── the run-level deadline (patient reads still have to come back)
class _Clock:
    """A monotonic source the fake reads advance themselves — a degraded upstream, without the wait."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _slow_call_tool(clock, seconds_per_read, calls=None):
    def call_tool(name, args, timeout=None):
        if calls is not None:
            calls.append(name)
        clock.t += seconds_per_read
        return fake_call_tool(name, args)
    return call_tool


def test_a_read_the_deadline_cannot_pay_for_is_never_started():
    """The projection, not elapsed time, is the cap: a 30s cohort read starting at 31s of a 45s
    deadline lands at 61s, which is the overrun the deadline exists to prevent."""
    clock, calls = _Clock(), []
    c = sweep.Client(_slow_call_tool(clock, 0.0, calls), deadline_s=45, clock=clock)

    clock.t = 20.0
    assert c.mcp_call("market_list_instruments") is not None       # 20 + 15 fits
    clock.t = 31.0
    with pytest.raises(sweep.SweepDeadline):
        c.mcp_call("discovery_get_top_traders")                    # 31 + 30 does not

    assert calls == ["market_list_instruments"], calls             # the second read was never asked for
    assert c.skipped == 1 and c.failed == 0                        # skipped, NOT failed


def test_an_unbounded_client_is_still_unbounded():
    """deadline_s=None is the old behaviour, and it is what the tests and any caller with its own
    bound get. A deadline that cannot be turned off would be a second, invisible timeout."""
    clock = _Clock(t=10_000.0)
    c = sweep.Client(_slow_call_tool(clock, 0.0), clock=clock)
    assert c.mcp_call("market_list_instruments") is not None
    assert c.skipped == 0


def test_the_deadline_renders_a_thin_feed_instead_of_being_killed_mid_flight(state_dir):
    """The reads got more patient (15s/30s) and the caller above them did not: `--brief` runs inside
    another skill's ~60s answer. Without a run-level bound a degraded upstream does not return a
    shorter feed, it returns none at all — the agent kills the process mid-read. So the market lenses
    are read first, the cohort last, and what did get fed is rendered."""
    clock, calls = _Clock(), []
    rep = sweep.run(_slow_call_tool(clock, 8.0, calls), now=NOW,
                    deadline_s=sweep.BRIEF_DEADLINE_S, clock=clock)

    assert calls == ["market_list_instruments", "leaderboard_get_markets",
                     "leaderboard_get_momentum_events", "market_get_cross_asset_flows"], calls
    for key in ("universe", "board_4h", "momentum", "cross_asset"):
        assert rep["coverage"][key].startswith("ok"), (key, rep["coverage"][key])
    assert rep["coverage"]["cohort"].startswith("NO DATA: the proven-cohort read was not started")
    assert clock.t <= sweep.BRIEF_DEADLINE_S, clock.t     # the run is capped AT the deadline

    feed = sweep.brief_text(rep, 3)
    assert feed != sweep.OUTAGE_LINE and feed.strip()
    assert feed.endswith("_Not measured this run: proven-trader positions._"), feed


def test_a_read_the_deadline_skipped_is_never_reported_as_a_failed_read(state_dir):
    """Nothing was asked of the server, so there is no upstream fault to chase. Saying "failed" here
    sends the next investigation at a service that was never called — the same shape of wrong answer
    that "an app-scoped token returns nothing" was, on this very line."""
    clock = _Clock()
    rep = sweep.run(_slow_call_tool(clock, 12.0), now=NOW, deadline_s=sweep.BRIEF_DEADLINE_S, clock=clock)

    cross = rep["coverage"]["cross_asset"]                 # skipped inside _read, at 36s + 15s > 45s
    assert cross.startswith("NO DATA: market_get_cross_asset_flows not started"), cross
    assert "deadline" in cross and not cross.startswith("failed")

    cohort = rep["coverage"]["cohort"]                     # skipped as a whole lens, before its first read
    assert "Not a failed read and not a token problem." in cohort, cohort
    assert rep["current"]["reads_skipped"] == 2
    assert f"past the {sweep.BRIEF_DEADLINE_S}s deadline" in rep["summary"], rep["summary"]


def test_the_brief_path_is_given_less_time_than_the_full_feed(state_dir, monkeypatch, capsys):
    """`--brief` closes another skill's answer on that skill's budget (~60s today); `--print-feed` is
    the whole answer (~120s). One deadline for both would either strand the feed or overrun the brief."""
    seen, real_run = [], sweep.run
    monkeypatch.setattr(sweep, "run", lambda ct, **kw: (seen.append(kw["deadline_s"]), real_run(ct, **kw))[1])
    _cli_client(monkeypatch)

    assert sweep.main(["--now", NOW, "--brief", "2"]) == 0
    assert sweep.main(["--now", NOW, "--print-feed"]) == 0
    capsys.readouterr()
    assert seen == [sweep.BRIEF_DEADLINE_S, sweep.FEED_DEADLINE_S]
    assert sweep.BRIEF_DEADLINE_S < sweep.FEED_DEADLINE_S


def _whale_tool(pos, coin="BTC"):
    """fake_call_tool, with one proven wallet holding `pos` on `coin`. The cohort is cut from
    discovery_get_top_traders by lifetime realized, so _W[0] is the most proven wallet there is."""
    def call(name, args):
        resp = fake_call_tool(name, args)
        if name == "discovery_get_trader_state" and _W[0] in args.get("trader_addresses", []):
            for row in resp["data"]["traders"]:
                if row["address"] == _W[0]:
                    row["openPositions"] = [dict(pos, coin=coin)]
        return resp
    return call


def _whale(age_s, notional="12400000", entry="77500"):
    """A $12.4M short whose startTime is `age_s` ago. `startTime` is Unix SECONDS, absolute — the
    field the detector actually reads, so the fixture exercises the real code path."""
    return {"szi": "-160.0", "positionValue": notional, "entryPx": entry,
            "startTime": int(time.time()) - age_s}


FRESH_WHALE = _whale(1080)                     # $12.4M short, opened 18 minutes ago


def test_a_proven_wallet_opening_size_is_read_from_one_sweep(state_dir):
    """The whole point: no history. `discovery_get_trader_state` returns the position's own age, so
    "opened 18 minutes ago" is a fact about the position rather than a diff against an earlier sweep
    of ours — which is why this ships in 2.0 while adds and flips (they need the old size / old side)
    stay in v2."""
    rep = sweep.run(_whale_tool(FRESH_WHALE), now=NOW)
    btc = rep["current"]["asset_metrics"]["BTC"]
    assert btc["whale_opens"], "a $12.4M position opened 18 minutes ago was not picked up"
    o = btc["whale_opens"][0]
    assert o["direction"] == "short" and o["notional_usd"] == 12_400_000.0
    assert 1080 <= o["age_seconds"] < 1140, o["age_seconds"]   # measured from startTime, so it ticks
    sigs = [s for s in rep["result"]["trade"] + rep["result"]["social"] if s["detector"] == "whale_open"]
    assert sigs, "the open never reached a feed"
    nums = " ".join(sigs[0]["numbers"])
    # not a fixed minute: the age is measured from startTime and ceil never under-reports, so a
    # fixture set 1080s back reads as 18 or 19 minutes depending on how long the sweep took
    assert "12.4M" in nums and "SHORT" in nums
    assert re.search(r"(18|19) minutes ago$", nums), nums
    assert sigs[0]["concrete_entity"], "a whale read with no wallet on it is just a number"


def test_a_position_we_cannot_date_is_never_called_an_open(state_dir):
    """An undated whale position is almost always an OLD one. "Opened just now" is exactly the claim
    that must never be guessed — golden rule 1, and rule 5's ban on claiming a move we cannot time."""
    undated = {k: v for k, v in FRESH_WHALE.items() if k != "startTime"}
    rep = sweep.run(_whale_tool(undated), now=NOW)
    assert rep["current"]["asset_metrics"]["BTC"]["whale_opens"] == []


def test_an_old_or_small_position_is_a_holding_not_news(state_dir):
    for pos, why in ((_whale(5 * 3600), "5h old — a holding"),
                     (_whale(1080, "500000"), "$500k — a position, not a statement")):
        rep = sweep.run(_whale_tool(pos), now=NOW)
        assert rep["current"]["asset_metrics"]["BTC"]["whale_opens"] == [], why


def test_whale_open_is_not_a_history_detector(state_dir):
    """It must be reachable from `--print-feed`, which is gated against every detector that needs an
    earlier reading. If it ever lands in HISTORY_DETECTORS this skill silently stops carrying it."""
    import score
    assert "whale_open" not in score.HISTORY_DETECTORS
    assert "whale_open" in score.EDGE and "whale_open" in score.NON_OBVIOUS
    rep = sweep.run(_whale_tool(FRESH_WHALE), now=NOW)
    assert "whale_open" in sweep.feed_text(rep) or "opened" in sweep.feed_text(rep)


def test_an_age_always_rounds_away_from_freshness():
    """The previous version of this test named the rounding rule and then tested only values that do
    not round — so `f"{s/3600:.0f}"` (round to NEAREST) passed it while reporting 2h30m as "2 hours
    ago". Understating an age is the overstatement for a detector whose claim is "this just
    happened", so every case below is one that actually rounds."""
    import score
    assert score._ago(30) == "just now"
    assert score._ago(90) == "2 minutes ago"          # not "1 minutes ago"
    assert score._ago(1080) == "18 minutes ago"
    assert score._ago(3540) == "59 minutes ago"
    assert score._ago(3600) == "an hour ago"
    assert score._ago(8999) == "3 hours ago"          # 2h30m — never "2 hours"
    assert score._ago(12600) == "4 hours ago"         # 3h30m — never "3 hours"
    for s in range(60, 4 * 3600, 617):                # never claims fresher than the truth
        spoken = score._ago(s)
        if spoken.endswith("minutes ago"):
            assert int(spoken.split()[0]) * 60 >= s
        elif spoken.endswith("hours ago"):
            assert int(spoken.split()[0]) * 3600 >= s


def test_the_read_says_opened_exactly_once():
    """`numbers` used to end with "opened 18 minutes ago" while both renderers prepend "opened" —
    so the flagship sentence read "opened $12.4M SHORT opened 18 minutes ago". The old tests were
    substring checks on "12.4M" and "18 minutes ago", which passed either way."""
    import score
    s = {"asset": "BTC", "detector": "whale_open", "direction": "short", "price_change_pct": None,
         "numbers": ["$12.4M SHORT 18 minutes ago"], "concrete_entity": "0x0000…0001",
         "entity_realized_pnl_usd": 5_000_000.0, "smart_source": "proven_cohort"}
    for rendered in (score.trade_read(s), score.frame(s)):
        assert rendered.lower().count("opened") == 1, rendered



def test_a_reset_clock_is_caught_by_the_price_the_position_opened_at():
    """`startTime` has been seen jumping forward on untouched positions, which makes an OLD position
    read as newly opened — and the error only ever runs in that direction, straight onto this
    detector's trigger. The entry price is a second witness from the same read: a position that
    really opened minutes ago is still near the price it opened at. 160 x 77,500 = $12.4M, so an
    entry of 71,000 means the position is ~9% away from where it says it just opened."""
    stale = _whale(17)                              # the exact shape of an observed reset: ~seconds old
    stale["entryPx"] = "71000"                      # …but nine percent from the mark
    rep = sweep.run(_whale_tool(stale), now=NOW)
    assert rep["current"]["asset_metrics"]["BTC"]["whale_opens"] == [], "a reset clock was believed"


def test_the_drift_allowance_grows_with_the_age_being_claimed():
    """A four-hour-old position is allowed to have moved; a seventeen-second-old one is not. The
    allowance has to scale, or the guard either rejects every real open or catches no resets."""
    near = _whale(17)                               # 77,500 entry vs 77,500 mark — no drift
    assert sweep.run(_whale_tool(near), now=NOW)["current"]["asset_metrics"]["BTC"]["whale_opens"]
    old_but_moved = _whale(3 * 3600, entry="74000")  # 3h old, ~4.5% drift — plausible over 3 hours
    assert sweep.run(_whale_tool(old_but_moved), now=NOW)["current"]["asset_metrics"]["BTC"]["whale_opens"]


def test_a_position_with_no_entry_price_is_not_claimed():
    """No second witness ⇒ no claim. The same rule as an undated position."""
    no_entry = {k: v for k, v in _whale(1080).items() if k != "entryPx"}
    rep = sweep.run(_whale_tool(no_entry), now=NOW)
    assert rep["current"]["asset_metrics"]["BTC"]["whale_opens"] == []
