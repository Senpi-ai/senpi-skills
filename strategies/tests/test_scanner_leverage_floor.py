"""A ROE ladder is implicitly authored for one leverage. The venue can silently pick another.

Every DSL threshold is ROE, and the engine converts ROE to a price floor by DIVIDING BY LEVERAGE
(senpi-strategy-author/references/dsl-configuration.md). So a ladder authored at Nx stretches every
exit distance in PRICE by N/actual whenever the venue caps an asset below N. A 10x ladder on a
5x-capped name arms its first rung at twice the intended price move, and its later rungs can be out
of reach entirely — the position rides a ladder never calibrated for it.

Live case (MON, 2026-10-01): wild-condor emitted 10x, the venue capped MON at 5x, the first rung
armed at 1.20% of price instead of 0.60%, rung 1 needed a 4.00% move instead of 2.00%, and the trade
peaked at 3.17% — so it stalled on tier 0 and locked 30% of high-water. At the authored 10x the same
tape reaches tier 3 and locks 60%.

Before this, four packages (penguin, pelican, purple-penguin, orca) carried a comment promising to
"walk the ranking and take the first that clears the floor" while the code took `candidates[0]` and
clamped it silently; condor and cheetah did not read the venue cap at all and emitted the request.

Run: python3 -m pytest strategies/tests/test_scanner_leverage_floor.py -q
"""
import importlib.util
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# (package, the key its candidates use for the asset name)
PACKAGES = [
    ("penguin", "token"), ("pelican", "token"), ("purple-penguin", "token"), ("orca", "token"),
    ("penguin-chase-150bp", "token"),
    ("penguin-chase-200bp", "token"),
    ("penguin-chase-300bp", "token"),
    ("penguins-duo-chase-150bp", "token"),
    ("penguins-duo-chase-200bp", "token"),
    ("penguins-duo-chase-300bp", "token"),
    ("condor", "coin"), ("wild-condor", "coin"),
    ("cheetah", "token"), ("wild-cheetah", "token"),
]


# Every one of these packages ships its own `scoring.py` and imports it as the bare name `scoring`.
# In a SHARED pytest run some other package's `scoring` is already in sys.modules, so a plain import
# silently binds this package's scan.py to a stranger's scorer — the same collision the CI workflow
# avoids by running strategy packages one directory per process, and the reason quant-desk gets its
# own invocation. Evict the colliding names for the duration of the load and put them back after,
# so each package is exercised against its OWN modules no matter what ran first.
_SIBLINGS = ("scoring", "score", "sweep", "smartmoney")


