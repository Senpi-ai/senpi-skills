"""The Striker scorer is vendored FOUR times, and purple-penguin is a one-input fork.

Both facts are load-bearing and neither was pinned, which is why this file exists.

  1. **Four byte-identical copies of `scoring.py`.** penguin, pelican and orca each ship their
     own copy, and `senpi-signals/scripts/striker_scoring.py` is a fifth surface on the skill
     side. They were byte-identical on 2026-09-30 and nothing said so. A fix applied to one and
     not the others is a silent divergence in the entry model of three live templates plus a
     skill — the exact shape of the two-copy bugs this tree keeps rediscovering.

  2. **purple-penguin differs from penguin in ONE input.** It is an experiment whose whole
     validity rests on being penguin with `minScore: 8` and nothing else. The moment a second
     thing drifts, a difference in its results stops being attributable to the score floor and
     the experiment is worthless. This compares PARSED yaml, so comments and prose are free to
     differ; only effective config is pinned.

Run: python3 -m pytest strategies/tests/test_penguin_family_drift.py -q
"""
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

SCORER_COPIES = [
    "strategies/penguin/main/scanners/scoring.py",
    "strategies/pelican/main/scanners/scoring.py",
    "strategies/orca/main/scanners/scoring.py",
    "strategies/purple-penguin/main/scanners/scoring.py",
    # penguin-x5 and penguins-duo vendor the same scorer. penguin-x5 was byte-identical but NOT
    # listed here, so nothing would have caught it drifting — added 2026-10-03.
    "strategies/penguin-x5/main/scanners/scoring.py",
    "strategies/penguins-duo/main/scanners/scoring.py",
    "strategies/pelicans-duo/main/scanners/scoring.py",
    "senpi-signals/scripts/striker_scoring.py",
]


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _yaml(rel):
    return yaml.safe_load(_read(rel))


def test_every_vendored_striker_scorer_is_byte_identical():
    """A change to the entry model must land on all five surfaces or none."""
    base = SCORER_COPIES[0]
    ref = _read(base)
    for rel in SCORER_COPIES[1:]:
        assert _read(rel) == ref, (
            f"{rel} has drifted from {base}. These are vendored copies of one scorer — port the "
            f"change to every path in SCORER_COPIES in the same commit, or the entry model of "
            f"three live templates and the senpi-signals skill silently disagree.")


def test_the_score_floor_is_reachable_from_runtime_yaml():
    """`minScore` must be the authoritative floor, not shadowed by the module constant.

    It WAS shadowed: score_market applied a hardcoded STRIKER_MIN_SCORE and returned None below
    it, so a package setting minScore lower had no effect whatsoever. purple-penguin exists only
    because that is now fixed, so this pins the fix rather than the value."""
    scorer = _read(SCORER_COPIES[0])
    assert "floor=True" in scorer, (
        "score_market lost its `floor` kwarg — without it a caller cannot see sub-floor "
        "candidates and the CAND telemetry goes blind below the floor")
    assert "passedFloor" in scorer, "meta lost passedFloor, the precomputed floor verdict"
    for pkg in ("penguin", "pelican"):
        scan = _read(f"strategies/{pkg}/main/scanners/scan.py")
        assert "floor=False" in scan, f"{pkg}/scan.py no longer asks for sub-floor candidates"
        assert "score < min_score" in scan, (
            f"{pkg}/scan.py must enforce the floor itself once it passes floor=False — "
            f"otherwise sub-floor candidates would TRADE")


