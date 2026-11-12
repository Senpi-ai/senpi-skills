"""Shadow watched a hall of fame, not a desk.

The cohort was auto-selected as the TOP 3 by ALL-TIME realized PnL. Measured live on
2026-10-09, those three had last traded 45.8, 50.1 and 7.4 days ago, and four of the top five
had `activityLabel: null` because they are not active enough to be classified. This strategy
mirrors opens younger than 240 seconds. One deployed instance ran 194 clean ticks and produced
zero candidates in six hours — and because every print in the scanner was on a failure path, it
was indistinguishable from working-and-waiting.

Every field needed to prevent that was already in the `discovery_get_top_traders` row and none
of them were read: `lastTradeTimestamp`, `averageTradesPerDay`, `averageHoldTimeSeconds`,
`winRate`, `gainToPainRatio`, `activityLabel`.

This file pins the filter chain, the ranking, the refusals, and the log line — in particular that
a cohort too small to confirm anything is REFUSED rather than silently watched.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402

NOW = time.time()
H = 3600.0


def _trader(addr, pnl=500_000, last_trade_h=1.0, tpd=20.0, hold=7200.0,
            wr=80.0, g2p=5.0, dd=-20.0):
    """`hold=None` omits averageHoldTimeSeconds entirely — the API returns null for "not
    computed" and 0 for "closes instantly", and those must not collapse together."""
    row = {"address": addr, "realizedProfitAndLoss": pnl,
           "lastTradeTimestamp": (NOW - last_trade_h * H) if last_trade_h is not None else 0,
           "averageTradesPerDay": tpd, "winRate": wr, "gainToPainRatio": g2p, "maxDrawdown": dd}
    if hold is not None:
        row["averageHoldTimeSeconds"] = hold
    return row


def _addr(n):
    return "0x" + f"{n:040x}"


class _MCP:
    def __init__(self, traders):
        self.traders = traders
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "discovery_get_top_traders":
            return {"data": {"traders": self.traders}}
        return None


class _Ctx:
    def __init__(self, traders):
        self.senpi_mcp = _MCP(traders)
        self.state = None


def _stub_blacklist(flagged=(), fail=False):
    """No test may reach the real GetDiscoveryBlacklist. Returns the previous callable."""
    prev = scan.blacklist.filter_out
    low = {a.lower() for a in flagged}

    def _fake(addresses, **_kw):
        if fail:
            raise scan.blacklist.BlacklistUnavailable("stubbed outage")
        kept = [a for a in addresses if a.lower() not in low]
        return kept, [a for a in addresses if a.lower() in low]

    scan.blacklist.filter_out = _fake
    return prev


def _resolve(traders, inputs=None, cached=None, flagged=(), blacklist_down=False):
    prev = _stub_blacklist(flagged, blacklist_down)
    try:
        return _resolve_inner(traders, inputs, cached)
    finally:
        scan.blacklist.filter_out = prev


def _shipped_inputs():
    """The REAL inputs from runtime.yaml, not a copy. A hand-maintained copy here drifted from
    what ships the first time the thresholds moved, and two tests passed against values nobody
    was deploying."""
    import yaml
    with open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8") as fh:
        rt = yaml.safe_load(fh)
    return dict(next(s for s in rt["scanners"]
                     if s.get("type") == "external_scanner")["inputs"])


SHIPPED = _shipped_inputs()


def _resolve_inner(traders, inputs=None, cached=None):
    ctx = _Ctx(traders)
    base = dict(SHIPPED)
    base.update(inputs or {})
    addrs, cohort, note = scan._resolve_cohort(ctx, cached or {}, base, NOW)
    return addrs, cohort, note, ctx.senpi_mcp.calls


def _healthy(n, **kw):
    return [_trader(_addr(i), **kw) for i in range(1, n + 1)]


# ──────────────────────────────────────────────────────────── the window and the floor

def test_the_cohort_is_ranked_over_a_recent_window_not_all_time():
    """ALL_TIME is a hall of fame: its top two had last traded 46 and 50 days ago."""
    _a, _c, _n, calls = _resolve(_healthy(6))
    name, args = calls[0]
    assert name == "discovery_get_top_traders"
    assert args["time_frame"] == "WEEKLY", f"ranked over {args['time_frame']}"
    assert args["sort_by"] == "PROFIT_AND_LOSS_REALIZED"
    assert args["open_position_filter"] is True, (
        "a mirror has nothing to copy from a trader holding nothing")


