"""A score floor must be one of the package's OWN conviction bands, and must not drift down.

Raised 2026-10-01 after measuring 25 closes in 24h across the live fleet:

  * **15 of 25 never armed a single profit rung.** Their median peak was 2.0% ROE — about 0.2% of
    price. A position that never goes green dies wherever the stop is, so this is an ENTRY quality
    problem, not an exit one. (The exits were fixed the same day, separately: see
    test_one_price_ladder.py. That work was necessary and is not what this file is about.)
  * **Fees were 57% of gross** over that set — $287 on ~$505. The entire net came from two trades;
    the other 23 together lost $302. Churning low-conviction entries is the expensive part.
  * Only 2 of 25 reached rung 1 or beyond, and those two were the whole book.

**Each new floor is the package's own next conviction band**, read off `leverageTiers` — the author
already treated those scores as meaningful, so a floor that lands on one is derived rather than
invented. That matters because the alternative, picking a floor off a ratio, is how wild-condor ended
up at a floor so rare it took one trade in a day and needed a separate PR to accumulate a sample.

What this file does NOT claim: that these floors are optimal. The per-candidate `CAND` telemetry
that would show the score distribution behind those 15 dead entries only began flowing on
2026-10-01 and had not emitted yet, so nobody has measured which scores they actually were. These
floors are a reasoned step up the package's own ladder, to be revisited against CAND data.

Run: python3 -m pytest strategies/tests/test_score_floors.py -q
"""
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# pkg: (floor input key, floor, the package's own conviction bands from leverageTiers)
FLOORS = {
    "cheetah": ("minScore", 11, [10, 11, 12, 14]),
    "condor":  ("minScore", 13, [11, 13, 15]),
    "owl":     ("minCombinedScore", 14, [12, 14, 16]),
    "jaguar":  ("minScore", 10, [9, 10]),
    "raptor":  ("minScore", 8, [6, 8, 10]),
}

# Deliberately NOT raised. Each needs its own reason, because "raise them all" is the wrong move.
NOT_RAISED = {
    "orca": ("minScore", 9, "9 is penguin's floor and orca runs penguin's scorer over penguin's "
                            "universe. Penguin is the profitable one at that floor, so raising orca "
                            "above it would be stricter than the only value with evidence behind it. "
                            "orca's problem is 3 slots at 18% margin, not its floor."),
    "pangolin": ("minScore", 9, "took ZERO positions in the 24h measured. Its 12-entries-per-day cap "
                                "and 60s cooldown are theoretical, not binding. Raising the floor on "
                                "a strategy that is not trading makes it deader, not better."),
    "puffin": ("minScore", 85, "scores on its own 0-100 scale, not a component sum, and 85 is already "
                               "measured: over 14 days of feed output 82 of 116 observations cleared "
                               "65 but only 39 cleared 85, and 21 sit at exactly 100 so the scale "
                               "saturates. 85 is near the last level that still separates signals."),
}


def _inputs(pkg):
    with open(os.path.join(ROOT, "strategies", pkg, "main", "runtime.yaml"), encoding="utf-8") as fh:
        rt = yaml.safe_load(fh)
    return next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]


@pytest.mark.parametrize("pkg", sorted(FLOORS))
def test_the_floor_is_what_was_shipped(pkg):
    key, want, _bands = FLOORS[pkg]
    got = _inputs(pkg).get(key)
    assert got is not None, (
        f"{pkg} no longer declares `{key}` in runtime.yaml. An undeclared floor silently falls back "
        f"to the scanner module default, which is a fallback and not the shipped value (#677).")
    assert float(got) == float(want), (
        f"{pkg}'s {key} is {got}, shipped value is {want}. Lowering a floor is a deliberate act: "
        f"15 of 25 live closes never armed a rung, with a median peak of 0.2% of price, and fees ran "
        f"57% of gross. If new evidence says lower, change it HERE with that evidence attached.")


@pytest.mark.parametrize("pkg", sorted(FLOORS))
def test_the_floor_lands_on_one_of_the_packages_own_conviction_bands(pkg):
    """A floor on a band is derived; a floor between bands was invented.

    `leverageTiers` records the scores the author already treated as conviction steps, so a floor
    that coincides with one inherits that judgement. condor is the cautionary case: its old floor of
    12 sat between its own bands of 11 and 13, meaning it fired on a score its own sizing table did
    not recognise."""
    key, want, bands = FLOORS[pkg]
    tiers = _inputs(pkg).get("leverageTiers") or []
    actual = sorted({int(t[0]) for t in tiers})
    assert actual == sorted(bands), (
        f"{pkg}'s conviction bands moved from {sorted(bands)} to {actual}. The floor is derived from "
        f"these, so re-derive it and update FLOORS rather than leaving the two out of step.")
    assert want in actual, (
        f"{pkg}'s floor {want} is not one of its conviction bands {actual} — it sits between them, "
        f"so it fires on scores its own sizing table does not recognise.")


@pytest.mark.parametrize("pkg", sorted(FLOORS))
def test_a_band_below_the_floor_is_unreachable_and_that_is_recorded(pkg):
    """Raising a floor orphans the bands beneath it. They are kept on purpose — they record the
    author's calibration and come back if an experiment lowers the floor — but the file has to SAY
    so, or the next reader takes an unreachable tier for live behaviour."""
    key, want, bands = FLOORS[pkg]
    dead = [b for b in bands if b < want]
    if not dead:
        pytest.skip(f"{pkg} has no band below its floor")
    with open(os.path.join(ROOT, "strategies", pkg, "main", "runtime.yaml"), encoding="utf-8") as fh:
        src = fh.read()
    assert "UNREACHABLE" in src, (
        f"{pkg} has conviction band(s) {dead} below its floor of {want} with nothing in the file "
        f"saying they can never fire. Either note it, or remove the dead band(s).")


@pytest.mark.parametrize("pkg", sorted(NOT_RAISED))
def test_a_floor_left_alone_stays_left_alone(pkg):
    """These three were excluded for specific measured reasons. "Raise them all for consistency" is
    the change this guards against — consistency across packages with different universes, different
    scorers and different trade counts is not a reason."""
    key, want, _why = NOT_RAISED[pkg]
    got = _inputs(pkg).get(key)
    assert got is not None and float(got) == float(want), (
        f"{pkg}'s {key} moved to {got}; it is deliberately held at {want}. Reason on record: "
        f"{NOT_RAISED[pkg][2]}")


def test_every_wild_variant_still_sits_above_its_raised_parent():
    """The wild variants exist to ask for MORE conviction than the parent. Raising a parent's floor
    can silently erase that gap, which would make the experiment meaningless rather than wrong."""
    pairs = [("wild-cheetah", "cheetah"), ("wild-condor", "condor")]
    for wild, parent in pairs:
        w = float(_inputs(wild)["minScore"])
        p = float(_inputs(parent)["minScore"])
        assert w > p, (
            f"{wild}'s floor ({w}) no longer exceeds {parent}'s ({p}) after the parent was raised. "
            f"The variant has to ask for more than its parent or it is not a higher-conviction "
            f"experiment — raise {wild} too, or lower {parent} back with evidence.")
