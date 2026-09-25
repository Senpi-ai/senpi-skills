"""What PUFFIN changes about SIGNALS-HUNTER, pinned: the ring, the margin cap, and the card.

Three things here have already been shipped wrong somewhere in this tree, which is why each one is
a test rather than a comment:

  1. **The ring.** score.py picks both diff baselines by wall-clock AGE. A count-tailed ring
     therefore ties what the detectors can SEE to the tick interval, and shortening the tick from
     an hour to 15 minutes silently hands the "12h" trend arm a 6h baseline — which it does not
     refuse, it just reports as a trend. Fail-open, invisible, and the exact shape of the dead
     volume gate (strategies/tests/test_no_phantom_asset_context_keys.py).
  2. **The margin cap.** `maxTotalMarginPct` defaults to 80 in the module SIGNALS-HUNTER ships. At
     a 90% tier that rejects every signal on an EMPTY book, forever, while every tick reports
     itself healthy. A concentrated fork that forgets to raise it never opens a position.
  3. **The card.** `belief_plain` quotes a per-stop-out cost and a gross exposure. Those are
     arithmetic on max_loss_pct, marginPct and leverage, so they can go stale in a retune — the
     athena-x failure that strategies/tests/test_card_self_consistency.py exists for.

Run: python3 -m pytest strategies/puffin/tests -q
"""
import datetime
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import score  # noqa: E402

RT = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
CARD = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SCANNER = next(s for s in RT["scanners"] if s["name"] == "puffin_scan")
IN = SCANNER["inputs"]
DSL = RT["exit"]["dsl_preset"]
LEV = IN["leverage"]


def _ring(interval_seconds, hours, now):
    """A ring as this scanner would actually build one: one snapshot per tick, oldest first."""
    step = interval_seconds / 60.0
    n = int(hours * 60 / step)
    return [{"ts": (now - datetime.timedelta(minutes=step * (n - i))).isoformat(),
             "asset_metrics": {"A": {"smart_share": 50.0, "smart_source": "proven_cohort"}}}
            for i in range(n)]


def _arms(ring, now):
    """(fast baseline age, slow baseline age) in minutes, as score.py would resolve them."""
    fast = score._pick_baseline(ring, now)
    ts = score._parse_ts((fast or {}).get("ts"))
    fast_age = (now - ts).total_seconds() / 60.0 if ts else None
    _, slow_age = score.make_slow_lookup(ring, now)("A", "proven_cohort")
    return fast_age, slow_age


# ── 1. the ring ──

def test_the_shipped_clock_still_feeds_both_detector_arms():
    """The regression this package exists around. At the interval runtime.yaml actually ships,
    the stratified ring must give the fast arm a ~60min partner and the slow arm one near 12h and
    comfortably above the refusal floor — otherwise the trend detector is dead on arrival."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ring = _ring(SCANNER["interval_seconds"], 30, now)
    kept = scan._stratify_ring(ring, now, IN["ringMax"], IN["ringDenseMinutes"],
                               IN["ringSparseStepMinutes"])
    fast_age, slow_age = _arms(kept, now)
    assert len(kept) <= IN["ringMax"], f"stratified ring overran its own cap: {len(kept)}"
    assert fast_age is not None and abs(fast_age - score.DIFF_TARGET_MIN) <= 30, (
        f"fast arm baseline is {fast_age}min, wanted ~{score.DIFF_TARGET_MIN}min")
    assert slow_age is not None, (
        "the ~12h trend arm has NO baseline at the shipped interval — it will never fire")
    assert slow_age >= score.TREND_MIN_AGE_MIN, (
        f"slow baseline {slow_age}min is under score.TREND_MIN_AGE_MIN "
        f"({score.TREND_MIN_AGE_MIN}) — make_slow_lookup discards it")
    assert abs(slow_age - score.TREND_LOOKBACK_MIN) <= 120, (
        f"slow baseline {slow_age}min is far from the intended {score.TREND_LOOKBACK_MIN}min arm")


def test_the_inherited_tail_keep_would_break_that():
    """Guard the guard. If a plain `ring[-ringMax:]` also passed the test above, _stratify_ring
    would be decoration and someone would rightly delete it.

    It does not. At 24 snapshots x 15min the tail spans exactly 6h, so the slow arm lands on
    precisely TREND_MIN_AGE_MIN — the boundary make_slow_lookup compares with `age <`, so it is
    ACCEPTED rather than refused. That is the worst of the three outcomes: the trend detector does
    not go dark and does not error, it reports a 6h baseline as the ~12h arm. Half the lookback,
    same name, nothing in the logs."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ring = _ring(SCANNER["interval_seconds"], 30, now)
    _, tail_slow = _arms(ring[-IN["ringMax"]:], now)
    _, kept_slow = _arms(scan._stratify_ring(ring, now, IN["ringMax"], IN["ringDenseMinutes"],
                                             IN["ringSparseStepMinutes"]), now)
    assert tail_slow is None or tail_slow <= score.TREND_MIN_AGE_MIN, (
        f"a count-tailed ring gave the slow arm {tail_slow}min — if that is now adequate, "
        f"_stratify_ring is no longer earning its place")
    assert kept_slow is not None and kept_slow > (tail_slow or 0), (
        f"stratifying did not improve the slow arm: tail={tail_slow} stratified={kept_slow}")