def test_a_trader_below_the_weekly_floor_is_dropped():
    addrs, _c, note, _ = _resolve(_healthy(4) + [_trader(_addr(99), pnl=50_000)])
    assert _addr(99) not in addrs
    assert "PnL<" in note


# ──────────────────────────────────────────────────────────── ACTIVE, not dormant

def test_a_dormant_trader_is_dropped_however_profitable():
    """The whole bug. $50M lifetime means nothing if they last traded 46 days ago."""
    dormant = _trader(_addr(50), pnl=50_000_000, last_trade_h=46 * 24)
    addrs, _c, note, _ = _resolve(_healthy(4) + [dormant])
    assert _addr(50) not in addrs, "a trader dormant for 46 days is still being watched"
    assert "dormant" in note


def test_a_trader_with_no_last_trade_timestamp_is_treated_as_dormant():
    """"We cannot tell when they last traded" is not a reason to follow them."""
    unknown = _trader(_addr(51), last_trade_h=None)
    addrs, _c, _n, _ = _resolve(_healthy(4) + [unknown])
    assert _addr(51) not in addrs


def test_a_trader_just_inside_the_window_is_kept():
    addrs, _c, _n, _ = _resolve(_healthy(4) + [_trader(_addr(52), last_trade_h=47.0)])
    assert _addr(52) in addrs


# ──────────────────────────────────────────────────────────── market makers

def test_a_quote_machine_is_dropped():
    """The live weekly cohort carried traders at 736, 515 and 488 trades/day — but frequency
    alone is NOT the test, because six of those turned out to hold for hours. The MM profile is
    high frequency AND an instant round-trip: 489/day with a 0-second hold."""
    mm = _trader(_addr(60), tpd=489.0, hold=0.0)
    addrs, _c, note, _ = _resolve(_healthy(4) + [mm])
    assert _addr(60) not in addrs, "a 0-second-hold quote machine is being mirrored"
    assert "market-maker-like" in note


def test_the_extreme_frequency_backstop_fires_regardless_of_hold():
    """Above the backstop nothing else matters — an account reporting a long hold at 1,600
    trades/day is reporting something inconsistent, and a mirror should not be guessing."""
    addrs, _c, _n, _ = _resolve(_healthy(4) + [_trader(_addr(67), tpd=1629.0, hold=90000.0)])
    assert _addr(67) not in addrs


def test_a_seconds_scale_holder_is_dropped_on_hold_time():
    addrs, _c, _n, _ = _resolve(_healthy(4) + [_trader(_addr(61), hold=45.0)])
    assert _addr(61) not in addrs


def test_a_zero_second_hold_IS_the_market_maker_signature():
    """This case was inverted until the live funnel ran. Treating a reported 0 as "unknown, do
    not punish" let a scalper at 178 trades/day with a ZERO-second hold into the cohort — the one
    profile a fresh-entry mirror can never fill alongside."""
    addrs, _c, _n, _ = _resolve(_healthy(4) + [_trader(_addr(62), tpd=178.6, hold=0.0)])
    assert _addr(62) not in addrs, "a 0-second-hold scalper is in the cohort"


def test_a_MISSING_hold_time_falls_back_to_a_tighter_frequency_cut():
    """null is genuinely unknown, so frequency has to carry the decision there — but at a much
    tighter threshold than the extreme backstop, because we cannot see the hold."""
    addrs, _c, _n, _ = _resolve(_healthy(4) + [_trader(_addr(64), tpd=20.0, hold=None)])
    assert _addr(64) in addrs, "a low-frequency trader with no hold reported was dropped"
    addrs, _c, _n, _ = _resolve(_healthy(4) + [_trader(_addr(65), tpd=515.0, hold=None)])
    assert _addr(65) not in addrs, "515 trades/day with no hold reported was kept"


def test_a_high_frequency_trader_with_LONG_holds_is_kept():
    """The error the live funnel caught: a flat 200/day cut removed six real discretionary
    traders, including one at 736 trades/day holding for 32 hours with $1.13M realized."""
    addrs, _c, _n, _ = _resolve(
        _healthy(4) + [_trader(_addr(66), tpd=736.0, hold=116419.0, pnl=1_130_000)])
    assert _addr(66) in addrs, "frequency alone is still disqualifying real traders"


