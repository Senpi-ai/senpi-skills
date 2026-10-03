"""The move must not already have happened.

Quant desk, 2026-10-02, on live Penguin entries: **55% fired after a >= 3% 1h pre-move, and those
ran a profit factor of 0.3 against 3.3** for entries taken before the move. Same detector, same
universe, same score — the only difference was how much of the move was left to capture.

**A second measurement disagrees, and both belong here.** Over 7 days of CAND telemetry to
2026-10-03, among candidates that cleared the score floor, only **16 of 578 penguin candidates
(2.8%)** and **0 of 84 pelican candidates (0.0%)** sat at or above a 3% pre-move. Units were
verified against Hyperliquid 1m candles — `priceChg1h` is a PERCENT, actual/reported 0.81-1.06
across 6 of 7 spot checks — so the comparison against 3.0 is correctly scaled and the gap is real,
not a units error. CAND samples CANDIDATES at scan time while the quant desk measured FILLED
ENTRIES, which are the top-scoring candidate rather than a random one; that selection effect should
bias entries later than candidates, but it should not turn 2.8% into 55%. **The 20x gap is
unexplained.** The gate is therefore kept as cheap, direction-aware insurance rather than as a
filter anyone should expect to bite often.

This is the one gate that reads PRICE rather than rank, which is exactly why the rank-jump model
cannot see it: a coin climbs the leaderboard *because* it already moved, so a large pre-move and a
high rank-jump score are the same event. Scoring higher does not mean arriving earlier.

The gate is **direction-aware**, which is the part that makes it safe. It rejects a LONG whose price
already ran up and a SHORT that already fell — chasing. A dip-buy (LONG after a fall) or a rally-sell
(SHORT after a rise) still passes, because there the pre-move is in the trade's favour rather than
spent.

It is **off unless a package opts in** via `maxPreMovePct`. The scorer is vendored across packages
whose own pre-move distributions have not been measured, so the default cannot change their
behaviour. Live on the penguin lineage; deliberately off on pelican and orca.

Run: python3 -m pytest strategies/tests/test_pre_move_gate.py -q
"""
import importlib.util
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# packages whose runtime.yaml must SET the gate, and the value measured for them
GATED = {"penguin": 3.0, "purple-penguin": 3.0, "penguin-x5": 3.0, "penguins-duo": 3.0,
         # pelican added 2026-10-03 on Jason's call. Measured first rather than assumed: over 7 days
         # of CAND telemetry, 0 of 84 pelican candidates that cleared the floor sat at or above 3%
         # (p50 +0.26%, p90 +0.88%, max +2.79%), so on its own distribution the gate would have
         # rejected nothing. Cheap insurance, not an active filter.
         "pelican": 3.0}
# vendors the same scorer but deliberately left off — a separate listed template with its own users,
# and no measurement of its own pre-move distribution yet
UNGATED = ["orca"]

_SIBLINGS = ("scoring", "score", "sweep", "smartmoney")


