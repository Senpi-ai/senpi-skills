"""wolf margin-units regression.

v3.x runtime sizes (marginPct/100)*withdrawable, so per-book `marginPct` MUST be a
PERCENT in (0,100], never a v2 fraction (0.20/0.18 -> ~100x undersize, silent — no
400, since 0.20 still satisfies the (0,100] bound). These tests assert the runtime.yaml
inputs.marginPct and the scan.py in-code default both land in [1,100], per book.
"""

import os
import re

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS = {"risk_on": 20, "risk_off": 18}


def _runtime_margin_pct(book):
    with open(os.path.join(ROOT, book, "runtime.yaml")) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return float(s["inputs"]["marginPct"])
    raise AssertionError(f"{book}: no external_scanner inputs.marginPct in runtime.yaml")


def _scan_default_margin_pct(book):
    src = open(os.path.join(ROOT, book, "scanners", "scan.py")).read()
    m = re.search(r'inputs\.get\(\s*"marginPct"\s*,\s*([0-9.]+)\s*\)', src)
    assert m, f"{book}: scan.py marginPct default not found"
    return float(m.group(1))


def test_runtime_margin_pct_is_percent_in_range():
    for book, expect in BOOKS.items():
        pct = _runtime_margin_pct(book)
        assert 1 <= pct <= 100, f"{book}: runtime.yaml marginPct={pct} not a PERCENT in [1,100]"
        assert pct == expect, f"{book}: expected {expect}, got {pct}"


def test_scan_default_margin_pct_is_percent_in_range():
    for book in BOOKS:
        pct = _scan_default_margin_pct(book)
        assert 1 <= pct <= 100, f"{book}: scan.py marginPct default={pct} not a PERCENT in [1,100]"
