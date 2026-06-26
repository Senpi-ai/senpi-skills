"""WHALEHUNTER — margin-unit regression for scoring.margin_pct_for.

v3.0.4 treats per-signal `marginPct` as a PERCENT in (0,100] of withdrawable
(resolve-margin.ts sizes (marginPct/100)*withdrawable). The producer must therefore
emit a PERCENT, not a fraction. These tests pin the emitted value into [1,100]
across the full conviction range, at the base and at the max-clamp boundary.

scoring.py imports clean (no I/O), and scan.py does `import scoring`, so we add the
scanner dir to sys.path and import the module directly.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import scoring  # noqa: E402

# Two configs that BOTH must emit a percent:
#  - DEFAULTS: empty dict -> exercises margin_pct_for's OWN built-in .get() fallbacks
#    (the shipped fraction defaults are the bug; post-fix they must be percent).
#  - YAML: the values actually wired in long/short runtime.yaml inputs (identical knobs).
# We parametrize over both so a fix to only one place still leaves a red test.
DEFAULTS = {}
YAML = {
    "marginPct": 12,
    "maxMarginPct": 25,
    "maxConvictionScale": 2.0,
    "cohortMinScore": 4,
}
CONFIGS = [DEFAULTS, YAML]

FLOOR = 4  # cohortMinScore default and YAML value


def _floor(cfg):
    return int(cfg.get("cohortMinScore", FLOOR))


import pytest  # noqa: E402


@pytest.mark.parametrize("cfg", CONFIGS)
def test_base_conviction_is_percent(cfg):
    # score == floor -> scale 1.0 -> emits the base, which must be a percent in [1,100].
    mp = scoring.margin_pct_for(_floor(cfg), cfg)
    assert 1 <= mp <= 100, f"base conviction emitted {mp}, expected a percent in [1,100]"


@pytest.mark.parametrize("cfg", CONFIGS)
def test_mid_conviction_is_percent(cfg):
    mp = scoring.margin_pct_for(_floor(cfg) + 2, cfg)  # score 6
    assert 1 <= mp <= 100, f"mid conviction emitted {mp}, expected a percent in [1,100]"


@pytest.mark.parametrize("cfg", CONFIGS)
def test_max_clamp_conviction_is_percent(cfg):
    # Very high score saturates scale at maxConvictionScale -> max-clamp boundary.
    mp = scoring.margin_pct_for(_floor(cfg) + 50, cfg)
    assert 1 <= mp <= 100, f"max-clamp conviction emitted {mp}, expected a percent in [1,100]"


@pytest.mark.parametrize("cfg", CONFIGS)
def test_full_conviction_range_in_percent_band(cfg):
    # Every score from floor through a large clamp value must land in [1,100], never <1.
    fl = _floor(cfg)
    for score in range(fl, fl + 51):
        mp = scoring.margin_pct_for(score, cfg)
        assert 1 <= mp <= 100, f"score {score} emitted {mp}, outside [1,100]"


@pytest.mark.parametrize("cfg", CONFIGS)
def test_monotonic_nondecreasing_with_conviction(cfg):
    fl = _floor(cfg)
    prev = 0.0
    for score in range(fl, fl + 10):
        mp = scoring.margin_pct_for(score, cfg)
        assert mp >= prev, f"score {score} emitted {mp} < previous {prev}"
        prev = mp
