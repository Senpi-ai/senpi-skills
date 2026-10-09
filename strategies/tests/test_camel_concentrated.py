"""camel-concentrated: the carry trade taken as one position per side at 10x.

Jason, 2026-10-06, asked for "a totally different type of strategy ... to run a concentrated high
leverage version of". camel was the pick on evidence rather than taste: of every non-momentum
template in the catalog it is the ONLY one actually trading — 259 real fills across 4 live wallets
in the 7 days to 2026-10-06, net +$5.42 after $6.72 of fees on $11.52 realized — while octopus (17
emits), eel (9) and bison (8) had gone quiet and ant, weaver, cougar and raven emitted nothing at
all. It is also the one with somewhere for concentration to GO: fees are 58% of its gross, and one
position costs roughly a quarter of the fees four do for the same money at work.

Three things this file pins, each of which would silently void the experiment:

  1. **Leverage lives in two places.** `strategy.default_leverage` AND the scanner's own
     `maxLeverage` clamp. Changing one and not the other leaves 10x inert while every card claims
     it — the same shape as the dead `minScore` floor this tree has been bitten by (#677). Same for
     slots: `strategy.slots` and the scanner's `maxSlots`.
  2. **The DSL is re-derived, not copied.** The ladder is ROE and the engine divides by leverage, so
     carrying camel's 5x numbers onto 10x would HALVE every price distance — the 6% ROE stop would
     become 0.60% of price, inside the measured p75 adverse bounce, and the book would stop out on
     noise. Every price distance here must equal camel's exactly (#821).
  3. **The guard rails had to move, and that is forced arithmetic.** One stop costs 6% ROE x 18%
     margin = 1.08% of a 4-slot arm, but 12% x 72% = 8.64% concentrated. camel's 10% daily limit
     would halt the book after ONE loss, and the experiment would then read as a failure of
     concentration rather than of calibration.

Run: python3 -m pytest strategies/tests/test_camel_concentrated.py -q
"""
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

ARMS = ("harvest", "payout")
PARENT, ARM_PKG = "camel", "camel-concentrated"


