"""Guard: jaguar's marginPct must be a PERCENT in (0,100], never a v2 fraction.

The v2 producer carried MARGIN_PCT = 0.50 (a FRACTION) and sized marginUsd in Python.
Runtime 3.0 sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so the scanner
MUST emit a PERCENT. A fraction like 0.50 would size 0.50% of withdrawable — ~200x
undersize, silent (0.50 still satisfies the (0,100] bound). marginPct is a pure pass-
through config value here: read at scan.py (inputs.marginPct, default 50) and emitted
verbatim at the candidate top level. So this is a config-level invariant, asserted
directly against the wire-bound value in runtime.yaml and the scan.py default.
"""

import os
import re

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")
_SCAN_PY = os.path.join(_HERE, "scan.py")


def _runtime_margin_pct():
    with open(_RUNTIME_YAML) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return float(s["inputs"]["marginPct"])
    raise AssertionError("no external_scanner inputs.marginPct in runtime.yaml")


def _scan_default_margin_pct():
    src = open(_SCAN_PY).read()
    m = re.search(r'inputs\.get\(\s*"marginPct"\s*,\s*([0-9.]+)\s*\)', src)
    assert m, "scan.py marginPct default not found"
    return float(m.group(1))


def test_runtime_margin_pct_is_percent_in_range():
    pct = _runtime_margin_pct()
    # >=1 catches the fraction bug (0.50); <=100 is the wire upper bound.
    assert 1 <= pct <= 100, f"runtime.yaml marginPct={pct} not a PERCENT in [1,100]"


def test_scan_default_margin_pct_is_percent_in_range():
    pct = _scan_default_margin_pct()
    assert 1 <= pct <= 100, f"scan.py marginPct default={pct} not a PERCENT in [1,100]"
