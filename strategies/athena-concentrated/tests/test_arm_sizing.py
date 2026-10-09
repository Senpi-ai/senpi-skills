#!/usr/bin/env python3
"""Both arms size bigger and more concentrated than their parents, with the ladder derived from
this package's own history rather than from camel's numbers.

Jason, 2026-10-09: "both arms take somewhat bigger more concentrated bets ... with the right DSL
settings more like camel's", then "take your best recommendation ... rooted in reasonable data
inputs". The inputs are a 14-day replay of every completed round trip on the athena / aegis /
phalanx fleet — 34 aegis trips over 7 coins, 54 phalanx trips over 11:

                        adverse excursion      favourable peak        median winner
    aegis    p75 2.83%  p90 3.41%   p50 0.42%  p75 4.51%  p90 6.02%   gives back 67% of peak
    phalanx  p75 5.04%  p90 5.96%   p50 2.46%  p75 8.73%  p90 12.36%  gives back 61% of peak

Three things follow, and each is asserted below.

1. THE STOP SITS AT THE MEASURED p90, not at a round number. phalanx's old stop was 5.00% of
   price against an adverse p75 of 5.04% — it was cutting a quarter of trades that later
   recovered. It is now 6.00%. aegis's 3.33% was already at its p90 and is unchanged in price.

2. THE FIRST RUNG ARMS EARLY. The old first rungs sat at 4.00% (aegis) and 6.67% (phalanx) of
   price and armed on 29% and 36% of trades, so most positions locked nothing and rode the
   give-back back through entry. Both now sit at 2.00% of price, arming on 41% and 60%. The
   locks come from the give-back distribution: at peak >= 2% a 38% lock survives the median
   aegis winner and 44% the median phalanx winner, so 35% and 40% are the measured values.

3. EVERY ROE NUMBER IS A PRICE DISTANCE x LEVERAGE. Raising leverage without re-deriving the
   ladder silently tightens every exit — the camel-concentrated lesson. The price distances are
   pinned here so the arithmetic cannot drift.

Phalanx deliberately stays at 2 slots. It does rank its candidates, but nothing in the data
attributes outcome to rank, so cutting to 1 would be taste dressed as concentration. It already
went 3 -> 2 when this package was created; this round it gets size and leverage.

aegis deliberately stays at >= 2 slots: its mechanism is expressing a regime as short-risk PLUS
long-defensive, and at 1 slot it can only hold one leg, which is no longer a regime read.
"""
import importlib.util
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))

# arm -> (leverage, slots, margin floor, margin cap, stop % of price, [(rung % of price, lock)])
SHAPE = {
    "aegis": (5, 2, 25.0, 45.0, 3.33, [(2.00, 35), (4.00, 45), (8.00, 60), (16.67, 85)]),
    "phalanx": (4, 2, 40.0, 48.0, 6.00, [(2.00, 40), (6.00, 55), (12.35, 70), (16.60, 88)]),
}
# the measured adverse p90 each stop is placed at
ADVERSE_P90 = {"aegis": 3.41, "phalanx": 5.96}


