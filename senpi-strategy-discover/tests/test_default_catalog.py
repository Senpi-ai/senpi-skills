"""Regression guard: the discovery engine must DEFAULT to the production catalog, never a fixture.

A 'TEMPORARY, revert before merge' line once pointed DEFAULT_CATALOG at
tests/fixtures/catalog_fullfleet.json and shipped to strategy-v2, so live discovery recommended
from synthetic data. These tests fail loudly if the default ever drifts back to a fixture.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402


def test_default_catalog_is_production_not_a_fixture():
    p = os.path.abspath(discover.DEFAULT_CATALOG).replace(os.sep, "/")
    assert p.endswith("strategies/catalog.json"), f"default catalog is {p}, not the prod catalog"
    assert "tests/fixtures" not in p, f"default catalog points at a test fixture: {p}"


def test_default_catalog_loads_the_real_fleet():
    recs = discover.load_catalog(discover.DEFAULT_CATALOG)
    ids = {r.get("id") for r in recs}
    assert len(recs) > 50, f"expected the full fleet, got {len(recs)} records"
    for known in ("kodiak", "rhino", "thesis-fund"):
        assert known in ids, f"real strategy {known!r} missing from the default catalog"