def test_an_hourly_clock_is_still_served():
    """The stratification must not be tuned so tightly to 15 minutes that it breaks the cadence it
    was forked from — that would make the function un-reusable and the comparison above dishonest."""
    now = datetime.datetime.now(datetime.timezone.utc)
    kept = scan._stratify_ring(_ring(3600, 30, now), now, IN["ringMax"], IN["ringDenseMinutes"],
                               IN["ringSparseStepMinutes"])
    fast_age, slow_age = _arms(kept, now)
    assert fast_age is not None and abs(fast_age - score.DIFF_TARGET_MIN) <= 30, fast_age
    assert slow_age is not None and slow_age >= score.TREND_MIN_AGE_MIN, slow_age


def test_a_starved_ring_says_so_instead_of_going_quiet(capsys):
    """The dense tier is taken first, so a dense window that fills the cap on its own leaves no
    room for the sparse one and the trend arm dies. Reachable by lowering interval_seconds or
    raising ringDenseMinutes. It must be loud, because every other detector keeps working."""
    now = datetime.datetime.now(datetime.timezone.utc)
    kept = scan._stratify_ring(_ring(300, 30, now), now, 24, 120, 120)   # 5-min tick: 24 dense
    _, slow_age = _arms(kept, now)
    assert slow_age is None, "this fixture no longer starves the ring — re-pick it"
    err = capsys.readouterr().err
    assert "WARNING" in err and "TREND_MIN_AGE_MIN" in err and "ringDenseMinutes" in err, err


def test_unparseable_snapshots_are_dropped_not_carried():
    now = datetime.datetime.now(datetime.timezone.utc)
    ring = _ring(900, 8, now) + [{"ts": "not-a-timestamp", "asset_metrics": {"A": {}}}]
    kept = scan._stratify_ring(ring, now, IN["ringMax"], IN["ringDenseMinutes"],
                               IN["ringSparseStepMinutes"])
    assert all(score._parse_ts(s["ts"]) is not None for s in kept)


def test_the_ring_is_returned_oldest_first():
    """`scan()` appends the new snapshot with `ring.append`, so the rest of the file assumes that
    order. Reversing it would not raise — it would just quietly change which snapshot the cap drops."""
    now = datetime.datetime.now(datetime.timezone.utc)
    kept = scan._stratify_ring(_ring(900, 30, now), now, IN["ringMax"], IN["ringDenseMinutes"],
                               IN["ringSparseStepMinutes"])
    ages = [(now - score._parse_ts(s["ts"])).total_seconds() for s in kept]
    assert ages == sorted(ages, reverse=True), "ring came back newest-first"


# ── 2. the margin cap ──

def test_the_cap_admits_the_top_tier_or_nothing_ever_opens():
    assert IN["maxTotalMarginPct"] > IN["highMarginPct"], (
        f"maxTotalMarginPct {IN['maxTotalMarginPct']} <= highMarginPct {IN['highMarginPct']}: the "
        f"emit guard rejects every signal on an empty book and the strategy never trades")
    assert scan.DEFAULT_MAX_TOTAL_MARGIN_PCT > scan.DEFAULT_HIGH_MARGIN_PCT


