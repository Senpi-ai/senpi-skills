"""hornet-concentrated: hornet's supply-chain momentum taken as TWO positions at 10x.

Jason, 2026-10-12, after finding his own agent running a 45%/10x hornet fork that does not exist
in the catalog: "Make it Hornet Concentrated, add it to the catalog."

The fork was not merely a config change — hornet's scanner hardcoded `_MAX_LEVERAGE = 5`, so
`leverage: 10` in a runtime.yaml would have been clamped to 5 and silently inert. That clamp is
now an input (`maxLeverage`) whose default is the old 5, which is why hornet itself is untouched
behaviourally while the variant can raise it.

Five things this file pins, each of which would silently void the variant:

  1. **Leverage lives in two places, and one of them is a clamp.** `strategy.default_leverage`
     AND the scanner's `maxLeverage` ceiling. Set the first alone and the book sizes at 5x while
     every card claims 10x — the dead-setting shape this tree was bitten by in #677.
  2. **The DSL is re-derived, not copied.** The ladder is ROE and the engine divides by leverage,
     so carrying hornet's 4x numbers onto 10x would make every exit 2.5x TIGHTER in price: the
     8% ROE stop would become 0.80% of price instead of 2.00%, and the book would stop out on
     noise. Every shared price distance here must equal hornet's exactly.
  3. **The guard rails had to move, and it is forced arithmetic.** One stop costs 8% ROE x 18%
     margin = 1.44% of a 3-slot hornet, but 20% x 45% = 9.00% concentrated. hornet's 15% daily
     limit would halt the book after ONE loss and the variant would read as a failure of
     concentration rather than of calibration.
  4. **Two slots, not three.** Measured on the live 45%/10x instance over 3 days and 7 opens,
     peak concurrent positions was 2 and never 3. At 45% of withdrawable per open a third has
     no room, so a third slot produces only emits that cannot fill.
  5. **The scanner is the parent's, byte for byte.** The whole claim of a variant is that the
     edge is untouched and only the sizing moved. A drifted scanner makes the comparison
     meaningless, and a comparison against the parent is the only way to read a variant's P&L.

Run: python3 -m pytest strategies/tests/test_hornet_concentrated.py -q
"""
import hashlib
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

PARENT, VARIANT = "hornet", "hornet-concentrated"
SCANNER_FILES = ("scan.py", "scoring.py")


