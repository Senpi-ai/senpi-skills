"""Guard: koala's marginPct must be a PERCENT in (0,100], never a v2 fraction.

The v3.x runtime sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so a
fraction like 0.50 would size 0.50% of withdrawable — ~100x undersize, silent
(no 400, since 0.50 still satisfies the (0,100] bound).

Koala is a trap case: the v2 DEFAULT_MARGIN_PCT was a FRACTION (0.50). This port
carries marginPct=50 (a PERCENT) in runtime.yaml; scan.py emits it top-level and
additionally converts any value <= 1 (a pasted v2 fraction) to a PERCENT. This
test asserts the runtime input is a PERCENT in [1,100] and that scan.py keeps the
defensive fraction->percent conversion.
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
    # >=1 catches the fraction bug (0.50); <=100 is the wire upper bound.
    assert 1 <= pct <= 100, f"runtime.yaml marginPct={pct} not a PERCENT in [1,100]"


def test_leverage_clamped_to_max_leverage():
    inputs = _external_scanner_inputs()
    lev = int(inputs["leverage"])
    max_lev = int(inputs["maxLeverage"])
    assert 0 < lev <= max_lev, f"leverage={lev} not in (0, maxLeverage={max_lev}]"


def test_scan_keeps_fraction_to_percent_guard():
    src = open(_SCAN_PY).read()
    # the defensive *100 conversion (value <= 1 -> percent) must be present.
    assert "* 100" in src and "<= 1.0" in src, (
        "scan.py must keep the defensive fraction->percent guard for marginPct"
    )
