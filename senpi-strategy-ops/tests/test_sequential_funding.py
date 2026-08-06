#!/usr/bin/env python3
"""Hermetic tests for one-wallet-at-a-time funding + the SERR083 name-collision fallback.

No MCP, no openclaw, no network, no sleeping. Run:
    python3 senpi-strategy-ops/tests/test_sequential_funding.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import deploy  # noqa: E402


class IsNameRejection(unittest.TestCase):
    """SERR083 must route to the unnamed retry — that is the whole unblock."""

    def test_serr083_is_a_name_rejection(self):
        # Real payload: "tool failed: SERR083: Custom strategy creation failed unexpectedly.
        # Retry with the same payload." — generic text, no "name" anywhere in it.
        err = deploy.MCPError("tool failed: SERR083: Custom strategy creation failed "
                              "unexpectedly. Retry with the same payload.")
        self.assertTrue(deploy.is_name_rejection(err))

    def test_existing_name_codes_still_route(self):
        for code in ("SERR055", "SERR056", "SERR058"):
            self.assertTrue(deploy.is_name_rejection(deploy.MCPError(f"tool failed: {code}: nope")))

    def test_word_name_still_routes(self):
        self.assertTrue(deploy.is_name_rejection(deploy.MCPError("strategyName is invalid")))

    def test_unrelated_errors_do_not_route(self):
        # These must stay hard failures — retrying them unnamed would mask a real problem.
        for msg in ("tool failed: SERR037: Insufficient USDC balance",
                    "tool failed: SERR045: not in a state to be topped up",
                    "HTTP 503"):
            self.assertFalse(deploy.is_name_rejection(deploy.MCPError(msg)), msg)


class _Inst:
    def __init__(self, name):
        self.name = name


class _StubCli:
    """Stands in for _cli: each strategy reports NOT-ACTIVE once, then ACTIVE."""

    def __init__(self):
        self.polls = {}

    def strategies_for(self, mcp, strategy_id=None, timeout=None):
        n = self.polls.get(strategy_id, 0) + 1
        self.polls[strategy_id] = n
        return [{"id": strategy_id, "status": "ACTIVE" if n >= 2 else "FUND_WALLET",
                 "strategyWalletAddress": "0xabc" if n >= 2 else ""}]

    @staticmethod
    def strategy_status(row):
        return row["status"]

    @staticmethod
    def strategy_wallet(row):
        return row["strategyWalletAddress"] or None


class AwaitFunded(unittest.TestCase):
    def setUp(self):
        self.pkg = types.SimpleNamespace(id="vanguard", version="2.1.0",
                                         instances=[_Inst("long"), _Inst("short")])
        self.st = {"instances": {"long": {"strategyId": "S-long", "status": "creating"},
                                 "short": {"strategyId": "S-short", "status": "creating"}}}
        self._orig = (deploy._cli, deploy.save_state, deploy.POLL_EVERY)
        self.cli = _StubCli()
        deploy._cli = self.cli
        deploy.save_state = lambda pkg, st: None
        deploy.POLL_EVERY = 0  # no real sleeping

    def tearDown(self):
        (deploy._cli, deploy.save_state, deploy.POLL_EVERY) = self._orig

    def test_polls_until_active_and_records_wallet(self):
        pending = deploy.await_funded(None, self.pkg, self.st, [self.pkg.instances[0]],
                                      deadline=time.time() + 60)
        self.assertEqual(pending, [])
        self.assertEqual(self.st["instances"]["long"]["status"], "active")
        self.assertEqual(self.st["instances"]["long"]["wallet"], "0xabc")

    def test_scoped_to_the_given_leg_only(self):
        # Waiting on `long` must not poll — or settle — `short`.
        deploy.await_funded(None, self.pkg, self.st, [self.pkg.instances[0]],
                            deadline=time.time() + 60)
        self.assertNotIn("S-short", self.cli.polls)
        self.assertEqual(self.st["instances"]["short"]["status"], "creating")

    def test_expired_deadline_returns_pending_instead_of_looping(self):
        pending = deploy.await_funded(None, self.pkg, self.st, self.pkg.instances,
                                      deadline=time.time() - 1)
        self.assertEqual(sorted(pending), ["long=FUND_WALLET", "short=FUND_WALLET"])

    def test_already_active_leg_is_not_repolled(self):
        self.st["instances"]["long"]["status"] = "active"
        deploy.await_funded(None, self.pkg, self.st, [self.pkg.instances[0]],
                            deadline=time.time() + 60)
        self.assertEqual(self.cli.polls, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
