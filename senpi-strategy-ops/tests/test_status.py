#!/usr/bin/env python3
"""Hermetic tests for status.py's row shape — no MCP, no openclaw.

Pins Ticket 23's other half (the grouping fix in #526 didn't touch this): `--json` rows must carry
the backend's own `name`/`name_source` (the same `(name, name_source)` shape
`senpi-portfolio/scripts/portfolio.py`'s `_strategy_name_and_source` returns), the runtime's own
name must stay in the `runtime` field and never stand in for the strategy's name, and `package`
must be `None` — a real data value — rather than the display string `"(not on runtime)"` sitting in
a data field. Run:
    python3 -m pytest senpi-strategy-ops/tests/test_status.py -v
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli    # noqa: E402
import status  # noqa: E402

_UNSET = object()


def fixture_with(strategyName=_UNSET, skillName=_UNSET, runtime=None, **rest):
    """One `strategy_list` payload row (real key spelling — see
    `senpi-portfolio/tests/fixtures/portfolio_fixture.json`), with the given fields overridden.

    `runtime` is not a payload field — it is a directive telling `build_rows` to also register a
    runtime-list entry under that name, on the same wallet, so the row's `runtime` match runs for
    real instead of being faked after the fact."""
    row = {
        "strategyId": "sid-test-0001",
        "status": "ACTIVE",
        "strategyWalletAddress": "0xtest000000000000000000000000000000test1",
        "totalFunded": 300,
        "strategyMetadata": {"skillName": "owl-pkg"},
    }
    if strategyName is not _UNSET:
        row["strategyName"] = strategyName
    if skillName is not _UNSET:
        row["strategyMetadata"] = {"skillName": skillName} if skillName is not None else {}
    row.update(rest)
    if runtime is not None:
        row["_runtime"] = runtime  # consumed by build_rows, never sent to status.build()
    return row


def build_rows(payload):
    """Call status.py's real row builder (`status.build`, whose loop body lives at
    `scripts/status.py:139`) on one `strategy_list` payload row — no live MCP, no live openclaw.

    Imports the row-building function directly rather than shelling out to `status.py`, per the
    task brief: monkeypatches the two reads `build()` makes (`_cli.list_strategies_or_none`,
    `_cli.list_runtimes`) and `status._openclaw_available`, and runs with `deep=False` so no
    subprocess for the fleet-wide health map is spawned either."""
    payload = dict(payload)
    runtime_name = payload.pop("_runtime", None)
    wallet = payload.get("strategyWalletAddress")
    runtimes = [{"name": runtime_name, "wallet": wallet, "status": "running", "source": None}] \
        if runtime_name else []

    orig_strats, orig_rts, orig_avail = (
        _cli.list_strategies_or_none, _cli.list_runtimes, status._openclaw_available)
    _cli.list_strategies_or_none = lambda *a, **k: [payload]
    _cli.list_runtimes = lambda *a, **k: runtimes
    status._openclaw_available = lambda: bool(runtimes)
    try:
        rows, _orphans, _cli_ok = status.build(None, deep=False)
    finally:
        _cli.list_strategies_or_none, _cli.list_runtimes, status._openclaw_available = (
            orig_strats, orig_rts, orig_avail)
    return rows


class TestStatusRowNaming(unittest.TestCase):
    def test_json_rows_carry_the_backend_strategy_name(self):
        """The surface the SKILL calls the single source of truth must print the name it was
        given."""
        rows = build_rows(fixture_with(strategyName="dev-wiring-test-0728", runtime=None))
        row = rows[0]
        self.assertEqual(row["name"], "dev-wiring-test-0728")
        self.assertEqual(row["name_source"], "strategyName")

    def test_runtime_name_is_labelled_as_the_runtime_name(self):
        """The deployed wallet's backend name is `owl`; its runtime is `owl-main`. Printing the
        runtime name in the name column sends an agent grepping for `owl-main` to the wrong
        wallet."""
        rows = build_rows(fixture_with(strategyName="owl", runtime="owl-main"))
        self.assertEqual(rows[0]["name"], "owl")
        self.assertEqual(rows[0]["runtime"], "owl-main")

    def test_off_runtime_package_field_is_null_not_a_display_string(self):
        rows = build_rows(fixture_with(strategyName="x", skillName=None))
        self.assertIsNone(rows[0]["package"])
        self.assertFalse(rows[0]["is_pkg"])


# At the END of the file, not the middle: `unittest.main()` only runs the classes already defined
# above it, so a mid-file call silently skipped every class declared after it.
if __name__ == "__main__":
    unittest.main()
