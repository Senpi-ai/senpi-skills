"""Guard: cheetah's marginPct must be a PERCENT in (0,100], never a v2 fraction.

v3.x runtime sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so a fraction
like 0.30 would size 0.30% of withdrawable — ~100x undersize, silent (no 400, since
0.30 still satisfies the (0,100] bound). The v2 producer used MARGIN_PCT=0.30 (a
fraction of account_value); the 3.0 port carries it as 30 (PERCENT). marginPct is a
pure pass-through config value: read at scan.py (inputs.marginPct, default 30) and
emitted verbatim at the candidate top level; scoring never reads it. So this is a
config-level invariant, asserted directly against the wire-bound value.
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
    # scan.py reads inputs.get("marginPct", _DEFAULT_MARGIN_PCT); the default lives in the
    # module constant. Match either an inline literal or the constant definition.
    m = re.search(r'inputs\.get\(\s*"marginPct"\s*,\s*([0-9._]+)\s*\)', src)
    if not m:
        m = re.search(r'_DEFAULT_MARGIN_PCT\s*=\s*([0-9._]+)', src)
    assert m, "scan.py marginPct default not found"
    return float(m.group(1).replace("_", ""))


def test_runtime_margin_pct_is_percent_in_range():
    pct = _runtime_margin_pct()
    # >=1 catches the fraction bug (0.30); <=100 is the wire upper bound.
    assert 1 <= pct <= 100, f"runtime.yaml marginPct={pct} not a PERCENT in [1,100]"


def test_scan_default_margin_pct_is_percent_in_range():
    pct = _scan_default_margin_pct()
    assert 1 <= pct <= 100, f"scan.py default marginPct={pct} not a PERCENT in [1,100]"


if __name__ == "__main__":
    test_runtime_margin_pct_is_percent_in_range()
    test_scan_default_margin_pct_is_percent_in_range()
    print("marginPct guards OK:",
          "runtime=", _runtime_margin_pct(), "scan_default=", _scan_default_margin_pct())