def _rt(pkg):
    with open(os.path.join(ROOT, "strategies", pkg, "main", "runtime.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _manifest(pkg):
    with open(os.path.join(ROOT, "strategies", pkg, "strategy.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _inputs(rt):
    return next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]


def _lev(rt):
    return float(_inputs(rt)["leverage"])


def _price_rungs(rt):
    """Each tier's trigger expressed as a move in PRICE, which is what the market has to deliver."""
    lev = _lev(rt)
    return [round(t["trigger_pct"] / lev, 4) for t in rt["exit"]["dsl_preset"]["phase2"]["tiers"]]


# ── 1. leverage is raised in BOTH places, and the clamp is one of them ──────────────────────

def test_leverage_is_raised_in_both_places_and_past_the_clamp():
    rt = _rt(VARIANT)
    i = _inputs(rt)
    assert rt["strategy"]["default_leverage"] == 10, "strategy.default_leverage is not 10x"
    assert i["leverage"] == 10, "the scanner's per-signal leverage is not 10x"
    assert int(i["maxLeverage"]) >= 10, (
        "scan.py clamps leverage to `maxLeverage` (default 5). Without raising it the book sizes "
        "at 5x while every card claims 10x — the setting is DEAD, the #677 shape")


def test_the_parent_is_untouched_by_the_clamp_becoming_an_input():
    """`maxLeverage` absent must mean exactly the old hardcoded behaviour."""
    src = open(os.path.join(ROOT, "strategies", PARENT, "main", "scanners", "scan.py"),
               encoding="utf-8").read()
    assert 'inputs.get("maxLeverage", _MAX_LEVERAGE)' in src, (
        "the clamp must read from the input with the hardcoded value as its DEFAULT")
    assert "_MAX_LEVERAGE = 5" in src, "the default ceiling is no longer hornet's original 5"
    assert "maxLeverage" not in yaml.dump(_inputs(_rt(PARENT))), (
        "hornet must NOT set maxLeverage — its behaviour has to be unchanged")
    assert _lev(_rt(PARENT)) == 4, "hornet's own leverage moved; this PR must not change it"


# ── 2. the DSL is re-derived so every distance is the same IN PRICE ─────────────────────────

def test_the_stop_sits_at_the_same_price_distance_as_the_parent():
    par, var = _rt(PARENT), _rt(VARIANT)
    p_stop = par["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / _lev(par)
    v_stop = var["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / _lev(var)
    assert abs(p_stop - v_stop) < 1e-9, (
        f"the stop moved in PRICE terms: hornet {p_stop:.2f}% vs concentrated {v_stop:.2f}%. "
        "The ladder is ROE and the engine divides by leverage, so 10x needs 2.5x the ROE number "
        "to keep the same room.")
    assert abs(v_stop - 2.00) < 1e-9, "the stop is no longer 2.00% of price"


def test_every_shared_rung_sits_at_the_parents_price_distance():
    par, var = _price_rungs(_rt(PARENT)), _price_rungs(_rt(VARIANT))
    # the variant may add rungs 10x can afford and 4x could not; every rung the PARENT has must
    # be reproduced exactly, so a given price move locks the same fraction on both.
    missing = [r for r in par if not any(abs(r - v) < 0.05 for v in var)]
    assert not missing, (
        f"hornet rungs at these PRICE distances have no counterpart in the variant: {missing} "
        f"(variant rungs: {var})")


def test_the_extra_rung_is_lower_than_anything_the_parent_had():
    """The one added rung is an early lock 4x could not afford — it must not sit mid-ladder."""
    par, var = _price_rungs(_rt(PARENT)), _price_rungs(_rt(VARIANT))
    extra = [v for v in var if not any(abs(v - r) < 0.05 for r in par)]
    assert len(extra) == 1, f"expected exactly one added rung, got {extra}"
    assert extra[0] < min(par), f"the added rung {extra[0]}% is not below hornet's first ({min(par)}%)"


def test_the_ladder_is_monotonic():
    tiers = _rt(VARIANT)["exit"]["dsl_preset"]["phase2"]["tiers"]
    trig = [t["trigger_pct"] for t in tiers]
    lock = [t["lock_hw_pct"] for t in tiers]
    assert trig == sorted(trig), f"triggers are not ascending: {trig}"
    assert lock == sorted(lock), f"locks are not ascending: {lock}"


# ── 3. the guard rails absorb a stop that is 6.25x more expensive ───────────────────────────

def _stop_cost_pct(rt):
    """What one stop-out costs as a fraction of the whole sleeve."""
    return rt["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] * _inputs(rt)["marginPctBase"] / 100.0


def test_one_stop_costs_what_the_arithmetic_says():
    assert abs(_stop_cost_pct(_rt(PARENT)) - 1.44) < 0.01
    assert abs(_stop_cost_pct(_rt(VARIANT)) - 9.00) < 0.01


def test_the_daily_limit_absorbs_more_than_one_stop():
    rt = _rt(VARIANT)
    limit = rt["risk"]["guard_rails"]["daily_loss_limit_pct"]
    assert limit / _stop_cost_pct(rt) >= 2.0, (
        f"daily_loss_limit_pct {limit}% is under two {_stop_cost_pct(rt):.2f}% stops, so the "
        "first loss of the day halts the book")


def test_the_drawdown_halt_absorbs_at_least_three_stops_and_never_resets():
    rt = _rt(VARIANT)
    g = rt["risk"]["guard_rails"]
    assert g["drawdown_halt_pct"] / _stop_cost_pct(rt) >= 3.0, (
        f"drawdown_halt_pct {g['drawdown_halt_pct']}% is under three stops")
    assert g["drawdown_reset_on_day_rollover"] is False, (
        "a halt that resets at midnight is not a halt — it caps the loss PER DAY, indefinitely")


# ── 4. two slots, because a third cannot fill ──────────────────────────────────────────────

def test_two_slots_and_the_manifest_agrees():
    rt, mf = _rt(VARIANT), _manifest(VARIANT)
    assert rt["strategy"]["slots"] == 2, (
        "45% of withdrawable per open leaves a third position roughly a third the size of the "
        "others; measured live over 3 days and 7 opens, peak concurrent was 2 and never 3")
    assert mf["catalog"]["max_slots"] == 2, "catalog.max_slots disagrees with strategy.slots"
    assert mf["catalog"]["leverage_max"] == 10, "catalog.leverage_max still claims the parent's cap"


def test_the_sleeve_is_not_oversubscribed():
    """marginPct is a percent of withdrawable AT OPEN and positions open sequentially, so
    deployment is geometric, not linear — 2 x 45% is 69.8% committed, not 90%."""
    rt = _rt(VARIANT)
    m = _inputs(rt)["marginPctBase"] / 100.0
    deployed = 1 - (1 - m) ** rt["strategy"]["slots"]
    assert 0.60 <= deployed <= 0.80, f"deployed fraction {deployed:.1%} is outside the intended band"


# ── 5. the edge is the parent's, byte for byte ─────────────────────────────────────────────

def test_the_scanner_is_the_parents_byte_for_byte():
    for name in SCANNER_FILES:
        digests = {}
        for pkg in (PARENT, VARIANT):
            path = os.path.join(ROOT, "strategies", pkg, "main", "scanners", name)
            with open(path, "rb") as fh:
                digests[pkg] = hashlib.sha256(fh.read()).hexdigest()
        assert digests[PARENT] == digests[VARIANT], (
            f"{name} has drifted between hornet and hornet-concentrated. The variant's whole claim "
            "is that only the sizing moved, and a variant's P&L is readable ONLY against its "
            "parent over the same window — a drifted scanner makes that comparison meaningless.")


def test_the_signal_inputs_that_define_the_edge_are_identical():
    """Breadth thresholds, universe, sub-groups and score floor are the EDGE. Only sizing moves."""
    par, var = _inputs(_rt(PARENT)), _inputs(_rt(VARIANT))
    for key in ("universe", "subGroups", "longBreadthPct", "shortBreadthPct", "minScore",
                "minTraderCount", "momModeratePct", "momStrongPct", "dex"):
        assert par[key] == var[key], f"{key} differs — that is a different strategy, not a variant"


# ── the variant contract itself ────────────────────────────────────────────────────────────

def test_it_declares_itself_a_variant_of_hornet():
    """`varies` is what folds the family into one card in discover, so a generic request can
    never surface both members (references/variant-families.md)."""
    assert _manifest(VARIANT)["catalog"]["varies"] == PARENT


def test_no_clock_closes_anything():
    d = _rt(VARIANT)["exit"]["dsl_preset"]
    for clock in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert clock in d, f"{clock} must be declared, not left to a default"
        assert d[clock]["enabled"] is False, f"{clock} is enabled; the ladder owns every exit"