def _rt(pkg, arm):
    with open(os.path.join(ROOT, "strategies", pkg, arm, "runtime.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _inputs(rt):
    return next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]


@pytest.mark.parametrize("arm", ARMS)
def test_leverage_is_raised_in_BOTH_places(arm):
    """`maxLeverage` is a strict clamp inside the scanner. Raise `default_leverage` alone and the
    scanner silently holds the book at 5x while the card advertises 10x."""
    rt = _rt(ARM_PKG, arm)
    assert rt["strategy"]["default_leverage"] == 10, "strategy.default_leverage is not 10x"
    assert _inputs(rt)["maxLeverage"] == 10, (
        "the scanner's maxLeverage clamp is still at its old value, so 10x is INERT — the runtime "
        "would size at the clamp and every card claiming 10x would be wrong")


@pytest.mark.parametrize("arm", ARMS)
def test_concentration_is_applied_in_BOTH_places(arm):
    rt = _rt(ARM_PKG, arm)
    assert rt["strategy"]["slots"] == 1, "strategy.slots is not 1"
    assert _inputs(rt)["maxSlots"] == 1, "the scanner's own maxSlots cap still allows more than one"


@pytest.mark.parametrize("arm", ARMS)
def test_margin_deployed_is_unchanged_from_camel(arm):
    """4 x 18 = 72 = 1 x 72. Concentration moves the COUNT, not how much margin is at work — so a
    difference in results is not just a bigger bet in disguise. (Leverage does double gross
    notional; that is the separate, declared half of the change.)"""
    new, old = _inputs(_rt(ARM_PKG, arm)), _inputs(_rt(PARENT, arm))
    assert new["marginPct"] * new["maxSlots"] == old["marginPct"] * old["maxSlots"] == 72, (
        f'margin deployed moved: {new["marginPct"]}x{new["maxSlots"]} vs '
        f'{old["marginPct"]}x{old["maxSlots"]}')


@pytest.mark.parametrize("arm", ARMS)
def test_every_dsl_price_distance_equals_camels(arm):
    """The whole point of re-deriving. ROE / leverage is the only portable form of a ladder."""
    a, p = _rt(ARM_PKG, arm), _rt(PARENT, arm)
    la, lp = a["strategy"]["default_leverage"], p["strategy"]["default_leverage"]
    da, dp = a["exit"]["dsl_preset"], p["exit"]["dsl_preset"]
    assert da["phase1"]["max_loss_pct"] / la == dp["phase1"]["max_loss_pct"] / lp == 1.2, (
        f'stop is {da["phase1"]["max_loss_pct"]/la:.2f}% of price, camel\'s is '
        f'{dp["phase1"]["max_loss_pct"]/lp:.2f}% — a 10x copy of 5x ROE numbers halves the stop '
        f'and puts it inside the measured noise band')
    got = [t["trigger_pct"] / la for t in da["phase2"]["tiers"]]
    want = [t["trigger_pct"] / lp for t in dp["phase2"]["tiers"]]
    assert got == want, f"rung price distances {got} != camel's {want}"
    assert [t["lock_hw_pct"] for t in da["phase2"]["tiers"]] == \
           [t["lock_hw_pct"] for t in dp["phase2"]["tiers"]], "the locks were changed too"


@pytest.mark.parametrize("arm", ARMS)
def test_no_clock_decides_an_exit(arm):
    """Jason, 2026-10-06, as a standing preference rather than a one-off: "i prefer to just let DSL
    do its job vs hard cuts of any sort".

    I argued the other way first and the argument was defensible — camel's cuts are 72h and 8h,
    while the measurement that removed clocks from the penguin family (110 of 142 clock closes in
    profit) was taken on 4h cuts clipping winners mid-move. Different instrument, different claim.
    It was still not the call to make: the preference is that the ladder and the stop decide every
    exit, at any horizon. Recorded here so the 72h argument does not get re-derived and re-applied.

    The cost, stated rather than discovered: with every clock off, the phase-1 floor at 1.20% of
    price is the ONLY thing that ends a dead carry, and there is no CLOSE_POSITION scanner here. A
    dislocation that never reverts keeps its slot — and concentrated, that slot IS the arm. The one
    thing that softens it is that funding keeps accruing while it waits, which is not true of a
    momentum trade going nowhere."""
    d = _rt(ARM_PKG, arm)["exit"]["dsl_preset"]
    on = [c for c in ("hard_timeout", "weak_peak_cut", "dead_weight_cut")
          if (d.get(c) or {}).get("enabled")]
    assert not on, (
        f"{arm} has {on} back on. Nothing in this package closes on a clock — the DSL ladder and "
        f"the phase-1 floor decide every exit. If that is meant to change, it is a product "
        f"decision, not a consistency sweep against the parent.")
    assert d["phase1"].get("max_loss_pct"), (
        f"{arm} has no phase-1 floor and no clocks — nothing would ever end a losing carry")


@pytest.mark.parametrize("arm", ARMS)
def test_the_guard_rails_absorb_as_many_stops_as_camels_did(arm):
    """Forced arithmetic, pinned so nobody "restores consistency" with the parent and silently
    halts the arm after one loss."""
    a, p = _rt(ARM_PKG, arm), _rt(PARENT, arm)
    def per_stop(rt):
        i = _inputs(rt)
        return rt["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / 100.0 * i["marginPct"]
    cost_a, cost_p = per_stop(a), per_stop(p)
    assert round(cost_a, 2) == 8.64 and round(cost_p, 2) == 1.08, (cost_a, cost_p)
    ga, gp = a["risk"]["guard_rails"], p["risk"]["guard_rails"]
    # camel's 10% limit against a 4-slot arm absorbs two full stop-outs (4 x 1.08 = 4.32)
    assert ga["daily_loss_limit_pct"] >= 2 * cost_a, (
        f'daily_loss_limit_pct {ga["daily_loss_limit_pct"]}% cannot absorb two {cost_a:.2f}% '
        f'stop-outs, so the arm halts almost immediately and measures the rail, not the thesis')
    assert ga["drawdown_halt_pct"] > gp["drawdown_halt_pct"], "the drawdown halt was not scaled"
    for k, v in gp.items():                      # everything else is camel's
        if k not in ("daily_loss_limit_pct", "drawdown_halt_pct"):
            assert ga[k] == v, f"guard rail {k} diverged from camel's"


@pytest.mark.parametrize("arm", ARMS)
def test_the_scanners_are_camels_byte_for_byte(arm):
    """The entry model is the thing being held constant — same ranking, same exhaustion gate."""
    for d, _, files in os.walk(os.path.join(ROOT, "strategies", PARENT, arm, "scanners")):
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            a = os.path.join(ROOT, "strategies", ARM_PKG, arm, "scanners", f)
            with open(os.path.join(d, f), "rb") as x, open(a, "rb") as y:
                assert x.read() == y.read(), f"{ARM_PKG}/{arm}/scanners/{f} drifted from {PARENT}'s"
        break


def test_the_sleeve_split_is_camels():
    """harvest and payout are the two sides of the carry trade; skewing them would change the
    net-neutrality that falls out of taking both."""
    def shares(pkg):
        with open(os.path.join(ROOT, "strategies", pkg, "strategy.yaml"), encoding="utf-8") as fh:
            return [(i["name"], i.get("funding_share")) for i in yaml.safe_load(fh)["instances"]]
    assert shares(ARM_PKG) == shares(PARENT), "the 50/50 harvest/payout split moved"


def test_it_declares_camel_as_its_parent():
    with open(os.path.join(ROOT, "strategies", ARM_PKG, "strategy.yaml"), encoding="utf-8") as fh:
        card = yaml.safe_load(fh)["catalog"]
    assert card.get("varies") == PARENT, (
        "the card does not declare `varies: camel`, so discover would rank it against its own "
        "parent on near-identical text instead of behind it")
    blob = " ".join(str(card.get(k, "")) for k in ("name", "tagline", "belief_plain", "thesis"))
    assert "10x" in blob, "the card never states the leverage, which is half of what it varies"
