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


class _SeqClient:
    """Returns a different discovery_get_top_traders payload on each successive call — lets a test give the
    same address different fields across the blend's three views (to exercise cross-view backfill). Every other
    tool returns None, so enrichment fails open to an empty book."""
    def __init__(self, payloads):
        self._p, self._i = list(payloads), 0

    def mcp_call(self, tool, timeout=12, **kw):
        if tool == "discovery_get_top_traders":
            p = self._p[min(self._i, len(self._p) - 1)]
            self._i += 1
            return {"success": True, "data": {"traders": p}}
        return None


def test_min_mirror_budget():
    # MARGIN model: to open a position at the ~$12 notional floor a mirror needs MIN_NOTIONAL_USD / leverage in
    # margin — matches the platform (the old notional-proportional formula overstated ~20x on leveraged books).
    small = [{"notional": 5000, "direction": "long", "moved_from_entry_pct": 1.0, "leverage": 10},   # 12/10 = 1.2
             {"notional": 3000, "direction": "long", "moved_from_entry_pct": 1.0, "leverage": 5},     # 12/5  = 2.4
             {"notional": 2000, "direction": "long", "moved_from_entry_pct": 1.0, "leverage": 3}]     # 12/3  = 4.0
    b = research._min_mirror_budget(1000.0, small)               # Σ = 7.6, cheapest = 1.2 → both clamp to $10
    assert b["min_budget_usd"] == 10.0 and b["opens_nothing_below_usd"] == 10.0 and b["positions"] == 3
    # a book whose Σ margins clears the $10 floor
    big = [{"notional": 9000, "direction": "long", "moved_from_entry_pct": 1.0, "leverage": 2} for _ in range(5)]  # 5 × (12/2=6) = 30
    assert research._min_mirror_budget(1000.0, big)["min_budget_usd"] == 30.0
    # a position that ran in the OG's favor is slippage-skipped → costs no margin
    ran = [{"notional": 5000, "direction": "long", "moved_from_entry_pct": 40.0, "leverage": 2},   # ran up 40% → skipped
           {"notional": 3000, "direction": "long", "moved_from_entry_pct": 1.0, "leverage": 2}]     # openable
    assert research._min_mirror_budget(1000.0, ran)["positions"] == 1
    # missing leverage → assume 1x (full notional as margin): 12/1 = 12
    assert research._min_mirror_budget(1000.0, [{"notional": 100, "direction": "long", "moved_from_entry_pct": 0.0}])["min_budget_usd"] == 12.0
    # flat / unknown book → None (account_value is no longer required by the margin model)
    assert research._min_mirror_budget(1000.0, []) is None
    assert research._min_mirror_budget(1000.0, None) is None


def test_min_mirror_budget_wired():
    # the key is always set (a dict when computable, else None) — never silently absent
    assert "min_mirror_budget" in research.run(_client(), "vet", addr="0xpro")["trader"]
    enriched = [c for c in research.run(_client(), "top")["candidates"] if "mirrorability" in c]
    assert all("min_mirror_budget" in c for c in enriched)


def test_activity_and_recency_flags():
    # a mirror only fires when the OG trades — flag the ones that will sit idle so the agent warns first
    assert "infrequent_trader" in research._flags({"trades_per_day": 0.05})   # ~1 trade / 3 weeks
    assert "infrequent_trader" not in research._flags({"trades_per_day": 3.0})
    assert "dormant" in research._flags({"last_trade_days_ago": 180})          # last traded 6 months ago
    assert "dormant" not in research._flags({"last_trade_days_ago": 4})
    # recency is derived from lastTradeTimestamp (seconds OR ms), relative to now
    import time
    c = research._candidate({"address": "0xz", "lastTradeTimestamp": time.time() - 10 * 86400})   # 10d ago, seconds
    assert abs(c["last_trade_days_ago"] - 10.0) < 0.2
    c_ms = research._candidate({"address": "0xz", "lastTradeTimestamp": (time.time() - 10 * 86400) * 1000})  # ms
    assert abs(c_ms["last_trade_days_ago"] - 10.0) < 0.2
    assert research._candidate({"address": "0xz"})["last_trade_days_ago"] is None   # no timestamp -> None


def test_book_summary():
    poss = [{"asset": "BTC", "direction": "long", "notional": 500},
            {"asset": "ETH", "direction": "short", "notional": 200}]
    b = research._book_summary(poss, 300)   # net_notional > 0 -> net long
    assert b["open_positions"] == 2 and b["longs"] == 1 and b["shorts"] == 1
    assert b["bias"] == "net long" and b["top_assets"] == ["BTC", "ETH"]   # sorted by notional
    assert research._book_summary([], 0.0)["open_positions"] == 0


