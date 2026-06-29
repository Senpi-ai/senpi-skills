"""Guard: falcon's marginPct must be a PERCENT in (0,100], never a v2 fraction.

v3.x runtime sizes `(marginPct/100)*withdrawable` (resolve-margin.ts), so a
fraction like 0.15 sizes 0.15% of withdrawable — ~100x undersize, silent (no
400, since 0.15 still satisfies the (0,100] bound). The v2 producer emitted
marginUsd = account_value * 0.15; the Runtime 3.0 port emits the PERCENT (15) on
the wire and lets the runtime size the dollars. This asserts both the
runtime.yaml inputs.marginPct AND the scoring.py default are PERCENTs in [1,100].
"""

import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")


def _scanner_inputs():
    with open(_RUNTIME_YAML) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return s["inputs"]
    raise AssertionError("no external_scanner inputs in runtime.yaml")


def test_runtime_margin_pct_is_percent_in_range():
    pct = float(_scanner_inputs()["marginPct"])
    # >=1 catches the fraction bug (0.15); <=100 is the wire upper bound.
    assert 1 <= pct <= 100, f"runtime.yaml marginPct={pct} not a PERCENT in [1,100]"


def test_scoring_default_margin_pct_is_percent_in_range():
    import importlib.util
    spec = importlib.util.spec_from_file_location("scoring", os.path.join(_HERE, "scoring.py"))
    scoring = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scoring)
    assert 1 <= scoring.DEFAULT_MARGIN_PCT <= 100, (
        f"scoring DEFAULT_MARGIN_PCT={scoring.DEFAULT_MARGIN_PCT} not a PERCENT in [1,100]")
