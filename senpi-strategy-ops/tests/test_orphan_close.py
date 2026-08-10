#!/usr/bin/env python3
"""Hermetic tests for the close sweep's orphan filter — `create` must never close its OWN wallet.

The close sweep exists to clear the runtime-less trap (a funded wallet from an abandoned run that a
re-run would otherwise land on forever). It must not fire on a wallet this deploy created and is
still working on: closing a funded sleeve to recover from its sibling's failure is what turns one
failed multi-leg deploy into an unbounded create → close → recreate churn at ~$1/wallet.

No MCP, no openclaw, no network. Run:
    python3 senpi-strategy-ops/tests/test_orphan_close.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import deploy  # noqa: E402


def _row(sid, status="ACTIVE"):
    return {"id": sid, "status": status, "strategyWalletAddress": "0x" + sid}


def _pkg(*names):
    return types.SimpleNamespace(id="elephant", version="1.0.0",
                                 instances=[types.SimpleNamespace(name=n) for n in names])


class RecordedStrategyIds(unittest.TestCase):
    def test_collects_every_recorded_leg(self):
        st = {"instances": {"trend": {"strategyId": "S-trend"}, "fade": {"strategyId": "S-fade"}}}
        self.assertEqual(deploy.recorded_strategy_ids(_pkg("trend", "fade"), st), {"S-trend", "S-fade"})

    def test_a_leg_with_no_id_yet_contributes_nothing(self):
        st = {"instances": {"trend": {"strategyId": "S-trend"}, "fade": {"status": "pending"}}}
        self.assertEqual(deploy.recorded_strategy_ids(_pkg("trend", "fade"), st), {"S-trend"})

    def test_absent_instances_do_not_raise(self):
        self.assertEqual(deploy.recorded_strategy_ids(_pkg("trend", "fade"), {"instances": {}}), set())


class OrphanStrategies(unittest.TestCase):
    """The regression this filter exists for: M415566's 38 churned wallets."""

    def test_our_own_wallets_are_never_orphans(self):
        # The incident shape: `fade` funded and went ACTIVE, `trend` stalled in PENDING_FUNDING.
        # Neither has a runtime yet. Both are OURS, so the sweep must touch neither — closing the
        # funded `fade` to recover from `trend` is the $1/cycle burn.
        rows = [_row("S-fade", "ACTIVE"), _row("S-trend", "PENDING_FUNDING")]
        self.assertEqual(deploy.orphan_strategies(rows, {"S-fade", "S-trend"}), [])

    def test_a_wallet_we_never_recorded_is_an_orphan(self):
        rows = [_row("S-fade"), _row("S-stale")]
        self.assertEqual(deploy.orphan_strategies(rows, {"S-fade"}), [_row("S-stale")])

    def test_no_recorded_ids_means_every_open_wallet_is_an_orphan(self):
        # A fresh deploy with a deleted/empty state file keeps the original protection in full.
        rows = [_row("S-a"), _row("S-b")]
        self.assertEqual(deploy.orphan_strategies(rows, set()), rows)

    def test_an_unreadable_id_stays_an_orphan(self):
        # Fail-CLOSED: a wallet we cannot identify must still block funding a fresh one beside it.
        rows = [{"status": "ACTIVE", "strategyWalletAddress": "0xabc"}]
        self.assertEqual(deploy.orphan_strategies(rows, {"S-fade"}), rows)

    def test_nothing_open_closes_nothing(self):
        self.assertEqual(deploy.orphan_strategies([], {"S-fade"}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
