"""A scanner may only read asset-context keys that Hyperliquid actually returns.

2026-09-24: `orca` (and `penguin`, forked from it hours earlier) computed its volume-confirmation
gate as `dayNtlVlm / prevDayNtlVlm`. HL's asset context has no `prevDayNtlVlm` — its previous-day
field is `prevDayPx`, a PRICE. So the divisor was always 0, the ratio branch never ran, and the
function returned its permissive `(0, True)` on every call. The gate was dead from launch while
`VOL_CONFIRMED` still printed downstream: 982 zero-ratio lines in telemetry.

The failure mode is what makes this worth a guard. A phantom key does not raise, does not warn and
does not read as broken — `.get(key, 0)` silently yields the default, and a gate written to fail
open then passes everything. The only visible symptom is a metric pinned at zero, which looks like
a quiet market.

Two properties make the class catchable by a cheap static check: the real key set is small and
stable, and scanners read it through one idiom (`ac.get("...")`). So enumerate the idiom and
intersect with the truth.

REAL_KEYS is the live response from `market_get_asset_data(BTC)`, verified 2026-09-24. If HL adds a
field, add it here in the same PR that uses it — and verify it against a real response first rather
than a doc, which is how the original bug got in.

Run: python3 -m pytest strategies/tests/test_no_phantom_asset_context_keys.py -q
"""
import os
import re

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ""))

# Verified against a live market_get_asset_data(BTC) response, 2026-09-24.
REAL_KEYS = {
    "coin",
    "dayBaseVlm",
    "dayNtlVlm",
    "funding",
    "impactPxs",
    "markPx",
    "midPx",
    "openInterest",
    "oraclePx",
    "premium",
    "prevDayPx",
}

# `ac` is the tree-wide name for the asset-context dict. Matches ac.get("x") and ac.get('x', 0).
_AC_GET = re.compile(r"""\bac\.get\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""")

# This file necessarily names the phantom key; it must not be its own counterexample.
_SELF = os.path.abspath(__file__)


def _scanner_sources():
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            if os.path.abspath(path) == _SELF:
                continue
            yield path


def _reads():
    """(package, file, key) for every asset-context key read anywhere under strategies/."""
    out = []
    for path in _scanner_sources():
        with open(path, encoding="utf-8", errors="ignore") as fh:
            body = fh.read()
        rel = os.path.relpath(path, _ROOT)
        pkg = rel.split(os.sep)[0]
        for key in _AC_GET.findall(body):
            out.append((pkg, rel, key))
    return sorted(set(out))


_READS = _reads()


def test_the_scan_found_reads():
    """Guard the guard: a regex that matched nothing would make the test below vacuous."""
    assert _READS, "no ac.get(...) reads found anywhere — regex or layout changed"


@pytest.mark.parametrize("pkg,rel,key", _READS)
def test_asset_context_key_is_real(pkg, rel, key):
    assert key in REAL_KEYS, (
        f"{rel} reads asset_context['{key}'], which Hyperliquid does not return.\n"
        f"Real keys: {', '.join(sorted(REAL_KEYS))}.\n"
        "A missing key does not raise — .get() returns the default and any gate built on it "
        "silently passes everything. Verify the field against a real market_get_asset_data "
        "response, not a doc; if HL genuinely added it, add it to REAL_KEYS in this PR."
    )