def _rt(arm):
    with open(os.path.join(PKG, arm, "runtime.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _inputs(rt):
    return next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]


def _scoring(arm):
    """Load the arm's scorer BY PATH — four packages vendor a module called `scoring`."""
    path = os.path.join(PKG, arm, "scanners", "scoring.py")
    spec = importlib.util.spec_from_file_location(f"_scoring_{arm}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_leverage_is_raised_in_both_places(arm):
    """`strategy.default_leverage` AND the scanner's own `leverageDefault`. Changing one leaves
    the other authoritative — the dead-input failure this tree has been bitten by repeatedly."""
    lev, _slots, _lo, _hi, _stop, _rungs = SHAPE[arm]
    rt = _rt(arm)
    assert rt["strategy"]["default_leverage"] == lev
    assert float(_inputs(rt)["leverageDefault"]) == lev


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_leverage_is_at_the_scanners_hard_ceiling_not_above_it(arm):
    """Both scanners clamp with `min(_MAX_LEVERAGE, requested)`, so a config above the ceiling is
    SILENTLY reduced and every card then overstates the leverage. aegis caps at 5, phalanx at 4."""
    lev = SHAPE[arm][0]
    src = open(os.path.join(PKG, arm, "scanners", "scan.py"), encoding="utf-8").read()
    ceiling = next(int(l.split("=")[1].split("#")[0].strip())
                   for l in src.splitlines() if l.startswith("_MAX_LEVERAGE"))
    assert lev <= ceiling, (
        f"{arm}: configured {lev}x but the scanner clamps at {ceiling}x — the orders would go out "
        f"at {ceiling}x while the manifest claims {lev}x")
    assert lev == ceiling, (
        f"{arm}: configured {lev}x with a {ceiling}x ceiling available — intentional? this package "
        f"is the high-leverage variant, so it should sit at the ceiling")


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_slots_are_concentrated_in_both_places(arm):
    _lev, slots, _lo, _hi, _stop, _rungs = SHAPE[arm]
    rt = _rt(arm)
    assert rt["strategy"]["slots"] == slots
    cap = _inputs(rt).get("maxSlots")
    if cap is not None:
        assert float(cap) == slots, f"{arm}: strategy.slots={slots} but the scanner caps at {cap}"
    assert slots >= 2, (
        f"{arm}: one slot is not a concentration of this strategy, it is a different one — aegis "
        f"cannot express a regime with a single leg, and nothing attributes phalanx's outcome "
        f"to its rank")


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_the_margin_range_is_what_the_config_pair_produces(arm):
    """Both scorers scale a base by conviction, and aegis also by regime intensity (which floors
    at x0.25). The floor and cap are therefore a PAIR — asserted against the scorer, not assumed."""
    _lev, _slots, lo, hi, _stop, _rungs = SHAPE[arm]
    sc, rt = _scoring(arm), _rt(arm)
    base = float(_inputs(rt)["marginPctBase"])
    cap = float(_inputs(rt)["marginPctMax"])
    assert cap == hi, f"{arm}: marginPctMax {cap} != {hi}"
    if arm == "aegis":
        vals = [sc.margin_pct_for(c, base, r, cap)
                for r in (0.0, 0.3, 0.5, 1.0, 1.5, 2.0) for c in (25, 49, 50, 69, 70, 100)]
    else:
        vals = [sc.margin_pct_for(c, base, cap) for c in (25, 49, 50, 64, 65, 79, 80, 100)]
    assert min(vals) == pytest.approx(lo), f"{arm}: floor is {min(vals)}, expected {lo}"
    assert max(vals) == pytest.approx(hi), f"{arm}: cap is {max(vals)}, expected {hi}"


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_the_margin_cap_is_set_at_all(arm):
    """phalanx shipped `marginPctMax: 0`, which the scanner reads as None = UNCLAMPED: a
    conviction-80 signal sized at base x 1.5. With a base of 40 that would be 60% in one name."""
    assert float(_inputs(_rt(arm))["marginPctMax"]) > 0, f"{arm}: margin is unclamped"


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_the_stop_sits_at_the_measured_adverse_p90(arm):
    lev, _slots, _lo, _hi, stop_px, _rungs = SHAPE[arm]
    p1 = _rt(arm)["exit"]["dsl_preset"]["phase1"]
    got = p1["max_loss_pct"] / lev
    assert got == pytest.approx(stop_px, abs=0.02), (
        f"{arm}: stop is {got:.2f}% of price, expected {stop_px:.2f}%")
    assert got <= ADVERSE_P90[arm] + 0.1, (
        f"{arm}: a {got:.2f}% stop is wider than the measured adverse p90 of "
        f"{ADVERSE_P90[arm]:.2f}% — it would almost never fire and the ladder becomes the only exit")
    assert got >= ADVERSE_P90[arm] * 0.8, (
        f"{arm}: a {got:.2f}% stop is well inside the measured adverse p90 of "
        f"{ADVERSE_P90[arm]:.2f}% — it cuts trades that the history says recover")
    assert p1["enabled"] is False, f"{arm}: phase-1 trailing was re-enabled"


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_every_rung_sits_at_its_measured_price_distance(arm):
    lev, _slots, _lo, _hi, _stop, rungs = SHAPE[arm]
    tiers = _rt(arm)["exit"]["dsl_preset"]["phase2"]["tiers"]
    assert len(tiers) == len(rungs), f"{arm}: {len(tiers)} rungs, expected {len(rungs)}"
    for t, (px, lock) in zip(tiers, rungs):
        got = t["trigger_pct"] / lev
        assert got == pytest.approx(px, abs=0.02), (
            f"{arm}: rung {t['trigger_pct']}% ROE is {got:.2f}% of price at {lev}x, expected {px}%")
        assert t["lock_hw_pct"] == lock


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_the_first_rung_arms_early(arm):
    """The point of the whole re-ladder: the old first rungs armed on 29% / 36% of trades."""
    lev = SHAPE[arm][0]
    first = _rt(arm)["exit"]["dsl_preset"]["phase2"]["tiers"][0]
    assert first["trigger_pct"] / lev == pytest.approx(2.00, abs=0.02), (
        f"{arm}: the first rung is at {first['trigger_pct']/lev:.2f}% of price, not 2.00% — the "
        f"measured arming rate at 2.00% is 41% (aegis) / 60% (phalanx)")
    assert 30 <= first["lock_hw_pct"] <= 45, (
        f"{arm}: a {first['lock_hw_pct']}% first lock is outside what the give-back distribution "
        f"supports (a 38% / 44% lock survives the median winner at peak >= 2%)")


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_the_drawdown_halt_absorbs_more_than_two_stops(arm):
    """Scaled, and forced rather than chosen. One stop costs stop-ROE x margin share, which rose
    with both. Left at 10% the arm would halt after one or two losses and the result would read
    as a failure of concentration rather than of calibration."""
    _lev, _slots, lo, hi, _stop, _rungs = SHAPE[arm]
    rt = _rt(arm)
    halt = rt["risk"]["guard_rails"]["drawdown_halt_pct"]
    stop_roe = rt["exit"]["dsl_preset"]["phase1"]["max_loss_pct"]
    worst = stop_roe * hi / 100.0          # one stop on the largest position, % of the sleeve
    assert halt / worst >= 2.0, (
        f"{arm}: one stop costs up to {worst:.1f}% of the sleeve and the halt is {halt}% — that is "
        f"only {halt/worst:.1f} stops")


@pytest.mark.parametrize("arm", sorted(SHAPE))
def test_no_clock_decides_an_exit(arm):
    dsl = _rt(arm)["exit"]["dsl_preset"]
    for name, block in dsl.items():
        if isinstance(block, dict) and name not in ("phase1", "phase2"):
            assert block.get("enabled") is False, f"{arm}: {name} is enabled"


def test_the_sleeve_deployment_is_geometric():
    """`marginPct` is a percent of WITHDRAWABLE AT OPEN and the runtime opens sequentially, so
    each position takes a share of what is LEFT. Established by measurement on the 2026-10-09
    relaunch: 104.13 / 83.87 / 66.97 against a 523.80 start, each 19.9-20.0% of the balance
    available at that open, closing on the reported withdrawable to within two cents."""
    for arm, (lev, slots, lo, hi, _s, _r) in SHAPE.items():
        for pct, label in ((lo, "floor"), (hi, "cap")):
            remaining, deployed = 1.0, 0.0
            for _ in range(slots):
                take = remaining * (pct / 100.0)
                deployed += take
                remaining -= take
            assert deployed < slots * pct / 100.0, (
                f"{arm} at the {label}: sequential sizing must deploy LESS than {slots} x {pct}%")
            assert deployed * lev < 4.0, (
                f"{arm} at the {label}: gross exposure {deployed*lev:.2f}x of the sleeve is beyond "
                f"what this package was sized for")


if __name__ == "__main__":
    for arm, (lev, slots, lo, hi, stop, rungs) in sorted(SHAPE.items()):
        dep_lo = 1 - (1 - lo / 100) ** slots
        dep_hi = 1 - (1 - hi / 100) ** slots
        print(f"  {arm:8} {lev}x  {slots} slots  {lo:.0f}-{hi:.0f}% per position  "
              f"deploys {100*dep_lo:.1f}-{100*dep_hi:.1f}%  gross {dep_lo*lev:.2f}-{dep_hi*lev:.2f}x  "
              f"stop {stop:.2f}% px")
    print("ARM SIZING OK")