# Replayed from the live funnel over 200 WEEKLY rows, 2026-10-09. tpd / hold / is-market-maker.
LIVE_MM_ROWS = [
    (178.6, 0.0, True), (1629.0, 297.0, True), (1025.0, 0.0, True), (489.0, 0.0, True),
    (436.0, 0.0, True), (465.0, 0.0, True), (515.0, None, True), (382.0, None, True),
    (736.0, 116419.0, False), (433.0, 158347.0, False), (293.0, 121939.0, False),
    (587.0, 69772.0, False), (777.0, 178679.0, False), (870.0, 20576.0, False),
    (187.0, 47313.0, False),
]


def test_the_market_maker_test_matches_the_live_funnel():
    """Every row the live run classified, pinned. 15/15 — the thresholds were DERIVED from this,
    so if a future edit moves them this is the table that says what it costs."""
    wrong = []
    for i, (tpd, hold, expect_mm) in enumerate(LIVE_MM_ROWS):
        a = _addr(500 + i)
        addrs, _c, _n, _ = _resolve([_trader(a, tpd=tpd, hold=hold)],
                                    inputs={"minCohortSize": 1, "maxWatched": 50,
                                            "minTraderWinRatePct": 0, "minTraderGainToPain": 0})
        got_mm = a not in addrs
        if got_mm != expect_mm:
            wrong.append(f"tpd={tpd} hold={hold}: got {'MM' if got_mm else 'keep'}, "
                         f"expected {'MM' if expect_mm else 'keep'}")
    assert not wrong, "the market-maker test drifted from the live funnel:\n  " + "\n  ".join(wrong)


def test_a_hand_pinned_address_is_honoured():
    """Distinct from Senpi's blacklist below — this is the operator's own pin list, and the tick
    line reports the two separately so you can tell which cut removed whom."""
    addrs, _c, note, _ = _resolve(
        _healthy(4) + [_trader(_addr(63))],
        inputs={"excludeTraderAddresses": [_addr(63).upper()]})      # case-insensitive
    assert _addr(63) not in addrs
    assert "1 pinned out" in note


# ──────────────────────────────────────────────────────────── quality, and the ranking

def test_quality_floors_drop_a_coin_flipper():
    addrs, _c, note, _ = _resolve(_healthy(4) + [_trader(_addr(70), wr=31.0, g2p=0.4)])
    assert _addr(70) not in addrs
    assert "quality" in note


def test_survivors_are_ranked_by_gain_to_pain_not_by_pnl():
    """Ranking by raw realized PnL is what put three near-total-drawdown whales at the top."""
    rows = [_trader(_addr(1), pnl=9_000_000, g2p=2.0),
            _trader(_addr(2), pnl=200_000, g2p=90.0),
            _trader(_addr(3), pnl=5_000_000, g2p=40.0),
            _trader(_addr(4), pnl=150_000, g2p=4.0)]
    addrs, _c, _n, _ = _resolve(rows)
    assert addrs[0] == _addr(2), f"best gain-to-pain is not first: {addrs}"
    assert addrs[-1] == _addr(1), "the biggest PnL with the worst ratio is not last"


def test_the_watch_list_is_capped_but_wide_enough_to_confirm():
    addrs, _c, _n, _ = _resolve(_healthy(30), inputs={"maxWatched": 10})
    assert len(addrs) == 10, "a cohort of three cannot confirm anything"


# ──────────────────────────────────────────────────────────── the refusals

def test_a_cohort_too_small_to_confirm_is_refused_not_watched():
    """Silence is indistinguishable from breakage, so refuse loudly instead of watching two."""
    addrs, _c, note, _ = _resolve(_healthy(2))
    assert addrs == [], f"operating on a cohort of 2: {addrs}"
    assert "below minCohortSize" in note