def test_blend_default_tags_views_single_view_does_not():
    # default FIND blends complementary views — every candidate is tagged with the view(s) it came from
    res = research.run(_client(), "top")
    assert res["candidates"] and all(c.get("seen_in") for c in res["candidates"])
    assert "blend" in res["ranking"]
    # an explicit single-view (blend=False) tags nothing and keeps the old ranking shape
    single = research.run(_client(), "top", blend=False)
    assert all(c.get("seen_in") == [] for c in single["candidates"])
    assert "time_frame" in single["ranking"]


def test_copyability_prefers_clean_partial_over_flagged_good_fit():
    # the pick must be row #1: a clean, reliable, active partial-fit trader beats a flagged good-fit one
    clean_partial = {"mirrorability": {"mirror_fit": "partial", "fresh_entry_surface_pct": 40},
                     "flags": [], "reliability": "solid", "seen_in": []}
    flagged_good = {"mirrorability": {"mirror_fit": "good", "fresh_entry_surface_pct": 70},
                    "flags": ["choppy_consistency", "infrequent_trader"], "reliability": "choppy", "seen_in": []}
    stale_clean = {"mirrorability": {"mirror_fit": "poor", "fresh_entry_surface_pct": 5},
                   "flags": [], "reliability": "solid", "seen_in": []}
    ordered = sorted([flagged_good, stale_clean, clean_partial], key=research._mirror_sort_key)
    assert ordered[0] is clean_partial          # clean + mirrorable-now leads
    assert ordered[-1] is stale_clean           # poor fit (stale) still demoted below mirrorable ones


def test_top_candidates_and_reliability():
    res = research.run(_client(), "top")
    assert len(res["candidates"]) == 3
    # active_days is derived from traderAgeSeconds; `trades` is NOT derivable from the find payload → None
    pro = next(c for c in res["candidates"] if c["address"] == "0xpro")
    assert pro["active_days"] == 90.0 and pro["trades"] is None   # closed count only comes from the vet path
    assert pro["consistency"] == "ELITE"              # from tcsLabel
    assert pro["reliability"] == "solid"
    assert next(c for c in res["candidates"] if c["address"] == "0xstreak")["reliability"] == "thin"


def test_vet_dossier():
    res = research.run(_client(), "vet", addr="0xpro")
    t = res["trader"]
    assert t["track_record"]["roi_pct"] == 62.0
    assert t["track_record"]["trades"] == 140   # the REAL closed count, from discovery_get_trader_history page_info.totalCount
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
    # Perps run big drawdowns on leverage — only near-liquidation (~83%+) caps a proven record.
    base = {"trades": 100, "active_days": 200, "consistency": "ELITE"}
    assert research._reliability({**base, "max_drawdown_pct": -20}) == "solid"
    assert research._reliability({**base, "max_drawdown_pct": -70}) == "solid"   # big DD is normal on perps
    assert research._reliability({**base, "max_drawdown_pct": -85}) == "ok"      # ≤ -83 caps solid -> ok
    assert research._reliability({**base, "max_drawdown_pct": -92}) == "choppy"  # ≤ -90 forced choppy
    assert "blowup_risk" not in research._flags({**base, "max_drawdown_pct": -79})  # not a red flag on perps
    assert "blowup_risk" in research._flags({**base, "max_drawdown_pct": -85})


def test_mirrorability_uses_price_distance():
    # distance from entry (not leveraged ROE) is the go/no-go — it's what slippage gates on
    ran = [{"asset": "X", "direction": "long", "notional": 100.0, "moved_from_entry_pct": 40.0}]
    fresh = [{"asset": "Y", "direction": "long", "notional": 100.0, "moved_from_entry_pct": 1.5}]
    assert research._mirrorability(ran)["mirror_fit"] == "poor"
    assert research._mirrorability(fresh)["mirror_fit"] == "good"
    assert research._mirrorability([])["mirror_fit"] == "unknown"


def test_mirrorability_is_directional_not_symmetric():
    # the platform gates slippage ONLY in the OG's favor — underwater-for-the-OG is a cheaper entry and OPENS.
    # long DOWN 40% (underwater for the OG) → adverse -40 <= band → opens → good
    assert research._mirrorability(
        [{"direction": "long", "notional": 100.0, "moved_from_entry_pct": -40.0}])["mirror_fit"] == "good"
    # short UP 40% (underwater for the OG short) → adverse -40 <= band → opens → good
    assert research._mirrorability(
        [{"direction": "short", "notional": 100.0, "moved_from_entry_pct": 40.0}])["mirror_fit"] == "good"
    # short DOWN 40% (ran in the OG's favor) → adverse +40 > band → skipped → poor
    assert research._mirrorability(
        [{"direction": "short", "notional": 100.0, "moved_from_entry_pct": -40.0}])["mirror_fit"] == "poor"


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


