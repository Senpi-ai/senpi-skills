"""A time cut may not fire inside the horizon the template claims to trade.

THE RULE (Jason, 15 Oct 2026): a strategy meant to hold for a long period should carry no
weak-peak cut and no hard timed close. Only genuinely short-term sniping should. The codebase
already said so -- camel's own runtime.yaml records that weak_peak "closes at MARKET at
whatever price is there, with no floor, so on a longer-hold book it realises a small LOSS on a
position that simply hadn't moved yet" and concludes it "is a CHOP tool only: it belongs where
the strategy already intends to be out within hours". That doctrine was never enforced, so it
drifted: measured against actual hold times, cougar was cutting a 47-hour position book at 16
hours and dog was cutting a 96-minute median at 90 minutes.

WHY A LINT AND NOT A REVIEW HABIT. The drift happened because nothing compared the two numbers.
`time_horizon` lives in strategy.yaml and the cut intervals live in runtime.yaml, so no single
file is wrong on its own -- only the pair is. That is exactly what a test is for.

ONE DELIBERATE EXCEPTION, and it is an architectural point rather than a loophole: a clock is
indefensible as a THESIS exit but defensible as SLOT RECOVERY. None of these packages ships a
CLOSE_POSITION scanner, so the ladder and the stop are the only other ways out; on a one-slot
book a position that neither locks nor stops would hold the entire strategy forever. So a cut
is allowed to exist far outside the hold distribution as a backstop -- dog's was widened from
360m to 2880m rather than deleted -- and BACKSTOP_FLOOR_MINUTES is where "far outside" is
defined per horizon.

Run: /usr/bin/python3 -B -m pytest strategies/tests/test_time_cuts_match_horizon.py -q
"""
import glob
import json
import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# A timed cut is only permitted at or beyond this interval, per declared horizon. Below it the
# cut is reaching into live theses; at or above it the cut can only recover a stalled slot.
BACKSTOP_FLOOR_MINUTES = {
    "scalp": 0,          # the cut IS the strategy here -- spider/scalp holds a median of 30m
    "intraday": 60,      # out within the day, so an hour-plus cut is coherent
    "swing": 2880,       # days: nothing may cut before 48h
    "position": 7200,    # weeks: nothing may cut before 5d
}
# weak_peak closes at market with no floor, so it is restricted to horizons that intend to be
# out within hours -- regardless of its interval.
WEAK_PEAK_HORIZONS = {"scalp", "intraday"}

# Pre-existing violations live in a data file, not here: there are 78 of them across 49
# packages, which is the honest state of the drift and too long to read inline. The file is a
# SHRINK-ONLY backlog -- a new violation fails, and so does an entry that stops violating.
BACKLOG_PATH = os.path.join(ROOT, "references", "time-cut-backlog.json")


def _backlog():
    with open(BACKLOG_PATH, encoding="utf-8") as fh:
        return {(e["package"], e["instance"], e["cut"]) for e in json.load(fh)["entries"]}


def _blocks(path):
    """{cut_name: (enabled, interval_minutes)} for both the inline and multi-line YAML forms."""
    s = open(path, encoding="utf-8").read()
    out = {}
    for key in ("weak_peak_cut", "dead_weight_cut", "hard_timeout"):
        body = None
        m = re.search(rf'{key}:\s*\{{([^}}]*)\}}', s)
        if m:
            body = m.group(1)
        else:
            m2 = re.search(rf'^(\s*){key}:\s*\n((?:\1\s+.*\n|\s*\n)*)', s, re.M)
            if m2:
                body = m2.group(2)
        if body is None:
            continue
        en = re.search(r'enabled:\s*(\w+)', body)
        iv = re.search(r'interval_in_minutes:\s*([0-9.]+)', body)
        out[key] = (bool(en and en.group(1) == "true"),
                    float(iv.group(1)) if iv else None)
    return out


def _horizon(pkg):
    man = os.path.join(ROOT, pkg, "strategy.yaml")
    if not os.path.exists(man):
        return None
    m = re.search(r'^\s*time_horizon:\s*["\']?([A-Za-z_]+)["\']?\s*$',
                  open(man, encoding="utf-8").read(), re.M)
    return m.group(1) if m else None


def _enabled_cuts():
    for rt in sorted(glob.glob(os.path.join(ROOT, "*", "*", "runtime.yaml"))):
        pkg, inst = rt.split(os.sep)[-3], rt.split(os.sep)[-2]
        for cut, (on, interval) in _blocks(rt).items():
            if on:
                yield pkg, inst, cut, interval, _horizon(pkg)