def test_a_failed_refresh_keeps_the_previous_cohort_and_says_so():
    """`cached.get("addrs", [])` is EMPTY on a fresh deploy, so a single failed read on tick one
    used to produce a permanently empty cohort with no log line at all."""
    cached = {"addrs": [_addr(i) for i in range(1, 6)], "cache_version": scan.CACHE_VERSION,
              "refreshed_at": NOW - 30 * H}
    addrs, _c, note, _ = _resolve([], cached=cached)
    assert addrs == cached["addrs"], "a failed refresh threw the working cohort away"
    assert "keeping" in note


def test_explicit_addresses_bypass_selection_entirely():
    addrs, _c, note, calls = _resolve(
        _healthy(6), inputs={"traderAddresses": [_addr(1), _addr(2)], "maxWatched": 10})
    assert addrs == [_addr(1), _addr(2)]
    assert calls == [], "an explicit cohort must not hit the ranking at all"
    assert "explicit" in note


def test_a_fresh_cohort_is_cached_with_the_bumped_version():
    """CACHE_VERSION moved so the dormant list cached under v1 is re-resolved, not carried 24h."""
    _a, cohort, _n, _ = _resolve(_healthy(6))
    assert cohort["cache_version"] == scan.CACHE_VERSION == 2
    assert cohort["refreshed_at"] == NOW


def test_a_valid_cache_is_reused_without_a_read():
    cached = {"addrs": [_addr(i) for i in range(1, 7)], "cache_version": scan.CACHE_VERSION,
              "refreshed_at": NOW - 2 * H}
    addrs, _c, note, calls = _resolve(_healthy(6), cached=cached)
    assert addrs == cached["addrs"] and calls == []
    assert "cached cohort" in note


def test_the_note_names_every_rejection_reason():
    """This string is the whole observability fix — it is printed every tick."""
    rows = (_healthy(4)
            + [_trader(_addr(80), pnl=1_000)]                    # PnL
            + [_trader(_addr(81), last_trade_h=400)]             # dormant
            + [_trader(_addr(82), tpd=900)]                      # MM
            + [_trader(_addr(83), wr=10, g2p=0.1)])              # quality
    _a, _c, note, _ = _resolve(rows)
    for token in ("WEEKLY", "PnL<", "dormant", "market-maker-like", "quality", "pinned out",
                  "market-maker blacklist"):
        assert token in note, f"the tick line never mentions {token}: {note}"




# ──────────────────────────────────────────────────────────── Senpi's market-maker blacklist

def test_a_wallet_on_senpis_market_maker_blacklist_is_dropped():
    """The authoritative cut. Vignesh's GetDiscoveryBlacklist, the same client quant-desk uses."""
    rows = _healthy(6)
    addrs, _c, note, _ = _resolve(rows, flagged=[_addr(3)])
    assert _addr(3) not in addrs, "a blacklisted market maker is still in the cohort"
    assert "1 on Senpi's market-maker blacklist" in note


def test_an_unreachable_blacklist_fails_OPEN_but_says_UNSCREENED():
    """A cohort we could not screen beats no cohort — but it must never read as clean. The
    blacklist has been answering 401/500, so this is the common path, not the rare one."""
    addrs, cohort, note, _ = _resolve(_healthy(6), blacklist_down=True)
    assert len(addrs) == 6, "a blacklist outage emptied the cohort"
    assert note.startswith("UNSCREENED "), f"an unscreened cohort reads as clean: {note}"
    assert cohort["mm_screened"] is False


def test_a_screened_cohort_is_recorded_as_screened():
    _a, cohort, note, _ = _resolve(_healthy(6))
    assert cohort["mm_screened"] is True
    assert not note.startswith("UNSCREENED")


def test_the_screen_can_be_turned_off():
    addrs, _c, _n, _ = _resolve(_healthy(6), inputs={"screenMarketMakers": False},
                                flagged=[_addr(3)])
    assert _addr(3) in addrs, "screenMarketMakers: false still screened"


def test_the_heuristic_cut_survives_a_blacklist_outage():
    """The reason there are two layers: when the service is down the numbers still work."""
    rows = _healthy(5) + [_trader(_addr(90), tpd=900.0, hold=0.0)]
    addrs, _c, note, _ = _resolve(rows, blacklist_down=True)
    assert _addr(90) not in addrs, "a 0-hold quote machine got in during a blacklist outage"
    assert "market-maker-like" in note


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"SHADOW ACTIVE COHORT OK — {len(fns)} checks")
