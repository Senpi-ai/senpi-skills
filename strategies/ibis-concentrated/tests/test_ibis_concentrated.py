"""Ibis Concentrated — the risk arithmetic, and why the stop is 16 and not the parent's 8.

WHAT THIS PACKAGE IS. ibis with one slot instead of three. The regime engine is the parent's
byte for byte (test_engine_parity.py); everything asserted here is sizing, stop geometry and
the regime-flip exit.

WHY THE STOP MOVED, AND THE MEASUREMENT BEHIND IT. The parent runs `max_loss_pct: 8` at 4x,
which is a 2.00% PRICE stop. Measured 2026-10-12 over 45 days of 4h candles from the
exchange's own history, across 12 names from the parent's $25M-volume universe:

    median 4h true range  1.65%        p75  2.51%        p90  3.53%
    BTC 0.89 · ETH 1.18 · BNB 0.97 · SOL 1.50 · LTC 1.66 · XRP 1.64 · DOGE 1.63
    LINK 1.87 · HYPE 1.82 · AVAX 2.01 · ADA 2.25 · SUI 2.61

On SUI, ADA and AVAX the MEDIAN bar is wider than the parent's whole stop; on SUI 69% of bars
are. The parent tolerates that because 10-15% margin makes a noise stop-out cost ~1% of the
wallet. At this fork's 44-53% it would cost 7-8%, so concentrating the parent's geometry
unchanged would have bought the same noise at five times the size. 16% ROE at 4x is a 4.00%
price stop — outside the p90 bar on 10 of the 12.

THE TWO INDEPENDENT DIALS. This is the arithmetic the whole design rests on:

    risk per stop  = marginPct x max_loss_pct / 100     (margin only)
    price distance = max_loss_pct / leverage            (leverage only)

So leverage places the stop where a regime's thesis dies and margin prices being wrong. It is
why the fade leg carries MORE leverage (5x, a 3.20% stop ~= 'price left the band') and LESS
money (18% base vs 35%) than the trend leg — not a contradiction, two different dials.

Run: python3 -m pytest strategies/ibis-concentrated/tests/test_ibis_concentrated.py -q
"""
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
PARENT = yaml.safe_load(open(os.path.join(HERE, "..", "..", "ibis", "main", "runtime.yaml"),
                             encoding="utf-8"))

SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"].endswith("_signals"))
FLIP = next(s for s in RUNTIME["scanners"] if s["name"].endswith("_regime_flip"))
IN = SIGNALS["inputs"]
GUARDS = RUNTIME["risk"]["guard_rails"]
PRESET = RUNTIME["exit"]["dsl_preset"]
STOP_ROE = PRESET["phase1"]["max_loss_pct"]

# the measurement in the docstring, as data
MEDIAN_4H_RANGE_PCT = 1.65
P90_4H_RANGE_PCT = 3.53


# ── the floor exists at all ───────────────────────────────────────────────────────────────

def test_the_absolute_floor_is_the_only_thing_under_entry_and_it_is_set():
    """phase1 trailing is off fleet-wide (it can ratchet a winner into a loss), which makes
    max_loss_pct the SOLE floor beneath entry — the runtime rests it on the exchange as a real
    stop-loss. A fork that concentrates the money must not also be the one that drops it."""
    assert PRESET["phase1"]["enabled"] is False, "phase1 trailing must stay off"
    assert STOP_ROE > 0, "max_loss_pct is the only floor under entry once phase1 is off"


# ── the stop clears the noise band, per regime ────────────────────────────────────────────

@pytest.mark.parametrize("key,expected_price_pct", [("leverageTrend", 4.00),
                                                    ("leverageRange", 3.20)])
def test_each_regimes_stop_sits_outside_an_ordinary_4h_bar(key, expected_price_pct):
    price_pct = STOP_ROE / IN[key]
    assert abs(price_pct - expected_price_pct) < 0.01, (
        f"{key}={IN[key]} with max_loss_pct={STOP_ROE} puts the stop {price_pct:.2f}% away in "
        f"price, not the {expected_price_pct}% this package's card and manifest both quote")
    assert price_pct > MEDIAN_4H_RANGE_PCT, (
        f"the stop is {price_pct:.2f}% away but the MEDIAN 4h bar on this universe is "
        f"{MEDIAN_4H_RANGE_PCT}% — an ordinary bar would reach it, which is the parent's "
        f"problem this fork exists to not inherit at size")