def test_the_cap_blocks_a_second_position_independently_of_slots():
    """`slots` is the runtime's guard; this is the scanner's. Both must say one."""
    assert IN["maxTotalMarginPct"] < 2 * IN["marginPct"], (
        "two base-tier positions fit under the cap — the scanner would emit a second one")
    assert RT["strategy"]["slots"] == 1


@pytest.mark.parametrize("key,mod", [("minScore", "DEFAULT_MIN_SCORE"),
                                     ("highScoreThreshold", "DEFAULT_HIGH_SCORE"),
                                     ("marginPct", "DEFAULT_MARGIN_PCT"),
                                     ("highMarginPct", "DEFAULT_HIGH_MARGIN_PCT"),
                                     ("leverage", "DEFAULT_LEVERAGE"),
                                     ("familyCap", "DEFAULT_FAMILY_CAP"),
                                     ("maxTotalMarginPct", "DEFAULT_MAX_TOTAL_MARGIN_PCT")])
def test_module_defaults_mirror_the_shipped_inputs(key, mod):
    """Per-signal sizing outranks the strategy block, so `inputs:` is what ships — and a fork whose
    module defaults still hold the ancestor's numbers trades as the ancestor the moment that block
    goes missing. Nothing in the runtime warns; the two just have to agree."""
    assert float(getattr(scan, mod)) == float(IN[key]), (
        f"scan.{mod} and runtime.yaml inputs.{key} disagree")


def test_the_strategy_block_mirrors_the_signal_sizing_too():
    assert RT["strategy"]["margin_pct"] == IN["marginPct"]
    assert RT["strategy"]["default_leverage"] == IN["leverage"]


# ── 3. the clock ──

def test_the_scan_is_faster_than_the_package_it_forked():
    sh = yaml.safe_load(open(os.path.join(HERE, "..", "..", "signals-hunter", "main",
                                          "runtime.yaml"), encoding="utf-8"))
    parent = next(s for s in sh["scanners"] if s["type"] == "external_scanner")
    assert SCANNER["interval_seconds"] < parent["interval_seconds"]
    assert SCANNER["timeout_seconds"] < SCANNER["interval_seconds"], "timeout must fit the tick"
    assert SCANNER["default_signal_validity_seconds"] <= 2 * SCANNER["interval_seconds"], (
        "a signal outliving two ticks can fire long after the reading that produced it")


# ── 4. the card is arithmetic ──

def test_the_card_quotes_the_stop_cost_the_config_actually_implies():
    """belief_plain: 'one stop-out takes about 19-23% of the account'."""
    stop = DSL["phase1"]["max_loss_pct"] / 100.0
    lo, hi = stop * IN["marginPct"], stop * IN["highMarginPct"]
    assert (18.0 <= lo <= 20.0) and (22.0 <= hi <= 24.0), (
        f"a stop-out now costs {lo:.1f}-{hi:.1f}% of the account; the card says 19-23%")


def test_the_card_quotes_the_gross_exposure_the_config_actually_implies():
    """tagline/thesis: 'up to 9x gross exposure'. test_card_self_consistency allows exactly one
    such figure per card, so this pins that figure to the config rather than to a memory of it."""
    assert IN["highMarginPct"] / 100.0 * LEV == 9.0
    assert CARD["catalog"]["leverage_max"] == LEV
    assert CARD["catalog"]["max_slots"] == RT["strategy"]["slots"]


def test_every_dsl_threshold_is_stated_in_roe_at_this_leverage():
    """SIGNALS-HUNTER's ladder is tuned for 5x and its own comment warns that carrying those ROE
    numbers onto a different leverage rescales every exit in price. This package is 10x, so the
    ladder must rise monotonically and start no tighter than the stop it sits above."""
    tiers = DSL["phase2"]["tiers"]
    trig = [t["trigger_pct"] for t in tiers]
    lock = [t["lock_hw_pct"] for t in tiers]
    assert trig == sorted(trig) and len(set(trig)) == len(trig), trig
    assert lock == sorted(lock) and len(set(lock)) == len(lock), lock
    assert trig[0] >= DSL["phase1"]["max_loss_pct"], (
        "the first profit tier arms tighter than the stop — it would lock in before risk is off")
    assert DSL["phase1"]["enabled"] is False and DSL["phase1"]["max_loss_pct"] > 0, (
        "phase1 trailing is off here, so max_loss_pct is the ONLY floor and must be set")