def test_purple_penguin_differs_from_penguin_in_exactly_one_input():
    """The experiment is only interpretable if minScore is the sole effective difference."""
    peng = _yaml("strategies/penguin/main/runtime.yaml")
    purp = _yaml("strategies/purple-penguin/main/runtime.yaml")

    # identity fields are expected to differ; everything else must not
    for k in ("name", "group", "version", "description"):
        peng.pop(k, None)
        purp.pop(k, None)
    peng["strategy"].pop("wallet", None)
    purp["strategy"].pop("wallet", None)

    ps = next(s for s in peng["scanners"] if s.get("type") == "external_scanner")
    qs = next(s for s in purp["scanners"] if s.get("type") == "external_scanner")
    assert ps["inputs"]["minScore"] == 9, "penguin's shipped floor moved; update this test"
    assert qs["inputs"]["minScore"] == 8, (
        f"purple-penguin's whole reason to exist is minScore 8; found {qs['inputs']['minScore']}")
    ps["inputs"].pop("minScore")
    qs["inputs"].pop("minScore")

    assert purp == peng, (
        "purple-penguin diverges from penguin beyond minScore. Every other difference destroys "
        "the experiment: a result could no longer be attributed to the score floor. Diff the two "
        "runtime.yaml files and revert anything that is not minScore.")


def test_purple_penguin_stays_out_of_the_catalog():
    """It is an unmeasured 8-floor on a 90%-margin 10x book. Users must not be offered it."""
    card = _yaml("strategies/purple-penguin/strategy.yaml")
    assert card["catalog"].get("status") == "blocked", (
        "purple-penguin lost catalog.status: blocked — gen_catalog would publish it to the "
        "catalog AND to senpi-strategy-discover, offering an untested score floor to users. "
        "Deploy it by explicit path instead; deploy.py honours a path regardless of status.")


# ── the leverage/ROE coherence invariant (cheetah, 2026-09-30) ──

def test_a_variable_leverage_book_cannot_carry_a_fixed_roe_ladder():
    """Every DSL threshold is ROE, so an exit's distance IN PRICE is (ROE / leverage).

    A package that scales conviction through LEVERAGE therefore rescales its entire exit ladder
    per trade — and backwards: cheetah's old 3/5/7/8 tiers gave a score-10 signal a 3.33%-of-price
    stop with its first profit floor 6.67% away, while a score-14 signal got 1.25% and 2.50%. The
    weakest signal was handed the widest stop and the most distant floor, and against a measured
    median peak of 0.84% of price the ladder never armed at any tier.

    The fix was to scale conviction through MARGIN and hold leverage flat. This pins that: if a
    package's tiers ever spread leverage again, one ROE ladder can no longer be correct for all
    of its trades."""
    for pkg in COHERENT:
        rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
        sc = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")
        tiers = sc["inputs"].get("leverageTiers") or []
        levs = {int(t[1]) for t in tiers}
        assert len(levs) == 1, (
            f"{pkg} spreads leverage across {sorted(levs)} while its DSL ladder is fixed in ROE, "
            f"so every exit means a different price move per tier. Scale conviction through the "
            f"third tier element (marginPct) and keep leverage flat.")
        assert all(len(t) > 2 for t in tiers), (
            f"{pkg} tiers lost their marginPct element — conviction is no longer sized at all")

def test_weak_peak_cut_can_only_fire_before_the_ladder_arms():
    """`weak_peak_cut` closes when peakROE never reached `min_value` and current ROE has fallen
    off that peak. Entering phase 2 implies peakROE >= the first tier's `trigger_pct`, so while
    `min_value < trigger_pct` the guard is unsatisfiable once a rung arms — the cut can only ever
    fire in phase 1 (senpi-strategy-author/references/dsl-configuration.md).

    That is the whole reason it is safe to leave enabled: it bounds a position that NEVER worked,
    and cannot touch one that did. Raise `min_value` above the first trigger, or drop the first
    trigger below `min_value`, and it silently becomes able to close a position that already armed
    a profit floor — a death cut turning into a profit cut without anyone changing its settings."""
    for pkg in ("condor", "wild-condor", "penguin", "pelican", "puffin", "purple-penguin"):
        rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
        preset = rt["exit"]["dsl_preset"]
        wpc = preset.get("weak_peak_cut") or {}
        if not wpc.get("enabled"):
            continue
        first_trigger = preset["phase2"]["tiers"][0]["trigger_pct"]
        assert wpc["min_value"] < first_trigger, (
            f"{pkg}: weak_peak_cut min_value {wpc['min_value']} is not below the first tier "
            f"trigger {first_trigger}, so it can fire AFTER the ladder has armed — closing a "
            f"position that reached a profit floor. Keep min_value strictly under the first rung.")