def _load(pkg):
    """Import a package's scan.py under its own name, against its own sibling modules."""
    d = os.path.join(ROOT, "strategies", pkg, "main", "scanners")
    saved = {n: sys.modules.pop(n) for n in _SIBLINGS if n in sys.modules}
    sys.path.insert(0, d)
    try:
        spec = importlib.util.spec_from_file_location(f"scan_{pkg.replace('-', '_')}",
                                                     os.path.join(d, "scan.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(d)
        for n in _SIBLINGS:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


class _Mcp:
    """Stands in for ctx.senpi_mcp, answering only strategy_get_asset_trading_limits."""

    def __init__(self, caps):
        self.caps = caps
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        assert name == "strategy_get_asset_trading_limits", name
        return {"data": {"leverage": {"value": self.caps[args["coin"]]}}}


class _Ctx:
    def __init__(self, caps):
        self.wallet = "0xtest"
        self.senpi_mcp = _Mcp(caps)


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_min_leverage_zero_preserves_the_previous_pick(pkg, key):
    """The default must be a no-op on selection: minLeverage 0 still takes the top-scoring name.

    This is what makes the change safe to ship to live templates — it adds the warning and the
    honest leverage, and changes nothing about WHICH name is traded until someone raises the floor.
    """
    mod = _load(pkg)
    assert mod._DEFAULT_MIN_LEVERAGE == 0, (
        f"{pkg} ships a non-zero minLeverage default — that silently changes which name live "
        f"wallets trade. Keep the default 0 (warn only) and raise it per package deliberately.")
    cands = [{key: "LOW", "score": 12}, {key: "HIGH", "score": 11}]
    ctx = _Ctx({"LOW": 3, "HIGH": 10})
    best, lev = mod._pick_leverable(ctx, cands, 0, lambda _c: 10)
    assert best[key] == "LOW", "minLeverage 0 must still take the top-scoring candidate"
    assert lev == 3, "the emitted leverage must be the VENUE cap, not the request"


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_a_floor_walks_past_names_the_venue_will_not_lever(pkg, key):
    mod = _load(pkg)
    cands = [{key: "LOW", "score": 12}, {key: "MID", "score": 11}, {key: "FULL", "score": 10}]
    ctx = _Ctx({"LOW": 3, "MID": 5, "FULL": 10})
    best, lev = mod._pick_leverable(ctx, cands, 10, lambda _c: 10)
    assert (best[key], lev) == ("FULL", 10), (
        "with a floor of 10x the walk must skip the 3x and 5x names and take the one whose venue "
        "cap matches what the ladder was authored for")


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_an_unmet_floor_falls_back_rather_than_skipping_the_tick(pkg, key):
    """Refusing to trade at all is worse than trading with a warning — a scanner that goes silent
    on every clamped name stops being the strategy the user deployed."""
    mod = _load(pkg)
    cands = [{key: "LOW", "score": 12}, {key: "MID", "score": 11}]
    ctx = _Ctx({"LOW": 3, "MID": 5})
    best, lev = mod._pick_leverable(ctx, cands, 10, lambda _c: 10)
    assert (best[key], lev) == ("LOW", 3), "must fall back to the top candidate at its real cap"


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_a_failed_limits_read_degrades_to_the_request(pkg, key):
    """A transient read failure must not zero the tick or invent a leverage."""
    mod = _load(pkg)

    class _Dead:
        def call_tool(self, name, args):
            raise RuntimeError("transient")

    class _C:
        wallet = "0xtest"
        senpi_mcp = _Dead()

    best, lev = mod._pick_leverable(_C(), [{key: "X", "score": 12}], 0, lambda _c: 10)
    assert (best[key], lev) == ("X", 10), "a failed read must degrade to the requested leverage"


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_the_emitted_leverage_is_the_venue_cap(pkg, key):
    """The defect this fixes: condor/cheetah emitted the REQUEST, so the signal, the tape and any
    later analysis all recorded a leverage the position never had."""
    mod = _load(pkg)
    ctx = _Ctx({"MON": 5})
    _best, lev = mod._pick_leverable(ctx, [{key: "MON", "score": 15}], 0, lambda _c: 10)
    assert lev == 5, f"{pkg} emitted the request instead of the venue cap"


# ── the WIRING, not just the helper ──
#
# The tests above all call _pick_leverable directly, so they pass even if scan() throws its result
# away. That is exactly the defect being fixed: condor and cheetah computed a leverage from the
# score tier and emitted THAT, so the signal recorded a leverage the position never had. A mutation
# that deleted the re-assignment passed all five tests above, which is why this one exists.

# packages whose leverage comes from a score tier and must therefore be OVERWRITTEN by the walk's
# venue-clamped value; the penguin family assigns the walk's result directly and needs no override
TIER_SIZED = ["condor", "wild-condor", "cheetah", "wild-cheetah"]


@pytest.mark.parametrize("pkg", TIER_SIZED)
def test_a_tier_sized_scanner_emits_the_venue_leverage_not_the_tier(pkg):
    src = open(os.path.join(ROOT, "strategies", pkg, "main", "scanners", "scan.py"),
               encoding="utf-8").read()
    assert "leverage = _venue_leverage" in src, (
        f"{pkg} computes leverage from the score tier but never replaces it with the venue-clamped "
        f"value from the walk — the emitted signal would claim a leverage the position does not "
        f"have, which is the bug this file exists for.")
    # the call may be wrapped across lines, so match the assignment rather than a literal
    tier_assigns = [m.start() for m in re.finditer(
        r"leverage(?:,\s*\w+)?\s*=\s*scoring\.get_sizing_for_score\(", src)]
    assert tier_assigns, f"{pkg} no longer sizes leverage from a score tier; update this test"
    override_at = src.index("leverage = _venue_leverage")
    assert override_at > max(tier_assigns), (
        f"{pkg} overrides leverage BEFORE its last tier lookup, so the tier value wins again")


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_the_walk_replaced_the_silent_pick_everywhere(pkg, key):
    """No package may go back to taking candidates[0] and clamping it without the walk."""
    src = open(os.path.join(ROOT, "strategies", pkg, "main", "scanners", "scan.py"),
               encoding="utf-8").read()
    assert "_pick_leverable(" in src, f"{pkg} lost the leverage walk"
    assert 'best = candidates[0]' not in src, (
        f"{pkg} still takes candidates[0] directly — the walk is bypassed")


# ── strict mode: refuse rather than run a ladder calibrated for another leverage ──

@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_strict_defaults_off_so_a_scanner_never_goes_silent_by_accident(pkg, key):
    mod = _load(pkg)
    cands = [{key: "LOW", "score": 12}]
    ctx = _Ctx({"LOW": 3})
    best, lev = mod._pick_leverable(ctx, cands, 10, lambda _c: 10)
    assert (best[key], lev) == ("LOW", 3), (
        "strict must default off — a package that silently stops trading when the venue clamps is "
        "not the strategy the user deployed")


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_strict_emits_nothing_when_no_candidate_fits_the_ladder(pkg, key):
    """For a package whose ladder is calibrated for one leverage band, trading outside it is worse
    than not trading: every exit distance is wrong by the clamp ratio."""
    mod = _load(pkg)
    cands = [{key: "LOW", "score": 12}, {key: "MID", "score": 11}]
    ctx = _Ctx({"LOW": 3, "MID": 4})
    best, lev = mod._pick_leverable(ctx, cands, 5, lambda _c: 5, strict=True)
    assert best is None and lev is None, "strict must return (None, None) when nothing clears"


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_strict_still_trades_when_something_does_fit(pkg, key):
    mod = _load(pkg)
    cands = [{key: "LOW", "score": 12}, {key: "FITS", "score": 11}]
    ctx = _Ctx({"LOW": 3, "FITS": 5})
    best, lev = mod._pick_leverable(ctx, cands, 5, lambda _c: 5, strict=True)
    assert (best[key], lev) == ("FITS", 5)


@pytest.mark.parametrize("pkg,key", PACKAGES)
def test_the_scanner_handles_a_strict_no_emit_without_crashing(pkg, key):
    """`_pick_leverable` returning (None, None) must be guarded at every call site — an unguarded
    `best["token"]` would raise and the contract zeroes every emit for the whole tick."""
    src = open(os.path.join(ROOT, "strategies", pkg, "main", "scanners", "scan.py"),
               encoding="utf-8").read()
    assert "if best is None:" in src, f"{pkg} does not guard the strict no-emit path"
    assert "strict=min_leverage_strict" in src, f"{pkg} never passes strict through from inputs"