def _violation(pkg, inst, cut, interval, horizon):
    """The reason this cut breaks the rule, or None."""
    if horizon is None:
        return "an enabled time cut with no declared time_horizon — nothing to check it against"
    if cut == "weak_peak_cut" and horizon not in WEAK_PEAK_HORIZONS:
        return (f"weak_peak_cut on horizon={horizon}; it closes at market with no floor and "
                f"belongs only on {sorted(WEAK_PEAK_HORIZONS)}")
    floor = BACKSTOP_FLOOR_MINUTES.get(horizon)
    if floor is not None and interval is not None and interval < floor:
        return (f"{cut} at {interval:g}m on horizon={horizon}, inside the {floor}m backstop "
                f"floor — it is cutting live theses, not recovering a stalled slot")
    return None


def test_no_new_time_cut_fires_inside_its_declared_horizon():
    bad = []
    for pkg, inst, cut, interval, horizon in _enabled_cuts():
        why = _violation(pkg, inst, cut, interval, horizon)
        if why and (pkg, inst, cut) not in _backlog():
            bad.append(f"{pkg}/{inst}: {why}")
    assert not bad, (
        "A time cut fires inside the horizon its template claims to trade:\n  "
        + "\n  ".join(sorted(bad))
        + "\n\nEither widen the cut past the backstop floor for that horizon, declare the "
          "horizon the strategy actually trades, or turn the cut off. Do NOT add it to "
          "the backlog file — that list only shrinks.")


def test_the_backlog_cannot_rot():
    """An entry that no longer violates must be deleted, so the backlog cannot drift into a
    blanket exemption that hides a real regression behind a stale name."""
    live = {(p, i, c): (iv, h) for p, i, c, iv, h in _enabled_cuts()}
    stale = []
    for key in sorted(_backlog()):
        if key not in live:
            stale.append(f"{'/'.join(key)}: cut is no longer enabled — delete this row")
            continue
        interval, horizon = live[key]
        if _violation(key[0], key[1], key[2], interval, horizon) is None:
            stale.append(f"{'/'.join(key)}: no longer violates — delete this row")
    assert not stale, (
        "strategies/references/time-cut-backlog.json is out of date:\n  " + "\n  ".join(stale))


@pytest.mark.parametrize("spec", [
    ("cougar", "long", "dead_weight_cut"), ("cougar", "short", "dead_weight_cut"),
    ("lion", "long", "dead_weight_cut"), ("lion", "short", "dead_weight_cut"),
    ("dog", "main", "weak_peak_cut"), ("dog", "main", "dead_weight_cut"),
    ("orca", "main", "weak_peak_cut"),
])
def test_the_cuts_fixed_here_stay_off(spec):
    """Measured violations closed on 15 Oct. Re-enabling any of these needs a replay showing
    the cut harvests an edge, not a preference."""
    pkg, inst, cut = spec
    on, _ = _blocks(os.path.join(ROOT, pkg, inst, "runtime.yaml"))[cut]
    assert not on, f"{pkg}/{inst}: {cut} was re-enabled without a replay behind it"


def test_the_sniper_keeps_its_cuts():
    """The rule is two-sided. spider/scalp holds a median of 30 minutes and its weak_peak is at
    30 minutes — the cut is what MAKES it a sniper, and removing it would be the same mistake
    in the other direction. An earlier pass nearly did, off a hold time pooled across both of
    spider's instances."""
    b = _blocks(os.path.join(ROOT, "spider", "scalp", "runtime.yaml"))
    assert b["weak_peak_cut"][0], "spider/scalp lost the weak_peak cut that defines it"
    assert b["weak_peak_cut"][1] <= 60, "spider/scalp's weak_peak is no longer a sniper's cut"


def test_dogs_clock_is_a_backstop_not_a_thesis_exit():
    """dog runs ONE slot with no CLOSE_POSITION scanner, so some clock must remain or a stalled
    position holds the strategy forever. It must sit far outside the hold distribution: p90 is
    393m, so anything under 2000m is reaching into live trades again."""
    on, interval = _blocks(os.path.join(ROOT, "dog", "main", "runtime.yaml"))["hard_timeout"]
    assert on, "dog has one slot and no invalidation scanner — it needs a slot-recovery backstop"
    assert interval >= 2000, f"dog's backstop at {interval:g}m is back inside the hold distribution"
