"""One ladder, expressed in PRICE, ported across five leverages — and the clock cuts that are gone.

Every DSL threshold is ROE, and the engine turns ROE into a price floor by DIVIDING BY LEVERAGE
(senpi-strategy-author/references/dsl-configuration.md). So a ladder written in ROE is implicitly
authored for ONE leverage, and the only thing that ports between packages is the ladder expressed as
a percentage of PRICE. Twelve packages now share one such ladder at 3x, 5x, 7x, 8x and 10x.

Why these numbers and not others — all measured on the 4h-leaderboard universe the penguin family
scans, which is also orca's, jaguar's, condor's, raptor's and owl's:

  * median favourable peak per position = 0.84% of price (92 closes, 41 assets). Rung 0 sits at
    0.60% of price so it arms on the majority of positions. It is a LOSS REDUCER, not a profit
    taker: it cannot bank a winner, it stops one that went green from round-tripping to the stop.
  * p75 adverse bounce off a running favourable extreme = 1.38% of price (3-day 1m baseline). The
    hard stop sits at 1.50% of price, OUTSIDE that band. Five packages used to sit inside it —
    jaguar at 0.80%, owl and raptor at 1.00%, orca at 1.14%, condor at 1.20% — which means ordinary
    noise was taking their stops out, and the fix is the same arithmetic in all five.
  * rung 0 at a 30% lock rather than 20%: under 10/20 penguin saw 55% of floor-exits never arm a
    single rung. At 6/30 arming went 48% -> 64% of positions with the stop unchanged (PR #804).

The two PURE CLOCK cuts are off across this family. `hard_timeout` and `dead_weight_cut` fire on
elapsed time and know nothing about the trade: across the fleet, of 142 clock-driven closes in one
week, 110 (77.5%) were in profit when the clock cut them. `weak_peak_cut` is NOT a pure clock cut —
its timer resets whenever ROE clears `min_value`, so it bounds a position that never worked — and
each package's own setting is left alone here (test_penguin_family_drift.py pins the invariant that
makes it safe: min_value below rung 0, so it can only fire in phase 1).

Run: python3 -m pytest strategies/tests/test_one_price_ladder.py -q
"""
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# The canonical ladder, as % of PRICE. Locks are a fraction of high-water and carry no leverage,
# so they are identical at every leverage.
LADDER_PX = [0.60, 2.00, 2.50, 3.00, 4.00, 5.00, 7.00, 10.00]
LADDER_LOCK = [30, 40, 50, 60, 65, 70, 75, 85]
STOP_PX = 1.50

# Packages that must land on LADDER_PX exactly, at whatever leverage they run.
ON_THE_LADDER = ["penguin", "pelican", "purple-penguin", "wild-condor", "penguin-x5", "puffin",
                 "orca", "owl", "jaguar", "condor", "raptor"]

# A wider stop is a THESIS choice and has to be stated, so the default is penguin's 1.50% and
# anything else needs a reason here. A TIGHTER stop than 1.50% on this universe is not listed and
# cannot be: it would sit inside the measured 1.38% p75 bounce.
STOP_PX_EXEMPT = {
    "puffin": (2.50, "a rotation thesis is not falsified by a 1.5% move against it the way a "
                     "momentum jump is — Puffin follows whale/smart-money rotation, not a snap"),
    # These two are INHERITED rather than derived: 10% ROE was carried over from the old phase-1
    # retrace so the per-trade risk cap would not change when trailing was disabled. At 5x it lands
    # at 2.00% of price, comfortably outside the measured 1.38% p75 bounce, so it is safe where it
    # is — and both are live, so tightening them to 1.50% would raise stop-out frequency with no
    # measurement preferring one over the other. Left alone deliberately.
    "cheetah": (2.00, "inherited from the pre-#806 phase-1 retrace; outside the 1.38% band already"),
    "wild-cheetah": (2.00, "inherits cheetah's stop; see cheetah"),
}

# cheetah and wild-cheetah ship rung 2 at `13` ROE on a 5x book = 2.60% of price, where the exact
# value is 12.5. Both are LIVE, the gap is 0.10pp of price, and a redeploy to chase it is not worth
# it — but it is recorded so real drift is still visible.
LADDER_ROUNDING = {
    ("cheetah", 2): 2.60,
    ("wild-cheetah", 2): 2.60,
}
ON_THE_LADDER_ROUNDED = ["cheetah", "wild-cheetah"]

