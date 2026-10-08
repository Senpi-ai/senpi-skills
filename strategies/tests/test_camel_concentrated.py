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
    # One rung was ADDED below camel's first, 2026-10-07, from Jason's live fork: "start locking
    # 40% at 15% instead of waiting until 28%". Camel's own four must still be present at camel's
    # exact price distances — the addition may not reprice the inherited ladder, which is the
    # failure mode the one-price-ladder rule exists to stop (#821).
    assert got[-len(want):] == want, (
        f"camel's own rungs were repriced: {got[-len(want):]} != camel's {want}")
    assert got[:-len(want)] == [EXTRA_RUNG_PX], (
        f"the only rung above camel's ladder should be the declared {EXTRA_RUNG_PX}% addition; "
        f"found {got[:-len(want)]}")
    assert got == sorted(got), f"the ladder is not ascending in price: {got}"
    assert [t["lock_hw_pct"] for t in da["phase2"]["tiers"]][-len(want):] == \
           [t["lock_hw_pct"] for t in dp["phase2"]["tiers"]], "camel's inherited locks changed"


EXTRA_RUNG_PX = 1.5          # % of price — the rung added on 2026-10-07
EXTRA_RUNG_LOCK = 40


@pytest.mark.parametrize("arm", ARMS)
def test_the_added_rung_is_the_one_that_was_asked_for(arm):
    """Pinned with its measured cost, so nobody "simplifies" it back out or retunes the lock
    without the numbers.

    Measured on camel's own book, 164 round trips across 5 live wallets to 2026-10-07:
    favourable peak p50 1.78% / p75 3.45%, adverse excursion p50 1.28% / p75 1.60%.

    So rung 0 at 1.50% of price arms on 86/164 = 52% of positions — it engages. Its leash is
    (1 - 0.40) x 1.50 = 0.90% of price, INSIDE the 1.28% median bounce, so once armed it usually
    fires and banks +0.60% of price. That is a deliberate trade of upside for hit-rate.

    And the thing to know before retuning the LOCK rather than the trigger: at a 1.50% trigger NO
    lock escapes the median bounce (30 -> 1.05%, 25 -> 1.12%, even 0 -> 1.50% clears only p50).
    The trigger is the only lever, and rung 1 at 2.80% already is it."""
    t = _rt(ARM_PKG, arm)["exit"]["dsl_preset"]["phase2"]["tiers"][0]
    lev = _rt(ARM_PKG, arm)["strategy"]["default_leverage"]
    assert t["trigger_pct"] / lev == EXTRA_RUNG_PX, (
        f'rung 0 arms at {t["trigger_pct"]/lev}% of price, not the asked-for {EXTRA_RUNG_PX}%')
    assert t["lock_hw_pct"] == EXTRA_RUNG_LOCK, (
        f'rung 0 locks {t["lock_hw_pct"]}% of high-water, not {EXTRA_RUNG_LOCK}%. Changing this is '
        f'a product decision — see this test\'s docstring for what the lock can and cannot buy.')


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


# ─────────────────────────────────────────────────────────────────────────────────────────────
# The 24h overextension gate (2026-10-08)
#
# Measured on this package's own live book, 2026-10-06/08, 8 round trips on 2 coins. The two
# FIRST entries — taken at -4.4% and +8.0% on the day — netted +$649.94. The four MET
# re-entries — taken at +37.4%, +50.2%, +61.5% and +46.8% — grossed +$19.30 against $20.55 of
# fees: net -$1.25 on ~$13.9k of churned notional. The funding signal outlives the price edge,
# so the scanner kept re-ranking a name that had already paid out.
#
# The cooldown is NOT the lever: every one of the six re-entries opened 10,849-10,996s after
# that coin's prior close — the 10,800s per-asset cooldown plus one 300s tick. It is a binary
# switch, and raising it blocks SAND's +$457 of profitable re-entries along with MET's nothing.
# The gate discriminates where the cooldown cannot.
# ─────────────────────────────────────────────────────────────────────────────────────────────

OVEREXTENSION_CAP = 20          # % 24h move, absolute, both legs

# (coin, own24h at the real entry, net $ after fees, expect_blocked)
LIVE_ENTRIES = [
    ("SAND", -4.4, 86.72, False), ("SAND", 5.1, 73.99, False), ("SAND", 6.6, 383.25, False),
    ("MET", 8.0, 563.22, False), ("MET", 37.4, -148.28, True), ("MET", 50.2, 82.15, True),
    ("MET", 61.5, 232.69, True), ("MET", 46.8, -167.81, True),
]