def test_the_trend_stop_clears_the_p90_bar_not_merely_the_median():
    """The trend leg carries the most money, so it gets the most room. The fade leg is
    deliberately tighter — its thesis dies when price leaves the band — so only this one is
    held to the p90 bar."""
    assert STOP_ROE / IN["leverageTrend"] > P90_4H_RANGE_PCT, (
        f"the trend stop is {STOP_ROE / IN['leverageTrend']:.2f}% away; the p90 4h bar is "
        f"{P90_4H_RANGE_PCT}%")


def test_the_stop_is_wider_than_the_parents_in_price_on_the_trend_leg():
    """The headline change. If someone 'restores' the parent's 8, this fails rather than
    silently shipping a noise-width stop at 50% margin."""
    p_in = next(s for s in PARENT["scanners"] if s["name"].endswith("_signals"))["inputs"]
    parent_price = PARENT["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / p_in["leverageTrend"]
    mine_price = STOP_ROE / IN["leverageTrend"]
    assert mine_price > parent_price * 1.5, (
        f"this fork's trend stop ({mine_price:.2f}% price) must be materially wider than the "
        f"parent's ({parent_price:.2f}%) — that is the premise of concentrating it")


def test_the_fade_leg_is_tighter_in_price_than_the_trend_leg():
    """A fade's target is the middle of the band; a continuation's is open-ended. The exit
    geometry must reflect that or the two regimes are being treated as one strategy."""
    assert STOP_ROE / IN["leverageRange"] < STOP_ROE / IN["leverageTrend"]


def test_the_fade_stop_is_about_the_width_a_range_must_have_to_be_faded():
    """3.20% against a minRangeWidthPct of 3.0 — the stop is 'price left the band', which is
    the thesis dying, not an arbitrary number. Pinned so a retune of one moves the other."""
    assert STOP_ROE / IN["leverageRange"] >= IN["minRangeWidthPct"], (
        "the fade's stop is now INSIDE the minimum fadeable range width — it would be hit "
        "while the fade thesis is still intact")


# ── the cost of being wrong ───────────────────────────────────────────────────────────────

def _tiers(base):
    """scoring.margin_tier_pct's reachable rungs at this package's minScore."""
    return [base * m for s, m in ((8, 1.5), (6, 1.25), (0, 1.0)) if s >= IN["minScore"] or s == 8]


def test_the_worst_single_stop_costs_what_the_card_says():
    worst_trend = IN["marginPctBaseTrend"] * 1.5
    cost = worst_trend / 100 * STOP_ROE
    assert abs(worst_trend - 52.5) < 0.01
    assert abs(cost - 8.4) < 0.05, (
        f"one trend stop now costs {cost:.2f}% of the wallet, not the ~8.4% the manifest "
        "header, the card and this file all quote — they move together or not at all")


def test_the_fade_costs_materially_less_than_the_trend():
    trend = IN["marginPctBaseTrend"] * 1.5 / 100 * STOP_ROE
    fade = IN["marginPctBaseRange"] * 1.5 / 100 * STOP_ROE
    assert fade < trend * 0.6, (
        f"the fade leg costs {fade:.2f}% per stop against the trend's {trend:.2f}% — the "
        "lower-conviction regime is supposed to risk about half as much")


def test_the_halt_absorbs_more_than_one_worst_case_stop():
    worst = IN["marginPctBaseTrend"] * 1.5 / 100 * STOP_ROE
    absorbed = GUARDS["drawdown_halt_pct"] / worst
    assert absorbed >= 2.0, (
        f"the drawdown halt absorbs {absorbed:.1f} stops — under two, the first loss of a "
        "concentrated book effectively ends it")
    assert GUARDS["drawdown_halt_pct"] > PARENT["risk"]["guard_rails"]["drawdown_halt_pct"], (
        "the per-trade loss is larger than the parent's, so the halt has to be larger too or "
        "it trips on the second stop")
    assert GUARDS["drawdown_reset_on_day_rollover"] is False


def test_consecutive_losses_are_capped_tighter_than_the_parent():
    assert GUARDS["max_consecutive_losses"] <= PARENT["risk"]["guard_rails"]["max_consecutive_losses"]
    assert GUARDS["max_consecutive_losses"] * (IN["marginPctBaseTrend"] * 1.5 / 100 * STOP_ROE) \
        <= GUARDS["drawdown_halt_pct"], (
        "the consecutive-loss cap lets the book lose more than the drawdown halt allows — one "
        "of the two guards is dead weight")


# ── it is actually concentrated ───────────────────────────────────────────────────────────

def test_it_takes_one_position_at_a_time():
    assert RUNTIME["strategy"]["slots"] == 1
    assert IN["maxSlots"] == 1
    assert MANIFEST["catalog"]["max_slots"] == 1
    assert GUARDS["max_entries_per_day"] <= 2, "one slot on a 4h clock is not a day-trading book"


def test_it_is_strictly_more_concentrated_than_the_parent():
    p = next(s for s in PARENT["scanners"] if s["name"].endswith("_signals"))["inputs"]
    assert RUNTIME["strategy"]["slots"] < PARENT["strategy"]["slots"]
    assert IN["marginPctBaseTrend"] > p["marginPctBase"], (
        "the whole point is more money behind one idea")


def test_the_score_floor_is_raised_so_one_slot_is_not_spent_on_a_mediocre_setup():
    p = next(s for s in PARENT["scanners"] if s["name"].endswith("_signals"))["inputs"]
    assert IN["minScore"] > p["minScore"]


def test_the_per_regime_margin_keys_are_both_present():
    """scan.py falls back to marginPctBase when these are missing — a silent de-concentration
    that would look like it was working. Assert the shipped config states both."""
    assert "marginPctBaseTrend" in IN and "marginPctBaseRange" in IN


# ── the regime-flip exit, which is what makes one slot defensible ─────────────────────────

def test_a_regime_flip_close_scanner_is_wired_to_a_close_action():
    close = [a for a in RUNTIME["actions"] if a["action_type"] == "CLOSE_POSITION"]
    assert len(close) == 1, "the regime-flip exit is the fork's answer to a one-slot book"
    assert close[0]["scanners"] == [FLIP["name"]]
    assert close[0]["context"][0]["scanner"] == FLIP["name"]
    assert FLIP["entrypoint"] == "close_regime_flip.py"


def test_the_flip_requires_confirmation_rather_than_firing_on_one_tick():
    assert FLIP["inputs"]["flipConfirmTicks"] >= 2, (
        "a single contradicting tick is not a flip — an ER oscillating across "
        "trendErThreshold would churn the position and pay fees each time")


def test_the_close_scanner_shares_the_entry_scanners_regime_vocabulary():
    """Two scanners disagreeing about what regime a position is in is the worst outcome
    available: one opens on TREND while the other reads RANGE and closes it. Every shared
    classification value must therefore match — except the one asymmetry below."""
    for k in ("erLookback", "rangeErThreshold", "rangeBars",
              "rangeBuyBelowPct", "rangeSellAbovePct", "extremeFundingApr"):
        assert FLIP["inputs"][k] == IN[k], (
            f"{k} differs between the entry scanner and the regime-flip scanner — they would "
            "classify the same market differently")


def test_the_flip_detector_is_never_less_willing_to_see_a_trend_than_the_entry_gate():
    """The one deliberate asymmetry, and the direction matters more than the values.

    Entry asks "is this a trend worth backing" — measured at ER 0.45. Invalidation asks "is
    this still a trend at all", and a LOWER threshold makes the detector MORE alert: a position
    opened at ER 0.60 whose ER decays to 0.40 while structure flips against it reads as
    TREND-with-flipped-structure and is closed. If this threshold were raised to match entry,
    that same position would read UNCLEAR — which `_contradiction` treats as "no verdict" — and
    the detector would go blind to a flip on a position its own strategy opened.

    So: <=, never >. Equality is allowed (it is the parent's arrangement); above is a bug."""
    assert FLIP["inputs"]["trendErThreshold"] <= IN["trendErThreshold"], (
        f"the flip detector's trendErThreshold ({FLIP['inputs']['trendErThreshold']}) is ABOVE "
        f"the entry gate's ({IN['trendErThreshold']}) — positions opened on a strong trend "
        "become UNCLEAR as their ER decays, and UNCLEAR is not a contradiction, so the flip "
        "that should close them is never seen")


def test_the_entry_gate_sits_where_the_measured_edge_is_not_where_the_parent_put_it():
    """The fix from the 2026-10-12 replay. Over 200 days / 3,424 signals net of fees, the
    trend leg's expectancy by ER band was -0.75% (0.36-0.40), -0.76% (0.40-0.45), +0.25%
    (0.45-0.50), +1.35% (0.50-0.55), rising to +7.34% above 0.70 — so the parent's 0.36 admits
    two net-NEGATIVE bands that were ~58% of its trend entries. Simulated on a single wallet
    the whole package went from -0.09% to +1.64% ROE per entry for six fewer entries in 200
    days, because one slot (not signal supply) is the binding constraint.

    Pinned against the PARENT rather than as a literal, so the relationship survives a retune
    of either; and floored at 0.45, because below that the negative bands come back."""
    p_in = next(s for s in PARENT["scanners"] if s["name"].endswith("_signals"))["inputs"]
    assert IN["trendErThreshold"] > p_in["trendErThreshold"], (
        "the entry gate is back at or below the parent's, which re-admits the ER bands measured "
        "net-negative after fees — at 44-53% margin, not 10-15%")
    assert IN["trendErThreshold"] >= 0.45, (
        f"the entry gate is {IN['trendErThreshold']}, under the 0.45 the replay supports")
    assert IN["trendErThreshold"] <= IN["strongErThreshold"], (
        "the gate has passed strongErThreshold, so the +2 ER rung is now unreachable and the "
        "ER term has stopped discriminating at all — re-derive both together")


def test_the_flip_scanner_does_not_poll_faster_than_the_bars_it_measures():
    """A 4h regime cannot flip faster than its candles; a faster poll is only extra reads, and
    on a 4h lookback it would also re-read the same unchanged bar."""
    assert FLIP["interval_seconds"] >= SIGNALS["interval_seconds"]


# ── exits stay price- and thesis-based, never a clock ─────────────────────────────────────

def test_no_time_cut_closes_anything():
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert PRESET[cut]["enabled"] is False, (
            f"{cut} is enabled — 110 of 142 clock closes measured across the fleet were in "
            "PROFIT. Thesis death is the regime-flip scanner's job; it exits on a reason.")


def test_the_profit_tiers_are_the_parents_unchanged():
    assert PRESET["phase2"]["tiers"] == PARENT["exit"]["dsl_preset"]["phase2"]["tiers"], (
        "the tiers are ROE-based and the trend leg keeps the parent's leverage, so leaving "
        "them alone keeps the parent's exact PRICE distances on that leg — a change here has "
        "to be re-derived in price and documented in the exit block")


def test_no_tier_locks_a_breakeven():
    for t in PRESET["phase2"]["tiers"]:
        assert t["lock_hw_pct"] > 0, "a breakeven exit still pays fees, so a scratch is a loss"


def test_phase2_tiers_are_present_and_monotonic():
    tiers = PRESET["phase2"]["tiers"]
    assert tiers, "phase2.tiers must exist even when empty"
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert [t["lock_hw_pct"] for t in tiers] == sorted(t["lock_hw_pct"] for t in tiers)


def test_the_first_lock_arms_before_the_stop_is_reached_in_roe_terms():
    """Not a law of nature, but if the first profit lock sat further out in ROE than the stop,
    the book could only ever exit at the floor or a tier it rarely reaches."""
    assert PRESET["phase2"]["tiers"][0]["trigger_pct"] <= STOP_ROE


# ── the manifest tells the truth ──────────────────────────────────────────────────────────

def test_the_manifest_declares_itself_a_variant_of_ibis():
    assert MANIFEST["catalog"]["varies"] == "ibis"
    assert MANIFEST["catalog"]["risk_level"] == "aggressive", (
        "44-53% of a wallet on one 4x position is not 'moderate' like the parent")
    assert MANIFEST["catalog"]["tier"] == "advanced"


def test_the_wallet_env_is_its_own():
    """Sharing the parent's env var would point both packages at one wallet."""
    assert MANIFEST["instances"][0]["wallet_env"] == "IBIS_CONCENTRATED_WALLET"
    assert "${IBIS_CONCENTRATED_WALLET}" in RUNTIME["strategy"]["wallet"]


def test_the_quoted_leverage_ceiling_matches_the_scanner():
    assert MANIFEST["catalog"]["leverage_max"] == IN["leverageMax"]
    assert IN["leverageTrend"] <= IN["leverageMax"]
    assert IN["leverageRange"] <= IN["leverageMax"]
