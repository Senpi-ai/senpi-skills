"""Regression guard: the discovery engine must DEFAULT to a REAL catalog, never a fixture.

A 'TEMPORARY, revert before merge' line once pointed the default catalog at
tests/fixtures/catalog_fullfleet.json and shipped to strategy-v2, so live discovery recommended
from synthetic data. These tests fail loudly if the default ever drifts back to a fixture.

The default now resolves to the skill-local `senpi-strategy-discover/catalog.json` (bundled with the
skill) or the repo `strategies/catalog.json` (dev checkout) — both are real; a test fixture is not.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

_REAL = ("strategies/catalog.json", "senpi-strategy-discover/catalog.json")


def test_default_catalog_is_production_not_a_fixture():
    p = os.path.abspath(discover.default_catalog()).replace(os.sep, "/")
    assert p.endswith(_REAL), f"default catalog is {p}, not a real catalog"
    assert "tests/fixtures" not in p, f"default catalog points at a test fixture: {p}"


def test_default_catalog_loads_the_real_fleet():
    recs = discover.load_catalog(discover.default_catalog())
    ids = {r.get("id") for r in recs}
    assert len(recs) > 50, f"expected the full fleet, got {len(recs)} records"
    for known in ("kodiak", "rhino", "thesis-fund"):
        assert known in ids, f"real strategy {known!r} missing from the default catalog"
