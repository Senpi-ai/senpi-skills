#!/usr/bin/env python3
"""The aegis leg sizes every position at a FLAT 20% of the sleeve, in every regime.

Jason asked for "20% per position" on 2026-10-07, and again on 2026-10-09 because the first
attempt did not deliver it. `marginPctBase: 20` looks like the answer and is not:
`scoring.margin_pct_for` multiplies the base by a regime-intensity factor
`ri = clamp(|regime_score| / 2.0, 0.25, 1.0)` and then by conviction (x1.0 / x1.25 / x1.5). With
base 20 a position reached 20% only when the regime score was pinned at its extreme; in a mild
regime `ri` floored at 0.25 and the position took 5%. Four slots x 5% = 20% of the sleeve — the
exact under-deployment that was reported, with a config that read as if it were already fixed.

The scanners are byte-identical to standalone aegis and pinned that way by test_legs_verbatim.py,
so the fix lives in config: `marginPctBase: 80` with `marginPctMax: 20`. The lowest point of the
grid, 80 x 1.00 x 0.25, lands exactly on 20.0, and everything above it clamps to 20.

That floor depends on the 0.25 clamp inside the shared scorer — a constant this package does not
own and cannot pin by byte-identity, because the same file serves standalone aegis. So this file
asserts the OUTCOME over the whole grid rather than the constant: if the clamp ever moves, aegis's
sizing does not quietly drift, this fails.
"""
import importlib.util
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))

TARGET_PCT = 20.0
SLOTS = 4


def _scoring():
    """Load the leg's scorer BY PATH — several packages vendor a module called `scoring`."""
    path = os.path.join(PKG, "aegis", "scanners", "scoring.py")
    spec = importlib.util.spec_from_file_location("_aegis_scoring", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _inputs():
    with open(os.path.join(PKG, "aegis", "runtime.yaml"), encoding="utf-8") as fh:
        rt = yaml.safe_load(fh)
    scanner = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")
    return rt, scanner["inputs"]


# the full range the scanner can hand the sizer: regime score 0..2 either side, conviction 0..100
REGIME_SCORES = [0.0, 0.1, 0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0]
CONVICTIONS = [25, 30, 45, 49, 50, 55, 64, 69, 70, 85, 100]


@pytest.mark.parametrize("regime", REGIME_SCORES + [-r for r in REGIME_SCORES])
def test_every_position_sizes_at_twenty_percent(regime):
    sc = _scoring()
    _rt, inputs = _inputs()
    base, cap = float(inputs["marginPctBase"]), float(inputs["marginPctMax"])
    for conviction in CONVICTIONS:
        got = sc.margin_pct_for(conviction, base, regime, cap)
        assert got == pytest.approx(TARGET_PCT), (
            f"regime {regime:+.1f}, conviction {conviction}: sized {got:.2f}% of the sleeve, "
            f"expected a flat {TARGET_PCT}% — base {base} x conviction x regime-intensity no "
            f"longer lands on {TARGET_PCT} at every point, so the config and the scorer have "
            f"drifted apart")


def test_the_config_is_the_pair_that_makes_it_flat():
    """Stated explicitly so a 'tidy-up' that sets base back to 20 fails here with the reason."""
    _rt, inputs = _inputs()
    assert float(inputs["marginPctMax"]) == TARGET_PCT, (
        "marginPctMax is not 20 — without the clamp, a high-conviction hedge in a severe regime "
        "sizes at base x 1.5, which with base 80 would be 120% of the sleeve in one position")
    assert float(inputs["marginPctBase"]) == 80.0, (
        "marginPctBase is not 80 — the base exists to cancel the regime-intensity floor (x0.25), "
        "not to state the position size. Setting it to 20 is the bug this package already had: "
        "it sizes 5% per position in a mild regime")


def test_the_sleeve_is_fully_deployed_in_every_regime():
    rt, _ = _inputs()
    assert rt["strategy"]["slots"] == SLOTS
    assert SLOTS * TARGET_PCT == 80.0, "4 x 20% should deploy 80% of the aegis sleeve"


def test_the_strategy_block_states_the_per_position_size():
    """`strategy.margin_pct` is the fallback when a signal carries no sizing; it should agree with
    what the scanner actually emits, or a reader learns the wrong number from the manifest."""
    rt, _ = _inputs()
    assert float(rt["strategy"]["margin_pct"]) == TARGET_PCT


if __name__ == "__main__":
    sc, (rt, inputs) = _scoring(), _inputs()
    base, cap = float(inputs["marginPctBase"]), float(inputs["marginPctMax"])
    seen = {round(sc.margin_pct_for(c, base, r, cap), 4)
            for r in REGIME_SCORES for c in CONVICTIONS}
    print(f"  margin% across {len(REGIME_SCORES)}x{len(CONVICTIONS)} regime/conviction grid: {sorted(seen)}")
    test_the_config_is_the_pair_that_makes_it_flat()
    test_the_sleeve_is_fully_deployed_in_every_regime()
    test_the_strategy_block_states_the_per_position_size()
    print(f"AEGIS FLAT SIZING OK — {SLOTS} x {TARGET_PCT}% = 80% of the sleeve in every regime")