# ── the "wild" variants: conviction and size move TOGETHER ──

WILD_PAIRS = [("wild-condor", "condor"), ("wild-cheetah", "cheetah"),
              ("grizzly-wild", "grizzly")]


def _sizing(pkg):
    rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
    inp = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]
    tiers = inp.get("leverageTiers") or []
    margins = [t[2] for t in tiers if len(t) > 2] or [inp.get("marginPct")]
    return float(inp["minScore"]), max(float(m) for m in margins)


def test_a_wild_variant_raises_the_bar_and_the_size_together():
    """The point of a `wild-` package is NOT to trade bigger. It is to trade bigger ONLY on what
    already cleared a higher bar.

    Raising margin alone is leverage with extra steps — the same signal distribution, more money
    behind each one, and a worse expectancy per dollar the moment the marginal signal is weak.
    Raising the floor alone leaves a rarer, better signal paid exactly what a common one was. The
    two only make sense as one change, so this fails if either half is ever walked back on its own.

    (Deliberately NOT asserted of purple-penguin: that one LOWERS the floor to measure an
    unmeasured band, which is a different experiment with its own justification.)"""
    for wild, parent in WILD_PAIRS:
        w_score, w_margin = _sizing(wild)
        p_score, p_margin = _sizing(parent)
        assert w_score > p_score, (
            f"{wild} does not raise the score floor above {parent} ({w_score} vs {p_score}) — "
            f"it is then just {parent} with more money on the same signals.")
        assert w_margin > p_margin, (
            f"{wild} does not raise margin above {parent} ({w_margin}% vs {p_margin}%) — "
            f"a higher bar with the same size leaves the rarer signal underpaid.")


def test_every_wild_variant_stays_out_of_the_catalog():
    """Unmeasured floors on whole-book positions. None of these may reach a user via discover."""
    for wild, _ in WILD_PAIRS:
        card = _yaml(f"strategies/{wild}/strategy.yaml")
        assert card["catalog"].get("status") == "blocked", (
            f"{wild} lost catalog.status: blocked — gen_catalog would publish it to the catalog "
            f"AND to senpi-strategy-discover. Deploy experiments by explicit path instead.")


# ── per-leverage sibling packages must land on the SAME price distances ──

LEVERAGE_SIBLINGS = [("penguin-x5", "penguin")]


def _lev_and_ladder(pkg):
    rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
    inp = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]
    lev = float(inp.get("leverageDefault") or rt["strategy"]["default_leverage"])
    preset = rt["exit"]["dsl_preset"]
    return lev, preset["phase2"]["tiers"], preset["phase1"]["max_loss_pct"], inp


@pytest.mark.parametrize("sibling,base", LEVERAGE_SIBLINGS)
def test_a_leverage_sibling_keeps_every_exit_at_the_same_price_distance(sibling, base):
    """The whole reason the sibling exists: every DSL threshold is ROE and the engine divides by
    leverage to get a price floor, so a ladder is authored for ONE leverage. A sibling for another
    band is only correct if trigger/leverage matches rung for rung — otherwise it is not the same
    strategy at a different size, it is a different strategy."""
    s_lev, s_tiers, s_stop, s_inp = _lev_and_ladder(sibling)
    b_lev, b_tiers, b_stop, _ = _lev_and_ladder(base)
    assert s_lev != b_lev, f"{sibling} requests the same leverage as {base}; it has no reason to exist"
    assert len(s_tiers) == len(b_tiers), "the sibling must mirror the base ladder rung for rung"
    for i, (st, bt) in enumerate(zip(s_tiers, b_tiers)):
        assert abs(st["trigger_pct"] / s_lev - bt["trigger_pct"] / b_lev) < 1e-9, (
            f"rung {i}: {sibling} arms at {st['trigger_pct'] / s_lev:.3f}% of price but {base} arms "
            f"at {bt['trigger_pct'] / b_lev:.3f}%. Re-derive it as base_trigger * {s_lev}/{b_lev}.")
        assert st["lock_hw_pct"] == bt["lock_hw_pct"], (
            f"rung {i}: locks are a fraction of high-water and carry no leverage, so they must match")
    assert abs(s_stop / s_lev - b_stop / b_lev) < 1e-9, (
        f"the stop sits at {s_stop / s_lev:.3f}% of price vs {base}'s {b_stop / b_lev:.3f}%")