def _scorer(pkg="penguin"):
    """Load a package's own scoring.py, evicting any sibling of the same name (shared pytest run)."""
    d = os.path.join(ROOT, "strategies", pkg, "main", "scanners")
    saved = {n: sys.modules.pop(n) for n in _SIBLINGS if n in sys.modules}
    sys.path.insert(0, d)
    try:
        spec = importlib.util.spec_from_file_location(
            f"scoring_{pkg.replace('-', '_')}", os.path.join(d, "scoring.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(d)
        for n in _SIBLINGS:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


def _market(direction="LONG", chg_1h=0.0, chg_4h=None):
    """A market that clears the other hard gates, so only the pre-move gate can reject it."""
    if chg_4h is None:
        chg_4h = 5.0 if direction == "LONG" else -5.0     # 4h must agree with direction
    return {"token": "TEST", "dex": "", "rank": 30, "direction": direction,
            "contribution": 100.0, "traders": 20, "price_chg_4h": chg_4h,
            "price_chg_1h": chg_1h, "cc_15m": 1.0}


def _call(mod, market, cap):
    """Run the scorer far enough to exercise the hard gates. `None` means a gate rejected."""
    return mod.score_market(market, {"rank": 60, "contribution": 10.0}, {"rank": 70, "contribution": 5.0},
                            set(), [5.0, 10.0, 100.0], 15, floor=False, max_pre_move_pct=cap)


def test_a_long_that_already_ran_up_is_rejected():
    mod = _scorer()
    assert _call(mod, _market("LONG", chg_1h=3.5), 3.0) is None, (
        "a LONG whose price already rose 3.5% in the hour was accepted at a 3.0 cap — this is the "
        "chase the gate exists to stop (PF 0.3 vs 3.3).")


def test_a_short_that_already_fell_is_rejected():
    mod = _scorer()
    assert _call(mod, _market("SHORT", chg_1h=-3.5), 3.0) is None, (
        "a SHORT after a 3.5% fall was accepted. The gate must be direction-aware in BOTH "
        "directions, or it only protects longs.")


def test_a_dip_buy_still_passes():
    """The gate rejects chasing, not entering. A LONG after a FALL has its move ahead of it."""
    mod = _scorer()
    assert _call(mod, _market("LONG", chg_1h=-4.0), 3.0) is not None, (
        "a LONG after a 4% fall was rejected. The pre-move is in this trade's favour, not spent — "
        "rejecting it would gate on volatility rather than on lateness.")


def test_a_rally_sell_still_passes():
    mod = _scorer()
    assert _call(mod, _market("SHORT", chg_1h=4.0), 3.0) is not None, (
        "a SHORT after a 4% rally was rejected; same reasoning as the dip buy, mirrored.")


def test_the_boundary_is_inclusive():
    """55% of entries sat above this line, so which side of it the boundary falls on matters."""
    mod = _scorer()
    assert _call(mod, _market("LONG", chg_1h=3.0), 3.0) is None, "exactly at the cap must reject"
    assert _call(mod, _market("LONG", chg_1h=2.99), 3.0) is not None, "just under must pass"


def test_the_gate_is_off_when_no_cap_is_given():
    """The default must not change the behaviour of packages that have not opted in."""
    mod = _scorer()
    assert _call(mod, _market("LONG", chg_1h=50.0), None) is not None, (
        "a 50% pre-move was rejected with no cap set. The scorer is vendored across packages whose "
        "pre-move distributions are unmeasured; the default has to be a no-op.")


@pytest.mark.parametrize("pkg,cap", sorted(GATED.items()))
def test_the_gated_packages_ship_the_cap(pkg, cap):
    with open(os.path.join(ROOT, "strategies", pkg, "main", "runtime.yaml"), encoding="utf-8") as fh:
        rt = yaml.safe_load(fh)
    inp = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]
    assert float(inp.get("maxPreMovePct", -1)) == cap, (
        f"{pkg} does not ship maxPreMovePct: {cap}. An unset input leaves the gate OFF, so the "
        f"package silently keeps taking the late entries this was measured to remove.")


@pytest.mark.parametrize("pkg", UNGATED)
def test_the_ungated_packages_stay_ungated(pkg):
    """Not an oversight. These are separate listed templates with their own users, and the 55% /
    PF-0.3 finding was measured on penguin's entries. Turning it on here needs their own data —
    which is a decision, not a consistency sweep."""
    with open(os.path.join(ROOT, "strategies", pkg, "main", "runtime.yaml"), encoding="utf-8") as fh:
        rt = yaml.safe_load(fh)
    inp = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]
    assert "maxPreMovePct" not in inp, (
        f"{pkg} has gained maxPreMovePct. If that is intended, measure its own pre-move "
        f"distribution first and move it into GATED with the evidence.")


@pytest.mark.parametrize("pkg", sorted(GATED) + UNGATED)
def test_every_caller_actually_passes_the_parameter(pkg):
    """A parameter the scanner never forwards is a config that silently does nothing — the exact
    shape of the dead `minScore` floor this tree has already been bitten by."""
    with open(os.path.join(ROOT, "strategies", pkg, "main", "scanners", "scan.py"),
              encoding="utf-8") as fh:
        src = fh.read()
    assert 'inputs.get("maxPreMovePct")' in src, f"{pkg}/scan.py never reads maxPreMovePct"
    assert "max_pre_move_pct=max_pre_move_pct" in src, (
        f"{pkg}/scan.py reads the input but never forwards it to score_market, so setting it in "
        f"runtime.yaml would do nothing at all.")