# Deliberately NOT on this ladder, each for a measured reason. Listed so that "port penguin's ladder
# everywhere" is a failing change rather than a silent one.
OFF_THE_LADDER = {
    "pangolin": "draws its universe from market_list_instruments, not the 4h leaderboard, and its "
                "OWN 20 closes measure median peak 0.57% / p75 3.51% / p90 5.39% of price — a far "
                "more skewed shape. Its rung 0 at 2.67% arms on 40% of positions, and its stop at "
                "3.33% is already outside its own band. Porting the 0.60%/1.50% pair here would arm "
                "on noise and scratch the runners.",
    "grizzly": "trades BTC only. Measured 2026-10-01 over 3 days of 1m bars (4,321 bars, 144 30m "
               "windows), BTC's adverse bounce off a running favourable extreme is p50 0.23% / p75 "
               "0.35% / p90 0.51% of price — roughly 4x quieter than the leaderboard universe. Its "
               "stop at 0.80% of price is already above that p90, so penguin's 1.50% would double "
               "the loss per stop-out and buy nothing.",
    "grizzly-wild": "inherits grizzly's exits verbatim; see grizzly.",
}

# Clock cuts must be off for this family. 65 other packages fleet-wide still carry one and are NOT
# claimed here — auditing them needs each one's own hold horizon, so this list grows deliberately.
DSL_ONLY = ON_THE_LADDER + ON_THE_LADDER_ROUNDED + ["pangolin", "grizzly", "grizzly-wild"]
PURE_CLOCK_CUTS = ["hard_timeout", "dead_weight_cut"]