@pytest.mark.parametrize("sibling,base", LEVERAGE_SIBLINGS)
def test_a_leverage_sibling_refuses_names_outside_its_band(sibling, base):
    """Its ladder is correct at its own leverage and wrong below it, so it must skip rather than
    trade — otherwise the sibling reintroduces exactly the mis-calibration it was built to remove."""
    _lev, _t, _s, inp = _lev_and_ladder(sibling)
    assert inp.get("minLeverageStrict") is True, (
        f"{sibling} must set minLeverageStrict — without it the venue can hand it a lower leverage "
        f"and its halved ladder becomes wrong in the other direction")
    assert float(inp["minLeverage"]) == _lev, (
        f"{sibling} must floor minLeverage at the leverage its ladder is authored for ({_lev})")


# ── no ELEVENTH package may grow a split-leverage ladder ──
#
# Ten live packages still spread leverage across score tiers while carrying a fixed-ROE ladder, so
# every exit means a different price move per tier (lowest conviction gets the WIDEST stop). They
# are not a mechanical sweep: exposure can be held constant, but the ladder's price meaning must
# change for whichever tiers are not the one you flatten to, and choosing that leverage needs the
# score distribution from telemetry. Two are a different bug entirely — `raptor` sizes via
# marginPctBase/marginPctHighConv with no marginPct, and `wolverine`'s third tier element is a
# LABEL ('apex'/'standard'), not a margin.
#
# Full per-package table, the arithmetic, and the procedure:
#   strategies/references/ladder-leverage-coherence.md
#
# This list is a DEBT REGISTER, not a permission slip. Shrink it; never add to it.
KNOWN_SPLIT_LEVERAGE = {
    # Measured 2026-10-01 over 14 days of real emits. FIVE of these produce no signals at all —
    # otter 0 emits from 4,059 scan lines, kodiak 0 from 206, lemon not deployed, polar 6,
    # kestrel 4 — so their ladders are theory rather than live behaviour. Fix them when they trade.
    "lemon", "otter", "polar", "kestrel", "kodiak", "wolverine",
    # raptor and pangolin were here and are now FIXED — see COHERENT.
}

# condor joined when its ladder was re-derived onto the shared price ladder (see
# test_one_price_ladder.py). orca is coherent by construction rather than by a flat tier list —
# it has no leverageTiers at all, one leverageDefault for every signal — so it cannot be asserted
# here; test_one_price_ladder.py covers it instead.
COHERENT = ["cheetah", "wild-cheetah", "owl", "jaguar", "raptor", "pangolin", "condor"]


def test_no_new_package_grows_a_split_leverage_ladder():
    """The ten above are known. An eleventh is a regression, and this is where it gets caught."""
    import glob
    offenders = set()
    for rt in glob.glob(os.path.join(ROOT, "strategies", "*", "*", "runtime.yaml")):
        pkg = os.path.relpath(rt, os.path.join(ROOT, "strategies")).split(os.sep)[0]
        try:
            d = yaml.safe_load(open(rt, encoding="utf-8"))
        except Exception:
            continue
        scanners = [s for s in (d.get("scanners") or [])
                    if isinstance(s, dict) and s.get("type") == "external_scanner"]
        if not scanners:
            continue
        tiers = (scanners[0].get("inputs") or {}).get("leverageTiers")
        if not isinstance(tiers, list):
            continue
        levs = {t[1] for t in tiers
                if isinstance(t, (list, tuple)) and len(t) > 1 and isinstance(t[1], (int, float))}
        preset = (d.get("exit") or {}).get("dsl_preset") or {}
        if len(levs) > 1 and (preset.get("phase2") or {}).get("tiers"):
            offenders.add(pkg)
    new = offenders - KNOWN_SPLIT_LEVERAGE
    assert not new, (
        f"{sorted(new)} spread leverage across score tiers while carrying a fixed-ROE ladder, so "
        f"each exit means a different price move depending on which tier fired — and the weakest "
        f"signal gets the widest stop. Scale conviction through the third tier element (marginPct) "
        f"and keep leverage flat. See strategies/references/ladder-leverage-coherence.md.")
    gone = KNOWN_SPLIT_LEVERAGE - offenders
    assert not gone, (
        f"{sorted(gone)} no longer spread leverage — remove them from KNOWN_SPLIT_LEVERAGE and add "
        f"them to COHERENT so the stronger assertion covers them from now on.")




