"""Guard: tortoise's marginPct must be a PERCENT in (0,100], never a v2 fraction.

v3.x runtime sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so a
fraction like 0.08 sizes 0.08% of withdrawable — ~100x undersize, silent (no 400,
since 0.08 still satisfies the (0,100] bound). The v2 producer used marginPct=0.08
(a FRACTION) and multiplied by account_value; this port emits a PERCENT (8) and
the runtime sizes. Tortoise has NO conviction tiers — the emitted marginPct is the
fixed input value. Also assert the leverage clamp matches v2 (MAX_LEVERAGE=3).
"""

import os
import re
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")
_SCAN_PY = os.path.join(_HERE, "scan.py")

sys.path.insert(0, _HERE)


def _runtime_margin_pct():
    with open(_RUNTIME_YAML) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return float(s["inputs"]["marginPct"])
    raise AssertionError("no external_scanner inputs.marginPct in runtime.yaml")


def _scan_default_margin_pct():
    src = open(_SCAN_PY).read()
    m = re.search(r"_DEFAULT_MARGIN_PCT\s*=\s*([0-9.]+)", src)
    assert m, "scan.py _DEFAULT_MARGIN_PCT not found"
    return float(m.group(1))


def _scan_max_leverage():
    src = open(_SCAN_PY).read()
    m = re.search(r"_MAX_LEVERAGE\s*=\s*([0-9]+)", src)
    assert m, "scan.py _MAX_LEVERAGE not found"
    return int(m.group(1))


def test_runtime_margin_pct_is_percent_in_range():
    pct = _runtime_margin_pct()
    assert 1 <= pct <= 100, f"runtime.yaml marginPct={pct} not a PERCENT in [1,100]"


def test_scan_default_margin_pct_is_percent_in_range():
    pct = _scan_default_margin_pct()
    assert 1 <= pct <= 100, f"scan.py _DEFAULT_MARGIN_PCT={pct} not a PERCENT in [1,100]"


def test_runtime_and_scan_margin_pct_agree():
    assert _runtime_margin_pct() == _scan_default_margin_pct(), \
        "runtime.yaml marginPct must match scan.py _DEFAULT_MARGIN_PCT"


def test_max_leverage_matches_v2():
    """v2 MAX_LEVERAGE = 3 (DCA is accumulation, not leverage)."""
    assert _scan_max_leverage() == 3, "scan.py _MAX_LEVERAGE must be 3 (v2 cap)"
