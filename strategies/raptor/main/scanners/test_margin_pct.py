"""Guard: raptor's marginPct must be a PERCENT in (0,100], never a v2 fraction.

v3.x runtime sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so a fraction
like 0.25 sizes 0.25% of withdrawable — ~100x undersize, silent (no 400, since 0.25
still satisfies the (0,100] bound). v2 raptor stored marginPctBase/marginPctHighConv as
FRACTIONS (0.25 / 0.35); this port converts them to PERCENTS (25 / 35). marginPct is
computed by scoring.margin_pct_for from the runtime.yaml inputs (a two-step conviction
ladder), so this asserts the runtime-bound values AND the scoring output are percents.
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scoring

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")


def _inputs():
    with open(_RUNTIME_YAML) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return s["inputs"]
    raise AssertionError("no external_scanner inputs in runtime.yaml")


def test_runtime_margin_pcts_are_percent_in_range():
    inp = _inputs()
    for key in ("marginPctBase", "marginPctHighConv"):
        pct = float(inp[key])
        # >=1 catches the fraction bug (0.25); <=100 is the wire upper bound.
        assert 1 <= pct <= 100, f"runtime.yaml {key}={pct} not a PERCENT in [1,100]"


def test_margin_pct_for_outputs_percent_in_range():
    inp = _inputs()
    base = float(inp["marginPctBase"])
    high = float(inp["marginPctHighConv"])
    high_conv_score = float(inp["highConvScore"])
    # below the high-conviction score -> base; at/above -> high. Both percents in (0,100].
    assert scoring.margin_pct_for(high_conv_score - 1, inp) == base
    assert scoring.margin_pct_for(high_conv_score, inp) == high
    for s in (6, 7, 8, 9, 10, 11, 16):
        m = scoring.margin_pct_for(s, inp)
        assert 1 <= m <= 100, f"margin_pct_for({s})={m} not a PERCENT in [1,100]"


def test_leverage_tiers_match_v2():
    inp = _inputs()
    tiers = inp["leverageTiers"]
    dflt = int(inp["defaultLeverage"])
    # v2: score 6-7 -> 7x, 8-9 -> 8x, 10+ -> 10x
    assert scoring.get_leverage_for_score(6, tiers, dflt) == 7
    assert scoring.get_leverage_for_score(7, tiers, dflt) == 7
    assert scoring.get_leverage_for_score(8, tiers, dflt) == 8
    assert scoring.get_leverage_for_score(9, tiers, dflt) == 8
    assert scoring.get_leverage_for_score(10, tiers, dflt) == 10
    assert scoring.get_leverage_for_score(16, tiers, dflt) == 10
    # below the lowest tier falls back to default
    assert scoring.get_leverage_for_score(0, tiers, dflt) == dflt


if __name__ == "__main__":
    test_runtime_margin_pcts_are_percent_in_range()
    test_margin_pct_for_outputs_percent_in_range()
    test_leverage_tiers_match_v2()
    print("test_margin_pct: OK")