# Rung-0 arming is a SEPARATE claim from leverage coherence, and the two were wrongly coupled: the
# 1%-of-price bar came from PENGUIN's universe (median peak 0.84%) and does not transfer. pangolin,
# measured on its OWN 20 closes, has a median peak of 0.57% but a p75 of 3.51% — a different shape,
# where a rung at 2.67% still arms on 40% of positions. So the bar is per-package, and an exemption
# has to carry its measurement.
RUNG0_MAX_PRICE_PCT = {
    # pkg: (max % of price, why)
    "pangolin": (2.70, "own 20 closes: median peak 0.57%, p75 3.51%, p90 5.39% — its rung 0 at "
                       "2.67% arms on 40% of positions, and n=20 is too thin to retune a ladder"),
}
_DEFAULT_RUNG0_MAX = 1.0   # penguin's universe: median peak 0.84% of price


@pytest.mark.parametrize("pkg", COHERENT)
def test_rung_zero_arms_often_enough(pkg):
    """A first rung above a package's own peak distribution never arms, so a position that goes
    green has no floor under it and can round-trip to the stop."""
    rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
    sc = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")
    tiers = sc["inputs"].get("leverageTiers") or []
    lev = {t[1] for t in tiers}.pop() if tiers else rt["strategy"]["default_leverage"]
    first = rt["exit"]["dsl_preset"]["phase2"]["tiers"][0]["trigger_pct"] / lev
    cap, why = RUNG0_MAX_PRICE_PCT.get(pkg, (_DEFAULT_RUNG0_MAX, "penguin's measured 0.84% median peak"))
    assert first <= cap, (
        f"{pkg}'s rung 0 arms at {first:.2f}% of price, above its {cap:.2f}% bar ({why}). Either "
        f"lower the trigger or record a measured exemption in RUNG0_MAX_PRICE_PCT — never raise "
        f"the cap without the package's own peak distribution behind it.")


# ── the penguin/puffin/pelican family closes on NOTHING but the DSL ──

NO_CLOCK = ["penguin", "purple-penguin", "penguin-x5", "penguins-duo", "pelican", "puffin"]
_TIME_CUTS = ("hard_timeout", "weak_peak_cut", "dead_weight_cut")


@pytest.mark.parametrize("pkg", NO_CLOCK)
@pytest.mark.parametrize("cut", _TIME_CUTS)
def test_no_time_cut_fires_on_this_family(pkg, cut):
    """Jason, 2026-10-05: "remove the 5h weak peak on all versions of penguin and puffin and
    pelican. Let DSL manage all exits." So every clock is off on this family, and this test is the
    thing that keeps it that way.

    `hard_timeout` and `dead_weight_cut` went first, on a measurement: of 142 hard_timeout closes
    in 7 days, 110 (77.5%) were in profit and 93 (65.5%) kept moving the right way for the next
    hour. `weak_peak_cut` survived that round because it is structurally different — its timer
    RESETS whenever ROE clears `min_value`, and its bar sits under rung 0's arm, so it could only
    fire on a position that never worked (see
    test_weak_peak_cut_can_only_fire_before_the_ladder_arms, which still guards condor's). It is off
    here anyway: the finding above is a verdict on deciding exits by elapsed time, not on one
    setting of one cut.

    The cost is real and accepted, so state it rather than discover it later: there is no
    CLOSE_POSITION scanner on this family, so with every clock off the ONLY thing that ends a dead
    position is the phase-1 floor. A flatliner holds its slot until that stop fires or it finally
    moves — which on penguin, at 90% margin in one slot, is the whole strategy. The judgement is
    that a slow winner is worth more than a freed slot.

    Re-enabling any of these is a product decision with a cost: say so here with the evidence."""
    preset = _yaml(f"strategies/{pkg}/main/runtime.yaml")["exit"]["dsl_preset"]
    assert not (preset.get(cut) or {}).get("enabled"), (
        f"{pkg} has {cut} back on. This family closes on the DSL ladder and the phase-1 floor "
        f"only — no exit here is decided by elapsed time. If that is meant to change, change it "
        f"with the measurement that justifies it, not as a default.")