def _scorer(arm):
    """Load the arm's scoring.py BY PATH. Never sys.path.insert — four packages vendor a module
    called `scoring`, and inserting one onto the path silently rebinds it for every later test in
    a shared pytest run."""
    import importlib.util
    path = os.path.join(ROOT, "strategies", ARM_PKG, arm, "scanners", "scoring.py")
    spec = importlib.util.spec_from_file_location(f"_scoring_{arm}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _candles(n, px=1.0):
    return [{"c": str(px), "h": str(px * 1.01), "l": str(px * 0.99), "o": str(px), "v": "1000"}
            for _ in range(n)]


@pytest.mark.parametrize("arm", ARMS)
def test_the_overextension_gate_is_enabled_on_both_legs(arm):
    cap = _inputs(_rt(ARM_PKG, arm)).get("maxOwn24hPct")
    assert cap is not None, (
        f"{arm}: maxOwn24hPct is absent, so the gate is DISABLED — the scorer treats a missing "
        f"cap as opt-out and a name up 60% on the day scores identically to one up 0.1%")
    assert float(cap) == OVEREXTENSION_CAP, f"{arm}: cap is {cap}, expected {OVEREXTENSION_CAP}"


@pytest.mark.parametrize("arm", ARMS)
def test_standalone_camel_is_NOT_gated(arm):
    """The gate is opt-in precisely so the parent keeps the behaviour it was measured on. camel
    runs four slots at 18% each, where one chased re-entry costs ~1% of the arm instead of ~8.6%,
    and it has live users. Enabling it there is a separate decision with its own evidence."""
    assert "maxOwn24hPct" not in _inputs(_rt(PARENT, arm)), (
        f"camel/{arm} is now gated — that changes a template other people are running")


@pytest.mark.parametrize("arm", ARMS)
def test_the_gate_blocks_exactly_the_live_entries_that_paid_nothing(arm):
    """The measurement, pinned. Replays each real entry's 24h move through the shipped scorer."""
    sc = _scorer(arm)
    leg_sign = 1 if arm == "payout" else -1
    fund = -0.0002 if arm == "payout" else 0.0002
    inputs = dict(_inputs(_rt(ARM_PKG, arm)))
    blocked_net = kept_net = 0.0
    for coin, own, net, expect_blocked in LIVE_ENTRIES:
        res = sc.score_carry(coin, _candles(10), _candles(8), fund, own * leg_sign, arm, inputs)
        got_blocked = res is None
        assert got_blocked == expect_blocked, (
            f"{arm}/{coin} at own24h={own * leg_sign:+.1f}%: "
            f"{'blocked' if got_blocked else 'allowed'}, expected "
            f"{'blocked' if expect_blocked else 'allowed'}")
        if got_blocked:
            blocked_net += net
        else:
            kept_net += net
    assert round(blocked_net, 2) == -1.25, f"blocked set nets {blocked_net:+.2f}, expected -1.25"
    assert round(kept_net, 2) == 1107.18, f"kept set nets {kept_net:+.2f}, expected +1107.18"


@pytest.mark.parametrize("arm", ARMS)
def test_the_gate_is_opt_out_by_absence_not_by_zero(arm):
    """A cap of 0 would block EVERYTHING on one side — absence is the only off switch, and the
    parent relies on it. Asserted on the scorer so the contract cannot drift."""
    sc = _scorer(arm)
    fund = -0.0002 if arm == "payout" else 0.0002
    extreme = 61.5 * (1 if arm == "payout" else -1)
    assert sc.score_carry("X", _candles(10), _candles(8), fund, extreme, arm, {}) is not None, (
        f"{arm}: an absent maxOwn24hPct must NOT gate — standalone camel depends on it")
    assert sc.score_carry("X", _candles(10), _candles(8), fund, extreme, arm,
                          {"maxOwn24hPct": OVEREXTENSION_CAP}) is None


@pytest.mark.parametrize("arm", ARMS)
def test_the_gate_fires_on_the_correct_SIDE_for_each_leg(arm):
    """Direction-aware: payout (long) skips a name that already RALLIED, harvest (short) one that
    already CRASHED. Getting the sign backwards would skip exactly the entries worth taking."""
    sc = _scorer(arm)
    fund = -0.0002 if arm == "payout" else 0.0002
    inputs = {"maxOwn24hPct": OVEREXTENSION_CAP}
    against = -50.0 if arm == "payout" else 50.0      # moved AGAINST the carry — must still pass
    assert sc.score_carry("X", _candles(10), _candles(8), fund, against, arm, inputs) is not None, (
        f"{arm}: a {against:+.0f}% move against the carry direction must NOT be gated — that is "
        f"the dislocation the strategy exists to buy")


@pytest.mark.parametrize("arm", ARMS)
def test_the_dedup_ttl_mirrors_the_per_asset_cooldown(arm):
    """`recentSignalTtlSeconds` was 180 against a 300s scan interval, so every entry in the
    in-scanner dedup map expired before the next tick could read it and the dedup could never
    fire. kodiak mirrors its cooldown for exactly this reason; phalanx was fixed for it too."""
    rt = _rt(ARM_PKG, arm)
    scanner = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")
    ttl = float(_inputs(rt)["recentSignalTtlSeconds"])
    interval = float(scanner["interval_seconds"])
    cooldown = float(rt["risk"]["guard_rails"]["per_asset_cooldown_seconds"])
    assert ttl > interval, (
        f"{arm}: TTL {ttl}s <= scan interval {interval}s — the dedup map is unconditionally empty "
        f"at read time, which is a silently dead gate rather than a loose one")
    assert ttl >= cooldown, f"{arm}: TTL {ttl}s should mirror the {cooldown}s per-asset cooldown"


@pytest.mark.parametrize("arm", ARMS)
def test_the_three_hour_per_asset_cooldown_is_KEPT(arm):
    """Deliberately unchanged. Every live re-entry opened 10,849-10,996s after that coin's prior
    close — the cooldown plus one tick — so it demonstrably works, and raising it is all-or-
    nothing: it would have blocked SAND's +$457 of profitable re-entries too."""
    assert _rt(ARM_PKG, arm)["risk"]["guard_rails"]["per_asset_cooldown_seconds"] == 10800


@pytest.mark.parametrize("arm", ARMS)
def test_phase1_trailing_stays_off_and_the_ladder_is_untouched(arm):
    """The other half of the 2026-10-08 decision: keep the DSL exactly as it is."""
    dsl = _rt(ARM_PKG, arm)["exit"]["dsl_preset"]
    assert dsl["phase1"]["enabled"] is False, f"{arm}: phase-1 trailing was re-enabled"
    assert dsl["phase1"]["max_loss_pct"] == 12, f"{arm}: the stop moved"
    assert [(t["trigger_pct"], t["lock_hw_pct"]) for t in dsl["phase2"]["tiers"]] == [
        (15, 40), (28, 45), (56, 65), (100, 80), (180, 90)], f"{arm}: the ladder moved"
