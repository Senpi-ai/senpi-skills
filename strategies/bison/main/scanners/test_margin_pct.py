"""Guard: bison's marginPct must be a PERCENT in (0,100], never a v2 fraction.

v3.x runtime sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so a
fraction like 0.25 sizes 0.25% of withdrawable — ~100x undersize, silent (no 400,
since 0.25 still satisfies the (0,100] bound). Bison's marginPct is conviction-
tiered: scoring.margin_tier_pct(score, base) returns base x{1.0, 1.25, 1.5}. The
base is read at scan.py (inputs.marginPctBase, default 25) and the EMITTED tier
value is the wire-bound marginPct. The apex tier (37.5%) must still be <= 100.
"""

import os
import re
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")
_SCAN_PY = os.path.join(_HERE, "scan.py")

sys.path.insert(0, _HERE)
import scoring  # noqa: E402


def _runtime_base_margin_pct():
    with open(_RUNTIME_YAML) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return float(s["inputs"]["marginPctBase"])
    raise AssertionError("no external_scanner inputs.marginPctBase in runtime.yaml")


def _scan_default_base_margin_pct():
    src = open(_SCAN_PY).read()
    m = re.search(r'inputs\.get\(\s*"marginPctBase"\s*,\s*([0-9.]+)\s*\)', src)
    assert m, "scan.py marginPctBase default not found"
    return float(m.group(1))


def test_runtime_base_margin_pct_is_percent_in_range():
    pct = _runtime_base_margin_pct()
    assert 1 <= pct <= 100, f"runtime.yaml marginPctBase={pct} not a PERCENT in [1,100]"


def test_scan_default_base_margin_pct_is_percent_in_range():
    pct = _scan_default_base_margin_pct()
    assert 1 <= pct <= 100, f"scan.py marginPctBase default={pct} not a PERCENT in [1,100]"


def test_apex_tier_margin_pct_within_wire_bound():
    """The highest conviction tier (base x1.5) must stay <= 100 (wire upper bound)."""
    base = _runtime_base_margin_pct()
    apex = scoring.margin_tier_pct(99, base)   # score 99 -> top tier
    assert 1 <= apex <= 100, f"apex tier marginPct={apex} not a PERCENT in [1,100]"


def test_margin_tiers_match_v2_multipliers():
    """Verbatim v2 tier multipliers/cutoffs: <10 -> base, 10-11 -> 1.25x, 12+ -> 1.5x."""
    base = 25.0
    assert scoring.margin_tier_pct(9, base) == base * 1.0      # 25
    assert scoring.margin_tier_pct(11, base) == base * 1.25    # 31.25
    assert scoring.margin_tier_pct(12, base) == base * 1.5     # 37.5