def test_the_phase1_floor_is_what_holds_a_dead_position(pkg_floor=1.5):
    """The corollary of the test above, and the number a reader needs when they ask "so what DOES
    close a loser?". With every clock off, the answer is this floor and nothing else — so if it
    ever went missing or got loose, the family would have no exit for a position that never works."""
    for pkg in NO_CLOCK:
        rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
        preset = rt["exit"]["dsl_preset"]
        lev = float(rt["strategy"]["default_leverage"])
        ml = preset["phase1"].get("max_loss_pct")
        assert ml, f"{pkg} has no phase-1 max_loss_pct — with every clock off, nothing ends a loser"
        px = float(ml) / lev
        assert 1.0 <= px <= 3.0, (
            f"{pkg}'s only backstop is {ml}% ROE at {lev}x = {px:.2f}% of price. Outside the "
            f"1.0-3.0% band that is either inside ordinary noise (and will scratch winners) or so "
            f"wide that a dead position is effectively never closed at all.")




# ── grizzly-wild is grizzly in exactly two inputs ──

def test_grizzly_wild_differs_from_grizzly_in_exactly_two_inputs():
    """Same contract as purple-penguin, for the other direction.

    grizzly-wild exists to ask one question: does grizzly's BTC thesis pay for a bigger position when
    it clears a higher bar? That is only answerable if the floor and the size are the ONLY things
    that differ — any third change and a difference in results stops being attributable."""
    g = _yaml("strategies/grizzly/main/runtime.yaml")
    w = _yaml("strategies/grizzly-wild/main/runtime.yaml")
    gi = next(s for s in g["scanners"] if s.get("type") == "external_scanner")["inputs"]
    wi = next(s for s in w["scanners"] if s.get("type") == "external_scanner")["inputs"]
    differ = {k for k in set(gi) | set(wi) if gi.get(k) != wi.get(k)}
    assert differ == {"minScore", "marginPct"}, (
        f"grizzly-wild differs from grizzly in {sorted(differ)}. Only minScore and marginPct may "
        f"differ — revert anything else or the experiment reads nothing.")
    assert (gi["minScore"], wi["minScore"]) == (12, 14), (
        f"the floor pair moved: grizzly {gi['minScore']}, grizzly-wild {wi['minScore']}. 14 is "
        f"grizzly's own apex tier, the score at which it already sized largest.")
    assert (gi["marginPct"], wi["marginPct"]) == (50, 90)


def test_grizzly_wild_inherits_grizzlys_exits_untouched():
    """Its whole exit thesis is "grizzly's, because BTC is not the leaderboard universe"."""
    g = _yaml("strategies/grizzly/main/runtime.yaml")
    w = _yaml("strategies/grizzly-wild/main/runtime.yaml")
    assert w["exit"] == g["exit"], (
        "grizzly-wild's exit block diverged from grizzly's. BTC's measured adverse bounce is p90 "
        "0.51% of price and grizzly's stop is 0.80%, already outside it — there is no measurement "
        "behind changing it on the wild variant alone.")


def test_grizzly_wild_vendors_grizzlys_scanners_byte_for_byte():
    """A forked scanner is a silent divergence in the entry model, which is the thing being held
    constant. If grizzly's detector is fixed, grizzly-wild must get the same fix."""
    for f in ("scan.py", "scoring.py"):
        assert _read(f"strategies/grizzly-wild/main/scanners/{f}") == \
               _read(f"strategies/grizzly/main/scanners/{f}"), (
            f"grizzly-wild/{f} has drifted from grizzly/{f}. Port the change to both in the same "
            f"commit, or the two stop being comparable.")