def test_blend_drops_gain_to_pain_for_realized_pnl():
    # GAIN_TO_PAIN_RATIO surfaces wiped / days-old / micro-volume accounts on live data — not a blend axis.
    sorts = [sb for _, sb, _ in research.MIRROR_VIEWS]
    assert "GAIN_TO_PAIN_RATIO" not in sorts
    assert "PROFIT_AND_LOSS_REALIZED" in sorts          # banked profit (not paper gains) replaces it
    assert sorts.count("RETURN_ON_INVESTMENT") == 2       # 7d hot + 30d return


def test_zero_sentinels_coerced_but_real_zeros_kept():
    # the live payload uses 0 for "not computed" — a 0 max-drawdown must not read as a flawless record,
    # and a 0 ROI on a real earner must not sink them; but genuine zeros and a real low tcs are kept.
    c = research._candidate({"address": "0x", "maxDrawdown": 0, "returnOnInvestment": 0, "profitAndLoss": 5000})
    assert c["max_drawdown_pct"] is None                 # 0 DD = missing, not a perfect record
    assert c["roi_pct"] is None                          # +PnL with 0% ROI = ROI not computed
    assert "blowup_risk" not in research._flags(c)        # None DD → unknown, never a fabricated clean record
    flat = research._candidate({"address": "0x", "returnOnInvestment": 0, "profitAndLoss": 0})
    assert flat["roi_pct"] == 0                          # 0 ROI AND 0 PnL is ambiguous — leave it, don't invent None
    assert research._candidate({"address": "0x", "tcsValue": 0})["tcs_score"] == 0   # tcs 0 is a real low score


def test_cross_view_backfill_recovers_real_drawdown():
    # a wiped account can show maxDrawdown=0 (sentinel) in one view and the real -100 in another; the blend
    # must not lose the real value to the first view's blank — else blowup_risk never fires.
    base = {"address": "0xw", "returnOnInvestment": 500, "profitAndLoss": 1000,
            "totalTrades": 50, "traderAgeSeconds": 30 * 86400, "tcsLabel": "STREAKY"}
    v_sentinel = [{**base, "maxDrawdown": 0}]
    v_real = [{**base, "maxDrawdown": -100}]
    res = research.run(_SeqClient([v_sentinel, v_real, v_sentinel]), "top")
    w = next(c for c in res["candidates"] if c["address"] == "0xw")
    assert w["max_drawdown_pct"] == -100                 # backfilled from the view that carried it
    assert "blowup_risk" in w["flags"]                    # the gate fires now; the sentinel view alone hid it


def test_roi_pnl_conflict_flags_but_keeps_the_trader():
    # +ROI with -PnL is a caution, not a disqualifier: flag it, demote it, DON'T drop it.
    assert "roi_pnl_conflict" in research._flags({"roi_pct": 295, "pnl_usd": -1_584_318})
    assert "roi_pnl_conflict" not in research._flags({"roi_pct": 50, "pnl_usd": 1000})    # both positive → fine
    assert "roi_pnl_conflict" not in research._flags({"roi_pct": -10, "pnl_usd": -500})   # both negative → honest loser
    conflict = {"mirrorability": {"mirror_fit": "good", "fresh_entry_surface_pct": 80},
                "flags": ["roi_pnl_conflict"], "reliability": "solid", "seen_in": []}
    clean = {"mirrorability": {"mirror_fit": "good", "fresh_entry_surface_pct": 50},
             "flags": [], "reliability": "solid", "seen_in": []}
    ordered = sorted([conflict, clean], key=research._mirror_sort_key)
    assert ordered[0] is clean and conflict in ordered    # clean leads; the conflicted trader stays on the list


def test_no_open_positions_flags_but_keeps_the_trader():
    # an empty current book = nothing to copy today: flag it so the user knows, don't exclude the trader.
    assert "no_open_positions" in research._flags({}, positions=[])
    assert "no_open_positions" not in research._flags({}, positions=[{"notional": 100}])
    assert "no_open_positions" not in research._flags({})                # unenriched (positions=None) → assert nothing


def test_tcs_score_breaks_ties_in_the_shortlist():
    # the consistency SCORE behind the label orders otherwise-equal candidates — don't discard it
    hi = {"mirrorability": {"mirror_fit": "good", "fresh_entry_surface_pct": 50}, "flags": [],
          "reliability": "solid", "seen_in": ["30d return #1"], "tcs_score": 90}
    lo = {"mirrorability": {"mirror_fit": "good", "fresh_entry_surface_pct": 50}, "flags": [],
          "reliability": "solid", "seen_in": ["30d return #1"], "tcs_score": 40}
    assert sorted([lo, hi], key=research._mirror_sort_key)[0] is hi


