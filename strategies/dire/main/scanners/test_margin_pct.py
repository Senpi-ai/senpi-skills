"""Guard: dire's marginPct must be a PERCENT in (0,100], never a v2 fraction.

The v3.x runtime sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so a
fraction like 0.20 sizes 0.20% of withdrawable — ~100x undersize, silent (no 400,
since 0.20 still satisfies the (0,100] bound).

Dire is the trap case: the v2 sizingTiers carry marginPct as a FRACTION
(0.20/0.25/0.30) AND there's a top-level runtime input marginPct. scan.py converts
each tier fraction to a percent (*100) before emitting. This test asserts BOTH the
top-level runtime input AND every emitted per-tier value land in [1,100].
"""

import os
import re

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")
_SCAN_PY = os.path.join(_HERE, "scan.py")


def _external_scanner_inputs():
    with open(_RUNTIME_YAML) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return s["inputs"]
    raise AssertionError("no external_scanner inputs in runtime.yaml")


def test_runtime_top_level_margin_pct_is_percent_in_range():
    pct = float(_external_scanner_inputs()["marginPct"])
    # >=1 catches the fraction bug (0.20); <=100 is the wire upper bound.
    assert 1 <= pct <= 100, f"runtime.yaml marginPct={pct} not a PERCENT in [1,100]"


def test_emitted_sizing_tier_margins_are_percent_in_range():
    # scan.py emits round(tierFraction * 100, 2); the tier fractions live in the
    # runtime inputs.sizingTiers. Assert each (fraction*100) lands in [1,100].
    tiers = _external_scanner_inputs()["sizingTiers"]
    assert tiers, "no sizingTiers in runtime.yaml inputs"
    for t in tiers:
        emitted = round(float(t["marginPct"]) * 100, 2)
        assert 1 <= emitted <= 100, (
            f"tier {t.get('label')} emits marginPct={emitted} not a PERCENT in [1,100]"
        )


def test_scan_converts_tier_fraction_to_percent():
    src = open(_SCAN_PY).read()
    # the *100 conversion must be present so fractions become percents on the wire.
    assert re.search(r'marginPct".*?\)\s*\*\s*100', src) or '* 100, 2)' in src, (
        "scan.py must multiply the tier marginPct fraction by 100 to emit a PERCENT"
    )