def _rt(pkg):
    with open(os.path.join(ROOT, "strategies", pkg, "main", "runtime.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _leverage(rt):
    """The one leverage this package's ROE ladder is authored for."""
    inp = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]
    levs = {float(t[1]) for t in (inp.get("leverageTiers") or []) if len(t) > 1}
    if len(levs) == 1:
        return levs.pop()
    assert not levs, f"split leverage {sorted(levs)} — a single ROE ladder cannot be correct for all"
    lev = inp.get("leverageDefault") or rt["strategy"].get("default_leverage")
    assert lev, "no leverage found: neither flat leverageTiers, leverageDefault, nor default_leverage"
    return float(lev)


@pytest.mark.parametrize("pkg", ON_THE_LADDER + ON_THE_LADDER_ROUNDED)
def test_every_rung_sits_at_the_canonical_price_distance(pkg):
    rt = _rt(pkg)
    lev = _leverage(rt)
    tiers = rt["exit"]["dsl_preset"]["phase2"]["tiers"]
    assert len(tiers) == len(LADDER_PX), (
        f"{pkg} has {len(tiers)} rungs, not {len(LADDER_PX)}. The ladder is one object — a package "
        f"that drops rungs stops trailing where the others still do.")
    for i, (tier, want_px, want_lock) in enumerate(zip(tiers, LADDER_PX, LADDER_LOCK)):
        got_px = tier["trigger_pct"] / lev
        want = LADDER_ROUNDING.get((pkg, i), want_px)
        assert abs(got_px - want) < 1e-9, (
            f"{pkg} rung {i} arms at {got_px:.4f}% of price; the ladder says {want:.2f}%. Every DSL "
            f"threshold is ROE and price distance is ROE/leverage, so at {lev:g}x this rung must be "
            f"trigger_pct {want * lev:g}. Re-derive it rather than hand-editing the ROE number.")
        assert tier["lock_hw_pct"] == want_lock, (
            f"{pkg} rung {i} locks {tier['lock_hw_pct']}% of high-water, not {want_lock}%. Locks are "
            f"a fraction of high-water and carry no leverage, so they are identical at every "
            f"leverage — a difference here is drift, never a re-derivation.")


@pytest.mark.parametrize("pkg", ON_THE_LADDER + ON_THE_LADDER_ROUNDED)
def test_the_hard_stop_sits_outside_the_measured_bounce_band(pkg):
    """A stop inside the universe's own p75 adverse bounce is taken out by ordinary noise."""
    rt = _rt(pkg)
    lev = _leverage(rt)
    stop_px = rt["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / lev
    want, why = STOP_PX_EXEMPT.get(pkg, (STOP_PX, "penguin's measured stop"))
    assert abs(stop_px - want) < 1e-9, (
        f"{pkg}'s stop is at {stop_px:.4f}% of price, not {want:.2f}% ({why}). At {lev:g}x that is "
        f"max_loss_pct {want * lev:g}. A stop BELOW 1.50% of price on this universe sits inside the "
        f"measured 1.38% p75 adverse bounce and gets hit by noise; a wider one needs a thesis "
        f"reason recorded in STOP_PX_EXEMPT.")


@pytest.mark.parametrize("pkg", ON_THE_LADDER + ON_THE_LADDER_ROUNDED)
def test_rung_zero_is_a_loss_reducer_not_a_profit_taker(pkg):
    """Rung 0 exists to stop a position that went green from round-tripping to the stop.

    That only works if it arms BELOW the universe's median favourable peak (0.84% of price) and its
    floor still sits above entry. A rung 0 further out than the median peak never arms on most
    positions, which is the state orca was in: 2.86% of price, 3.4x the median."""
    rt = _rt(pkg)
    lev = _leverage(rt)
    first = rt["exit"]["dsl_preset"]["phase2"]["tiers"][0]
    arms_at = first["trigger_pct"] / lev
    assert arms_at < 0.84, (
        f"{pkg}'s rung 0 arms at {arms_at:.2f}% of price, at or beyond the measured 0.84% median "
        f"favourable peak — on most positions it never arms at all.")
    assert 0 < first["lock_hw_pct"] < 100, (
        f"{pkg} rung 0 locks {first['lock_hw_pct']}% of high-water. A 0% floor exits flat and still "
        f"pays the round trip (banned fleet-wide); 100% cannot be held.")


@pytest.mark.parametrize("pkg", sorted(OFF_THE_LADDER))
def test_a_deliberate_divergence_stays_divergent(pkg):
    """These fail in BOTH directions on purpose.

    Each is off the canonical ladder because its OWN measured distribution says the canonical
    numbers are wrong for it. Someone "fixing" the inconsistency by porting penguin's ladder would
    be re-introducing the exact error this register exists to prevent, so converging onto the ladder
    fails here just as drifting elsewhere fails above."""
    rt = _rt(pkg)
    lev = _leverage(rt)
    pre = rt["exit"]["dsl_preset"]
    tiers = pre["phase2"]["tiers"]
    px = [t["trigger_pct"] / lev for t in tiers]
    stop_px = pre["phase1"]["max_loss_pct"] / lev
    on_ladder = (len(px) == len(LADDER_PX)
                 and all(abs(a - b) < 1e-9 for a, b in zip(px, LADDER_PX))
                 and abs(stop_px - STOP_PX) < 1e-9)
    assert not on_ladder, (
        f"{pkg} has been converged onto the canonical ladder. It must NOT be: {OFF_THE_LADDER[pkg]} "
        f"If new measurement says otherwise, replace the reason here with that measurement — do not "
        f"just delete the entry.")


@pytest.mark.parametrize("pkg", DSL_ONLY)
@pytest.mark.parametrize("cut", PURE_CLOCK_CUTS)
def test_no_pure_clock_cut_survives_in_the_dsl_only_family(pkg, cut):
    """`hard_timeout` and `dead_weight_cut` fire on elapsed time and read nothing about the trade.

    Across the fleet, of 142 clock-driven closes in one week, 110 (77.5%) were IN PROFIT when the
    clock cut them. A timer cannot tell a thesis that is wrong from one that has not happened yet;
    the ladder and the stop can. (`weak_peak_cut` is excluded on purpose — its timer resets whenever
    ROE clears min_value, so it bounds a DEAD position rather than a slow one.)"""
    pre = _rt(pkg)["exit"]["dsl_preset"]
    enabled = (pre.get(cut) or {}).get("enabled")
    assert enabled in (None, False), (
        f"{pkg} has {cut} enabled. It closes on elapsed time regardless of where the trade is: of "
        f"142 such closes in a week, 110 were in profit when the clock fired. Exits here are the "
        f"profit ladder and the stop.")


def test_the_ladder_is_actually_shared_across_several_leverages():
    """Guard against the ladder becoming vacuous.

    If every package on ON_THE_LADDER drifted to one leverage, the tests above would still pass
    while proving nothing about porting. The value of this invariant is that the SAME price ladder
    holds at several different leverages at once."""
    levs = {_leverage(_rt(p)) for p in ON_THE_LADDER + ON_THE_LADDER_ROUNDED}
    assert len(levs) >= 4, (
        f"the shared ladder now spans only {sorted(levs)}. It is meant to prove one price ladder "
        f"survives re-expression across leverage bands; with fewer than four it proves little.")
