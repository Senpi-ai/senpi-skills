#!/usr/bin/env python3
"""Offline engine test — runs research.run() against a recorded MCP fixture (no network)."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import research  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "research_fixture.json")


def _client():
    with open(FIXTURE) as f:
        return research._FixtureClient(json.load(f))


def test_top_candidates_and_reliability():
    res = research.run(_client(), "top")
    assert len(res["candidates"]) == 3
    # trades/active_days are DERIVED from averageTradesPerDay × traderAgeSeconds (live fields)
    pro = next(c for c in res["candidates"] if c["address"] == "0xpro")
    assert pro["active_days"] == 90.0 and pro["trades"] == 140
    assert pro["consistency"] == "ELITE"              # from tcsLabel
    assert pro["reliability"] == "solid"
    assert next(c for c in res["candidates"] if c["address"] == "0xstreak")["reliability"] == "thin"


def test_vet_dossier():
    res = research.run(_client(), "vet", addr="0xpro")
    t = res["trader"]
    assert t["track_record"]["roi_pct"] == 62.0
    assert t["labels"]["consistency"] == "ELITE"
    assert t["net_exposure"]["margin_pct"] == 84.0
    assert "high_margin_usage" in t["flags"]          # 84 > 80
    assert "concentrated_book" in t["flags"]          # BTC notional dominates
    # momentum record is nested under data.trader in the live shape
    assert t["recent_momentum"]["rank"] == 12.0
    assert t["recent_momentum"]["delta_pnl_4h_usd"] == 850.0
    assert t["recent_momentum"]["active_positions"] == 2.0


def test_strategies_mode():
    res = research.run(_client(), "strategies")
    s = res["strategies"][0]
    assert s["total_pnl_usd"] == 5000
    assert s["return_pct"] == 25.0                    # pnlPercentage
    assert s["followers"] == 40.0                     # traderFollowerCount
    assert s["age_days"] is not None                  # derived from strategyCreatedAt


def test_fails_open_on_empty():
    res = research.run(research._FixtureClient({}), "top")
    assert res["candidates"] == [] and res["meta"].get("degraded")


def test_reliability_drawdown_gate():
    # A real record (ELITE, deep sample) can't read "solid" if the trader was once near-liquidated.
    base = {"trades": 100, "active_days": 200, "consistency": "ELITE"}
    assert research._reliability({**base, "max_drawdown_pct": -20}) == "solid"
    assert research._reliability({**base, "max_drawdown_pct": -65}) == "ok"      # capped below solid
    assert research._reliability({**base, "max_drawdown_pct": -85}) == "choppy"  # near-liquidation
    assert "blowup_risk" in research._flags({**base, "max_drawdown_pct": -85})


def test_mirrorability_uses_price_distance():
    # distance from entry (not leveraged ROE) is the go/no-go — it's what slippage gates on
    ran = [{"asset": "X", "direction": "long", "notional": 100.0, "moved_from_entry_pct": 40.0}]
    fresh = [{"asset": "Y", "direction": "long", "notional": 100.0, "moved_from_entry_pct": 1.5}]
    assert research._mirrorability(ran)["mirror_fit"] == "poor"
    assert research._mirrorability(fresh)["mirror_fit"] == "good"
    assert research._mirrorability([])["mirror_fit"] == "unknown"


def test_high_turnover_flag():
    assert "high_turnover" in research._flags({"trades_per_day": 19.0})    # a copy bleeds fees
    assert "high_turnover" not in research._flags({"trades_per_day": 1.5})


def test_find_is_mirror_aware():
    res = research.run(_client(), "top")                       # enrichment on by default
    pro = next(c for c in res["candidates"] if c["address"] == "0xpro")
    # 0xpro's BTC sits ~4% from entry (mark 62.5k vs entry 60k) → most of the book is fresh
    assert pro["mirrorability"]["mirror_fit"] == "good"
    assert pro["momentum"] == "hot"                            # +850 4h delta
    assert res["mirror_shortlist"][0]["address"] == "0xpro"    # the copyable one leads, not the ROI king


def test_no_mirror_stays_track_record_only():
    res = research.run(_client(), "top", enrich_top=0)
    assert "mirror_shortlist" not in res
    assert all("mirrorability" not in c for c in res["candidates"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