# ── penguins-duo: penguin across two slots ──

DUO_SPLIT = {"slots": (1, 2), "margin_pct": (90, 45)}
DUO_SPLIT_INPUTS = {"maxPositions": (1, 2), "marginPct": (90, 45)}


def test_penguins_duo_differs_from_penguin_in_slots_and_margin_only():
    """The comparison is only interpretable if the slot count and the per-slot margin are the ONLY
    things that differ. Any third change and a difference in results stops being attributable to
    one-slot-versus-two."""
    peng = _yaml("strategies/penguin/main/runtime.yaml")
    duo = _yaml("strategies/penguins-duo/main/runtime.yaml")
    for k in ("name", "group", "version", "description"):
        peng.pop(k, None); duo.pop(k, None)
    peng["strategy"].pop("wallet", None); duo["strategy"].pop("wallet", None)

    for key, (p_want, d_want) in DUO_SPLIT.items():
        assert peng["strategy"][key] == p_want and duo["strategy"][key] == d_want, (
            f"strategy.{key}: penguin {peng['strategy'][key]} / duo {duo['strategy'][key]}, "
            f"expected {p_want} / {d_want}")
        peng["strategy"].pop(key); duo["strategy"].pop(key)

    ps = next(x for x in peng["scanners"] if x.get("type") == "external_scanner")
    qs = next(x for x in duo["scanners"] if x.get("type") == "external_scanner")
    for key, (p_want, d_want) in DUO_SPLIT_INPUTS.items():
        assert ps["inputs"][key] == p_want and qs["inputs"][key] == d_want, (
            f"inputs.{key}: penguin {ps['inputs'][key]} / duo {qs['inputs'][key]}, "
            f"expected {p_want} / {d_want}")
        ps["inputs"].pop(key); qs["inputs"].pop(key)

    assert duo == peng, (
        "penguins-duo diverges from penguin beyond the slots/margin split. Every other difference "
        "destroys the comparison — diff the two runtime.yaml files and revert anything else.")


def test_penguins_duo_keeps_the_same_total_margin_as_penguin():
    """Slots x margin must stay at penguin's 90%. If the duo committed MORE in total it would be a
    leverage change wearing a slot-count costume, and the comparison would measure size instead."""
    peng = _yaml("strategies/penguin/main/runtime.yaml")["strategy"]
    duo = _yaml("strategies/penguins-duo/main/runtime.yaml")["strategy"]
    assert duo["slots"] * duo["margin_pct"] == peng["slots"] * peng["margin_pct"] == 90, (
        f"combined margin differs: penguin {peng['slots']}x{peng['margin_pct']}% vs duo "
        f"{duo['slots']}x{duo['margin_pct']}%. Hold the total at 90% or the two are not comparable.")
    assert duo["default_leverage"] == peng["default_leverage"], "leverage must match too"


# ── pelican and its duo ──
#
# Pelican is penguin with exactly ONE functional difference: `xyzBanned: false`, admitting the XYZ
# (HIP-3) markets penguin bans. Everything else is penguin's verbatim, and that is the invariant —
# the moment a second thing drifts, "pelican is penguin over a wider universe" stops being true and
# a difference in their results becomes unattributable.

PELICAN_ONLY_DIFFERENCE = {"xyzBanned": (True, False)}   # (penguin, pelican)


def _depersonalise(obj, pkg):
    """Replace a package's own id wherever it appears in a string, so two recipes can be compared on
    BEHAVIOUR. The id is woven through identity fields — the scanner name, every action name, the
    `scanners:` references inside actions — and none of those are behavioural differences."""
    if isinstance(obj, dict):
        return {k: _depersonalise(v, pkg) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_depersonalise(v, pkg) for v in obj]
    if isinstance(obj, str):
        return obj.replace(pkg, "<pkg>")
    return obj