def test_trades_not_fabricated_in_find_path():
    # the find/blend payload CANNOT give a real closed count: realizedPnL / avgProfitPerTrade is NOT it on the
    # ALL_TIME payload (821679.97 / 7402.52 = 111 for a trader with 2 closed positions). So _candidate leaves
    # trades None — "not established" beats a fabricated number; the vet path fills it exactly.
    c = research._candidate({"address": "0xfbc9", "realizedProfitAndLoss": 821679.97, "averageProfitPerTrade": 7402.52,
                             "totalTrades": 55000, "averageTradesPerDay": 7071.0, "traderAgeSeconds": 572 * 86400})
    assert c["trades"] is None                        # NOT 111, NOT 55000 fills, NOT 7071×age


class _VetHistoryClient:
    """Vet a trader whose record barely exists: ELITE label + long age, but only 2 closed positions."""
    def mcp_call(self, tool, timeout=12, **kw):
        if tool == "discovery_get_top_traders":
            return {"success": True, "data": {"traders": [
                {"address": "0xbarely", "returnOnInvestment": 283, "profitAndLoss": 206000,
                 "realizedProfitAndLoss": 821679.97, "averageProfitPerTrade": 7402.52,
                 "traderAgeSeconds": 572 * 86400, "tcsLabel": "ELITE"}]}}
        if tool == "discovery_get_trader_history":
            return {"success": True, "data": {"page_info": {"totalCount": 2}}}
        return {"success": False}


def test_vet_uses_real_closed_count_and_trips_thin():
    # the vet dossier narrates the count as fact → it must be the REAL one (page_info.totalCount), and a
    # 2-closed record trips `thin` despite an ELITE label + 572 active days (111 would have sailed past it).
    d = research.run(_VetHistoryClient(), "vet", addr="0xbarely")["trader"]
    assert d["track_record"]["trades"] == 2
    assert d["reliability"] == "thin"
    assert "thin_track_record" in d["flags"]


class _StateFailClient:
    """discovery_get_top_traders returns one row; the per-trader state + momentum lookups FAIL (success:False)."""
    def mcp_call(self, tool, timeout=12, **kw):
        if tool == "discovery_get_top_traders":
            return {"success": True, "data": {"traders": [
                {"address": "0xfail", "returnOnInvestment": 50, "profitAndLoss": 1000,
                 "realizedProfitAndLoss": 5000, "averageProfitPerTrade": 100,
                 "traderAgeSeconds": 30 * 86400, "tcsLabel": "ELITE"}]}}
        return {"success": False}


def test_failed_state_lookup_is_unknown_not_empty_book():
    # a failed/timed-out state lookup must read as UNKNOWN, never a false empty book (which would flag
    # no_open_positions and sink it below `poor`). This fires on the skill's most common entry — a pasted address.
    c = next(x for x in research.run(_StateFailClient(), "top")["candidates"] if x["address"] == "0xfail")
    assert c["current_positions"] is None                          # book UNKNOWN, not []
    assert "no_open_positions" not in (c.get("flags") or [])
    assert c["mirrorability"]["mirror_fit"] == "unknown"


def test_window_scoped_fields_are_not_backfilled_across_views():
    # roi from one window must not sit beside pnl from another — that made roi_pnl_conflict a false artifact.
    v1 = [{"address": "0xw", "returnOnInvestment": 295, "traderAgeSeconds": 30 * 86400, "tcsLabel": "ELITE"}]  # roi present, pnl absent
    v2 = [{"address": "0xw", "profitAndLoss": -2000, "traderAgeSeconds": 30 * 86400, "tcsLabel": "ELITE"}]     # pnl present (negative)
    w = next(c for c in research.run(_SeqClient([v1, v2, v1]), "top")["candidates"] if c["address"] == "0xw")
    assert w["pnl_usd"] is None                                    # NOT backfilled from the other window
    assert "roi_pnl_conflict" not in (w.get("flags") or [])       # so no false cross-window conflict


def test_heavy_flags_demote_a_fee_bleeder_below_the_copyable_pick():
    # a hyper-active fee-bleeder (one HEAVY flag) must not outrank the actually-copyable trader on flag count.
    bleeder = {"mirrorability": {"mirror_fit": "partial", "fresh_entry_surface_pct": 30},
               "flags": ["high_turnover"], "reliability": "solid", "seen_in": []}
    copyable = {"mirrorability": {"mirror_fit": "good", "fresh_entry_surface_pct": 69},
                "flags": [], "reliability": "ok", "seen_in": []}
    assert sorted([bleeder, copyable], key=research._mirror_sort_key)[0] is copyable


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