def test_pelican_is_penguin_plus_the_wider_universe_and_nothing_else():
    peng = _yaml("strategies/penguin/main/runtime.yaml")
    pel = _yaml("strategies/pelican/main/runtime.yaml")
    for k in ("name", "group", "version", "description"):
        peng.pop(k, None); pel.pop(k, None)
    peng["strategy"].pop("wallet", None); pel["strategy"].pop("wallet", None)
    peng = _depersonalise(peng, "penguin")
    pel = _depersonalise(pel, "pelican")
    ps = next(x for x in peng["scanners"] if x.get("type") == "external_scanner")
    qs = next(x for x in pel["scanners"] if x.get("type") == "external_scanner")

    for key, (p_want, l_want) in PELICAN_ONLY_DIFFERENCE.items():
        assert ps["inputs"][key] == p_want and qs["inputs"][key] == l_want, (
            f"inputs.{key}: penguin {ps['inputs'][key]} / pelican {qs['inputs'][key]}, "
            f"expected {p_want} / {l_want}")
        ps["inputs"].pop(key); qs["inputs"].pop(key)

    assert pel == peng, (
        "pelican diverges from penguin beyond the XYZ universe flag. Exits, ladder, stop, guard "
        "rails, cadence, score floor, leverage, margin and the pre-move gate are all meant to be "
        "penguin's verbatim — diff the two runtime.yaml files and revert anything else.")


def test_pelicans_duo_differs_from_pelican_in_slots_and_margin_only():
    pel = _yaml("strategies/pelican/main/runtime.yaml")
    duo = _yaml("strategies/pelicans-duo/main/runtime.yaml")
    for k in ("name", "group", "version", "description"):
        pel.pop(k, None); duo.pop(k, None)
    pel["strategy"].pop("wallet", None); duo["strategy"].pop("wallet", None)
    # the duo INHERITED pelican's scanner and action names verbatim, so the common token is
    # "pelican" on both sides, not "pelicans-duo"
    pel = _depersonalise(pel, "pelican"); duo = _depersonalise(duo, "pelican")

    assert (pel["strategy"]["slots"], duo["strategy"]["slots"]) == (1, 2)
    assert (pel["strategy"]["margin_pct"], duo["strategy"]["margin_pct"]) == (90, 40)
    for k in ("slots", "margin_pct"):
        pel["strategy"].pop(k); duo["strategy"].pop(k)

    ps = next(x for x in pel["scanners"] if x.get("type") == "external_scanner")
    qs = next(x for x in duo["scanners"] if x.get("type") == "external_scanner")
    assert (ps["inputs"]["maxPositions"], qs["inputs"]["maxPositions"]) == (1, 2)
    assert (ps["inputs"]["marginPct"], qs["inputs"]["marginPct"]) == (90, 40)
    for k in ("maxPositions", "marginPct"):
        ps["inputs"].pop(k); qs["inputs"].pop(k)

    assert duo == pel, (
        "pelicans-duo diverges from pelican beyond the slots/margin split — revert anything else.")


def test_the_two_duos_do_not_split_margin_the_same_way_and_that_is_recorded():
    """penguins-duo holds its parent's 90% (2 x 45); pelicans-duo is 80% (2 x 40), BELOW pelican's 90.

    This is deliberate and specified, not a slip — but it has a consequence worth pinning: because
    pelicans-duo is both more diversified AND smaller than its parent, a difference in its results
    cannot be attributed to the slot count alone. penguins-duo isolates the slot count; this one does
    not. If someone later 'fixes' the inconsistency by moving it to 45, that changes what the
    experiment measures, so it should be a deliberate edit here rather than a silent one."""
    pg_duo = _yaml("strategies/penguins-duo/main/runtime.yaml")["strategy"]
    pl_duo = _yaml("strategies/pelicans-duo/main/runtime.yaml")["strategy"]
    assert pg_duo["slots"] * pg_duo["margin_pct"] == 90, "penguins-duo must hold penguin's 90%"
    assert pl_duo["slots"] * pl_duo["margin_pct"] == 80, (
        f"pelicans-duo combined margin is {pl_duo['slots'] * pl_duo['margin_pct']}%, recorded as 80% "
        f"(2 x 40). Changing it to 90% would make it comparable to penguins-duo but would also change "
        f"what this experiment measures — do that deliberately, with the reason.")
    assert pl_duo["default_leverage"] == pg_duo["default_leverage"] == 10
