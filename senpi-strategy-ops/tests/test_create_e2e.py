#!/usr/bin/env python3
"""End-to-end `cmd_create` against a faked BACKEND — the M415566 incident, replayed.

Everything below the network boundary is real: package state, reconcile, the close sweep, the
create loop, `await_funded`, `report`. Only `MCPClient` / `_cli` / `close` are stubbed, so these
assert what the DEPLOY DID (which calls it made, in what order) rather than what a helper returned.
That distinction is the point — the bug this file guards was never inside a function, it was a
filter missing at the call site, so a unit test of the helper would have passed on broken code.

No MCP, no openclaw, no network, no sleeping. Run:
    python3 senpi-strategy-ops/tests/test_create_e2e.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import sys
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli as real_cli  # noqa: E402 — pure readers (dig) stay REAL, so payload shape is tested too
import deploy  # noqa: E402


class Backend:
    """The two-leg backend, with the funding outcome per leg scripted by the test.

    `stall` names the instances whose wallet never leaves FUND_WALLET — the losing side of the
    concurrent-funding race (`total_funded = 0`, parked in PENDING_FUNDING). Everything else goes
    ACTIVE on its next read, which is what a wallet funded with no sibling competing does.
    """

    def __init__(self, stall=(), settle_after=1):
        self.stall = set(stall)
        self.settle_after = settle_after   # reads a wallet needs before it reports ACTIVE
        self.rows = {}          # strategyId -> row
        self.creates = []       # ordered log of (budget, name) submitted
        self.closed = []        # ordered log of strategyIds handed to close_one
        self.runtimes = []      # wallets with a RUNNING runtime
        self.reads = {}         # strategyId -> how many times it has been polled
        self._n = 0

    # --- the money call ---------------------------------------------------
    def create(self, initialBudget, strategyName=None, **kw):
        self._n += 1
        sid = f"S-{self._n}"
        # Which leg is this? The wallet name carries it (`elephant-trend`), which is exactly how
        # `_wallet_name` builds it and how `_recover_wallet` reads it back.
        leg = (strategyName or "").rsplit("-", 1)[-1]
        self.creates.append({"budget": initialBudget, "name": strategyName, "id": sid})
        self.rows[sid] = {"id": sid, "strategyName": strategyName, "skillName": kw.get("skillName"),
                          "status": "FUND_WALLET", "strategyWalletAddress": "",
                          "totalFunded": 0, "_leg": leg}
        return {"strategyId": sid}

    def settle(self, sid):
        """One read tick: a non-stalled wallet becomes ACTIVE and funded once it has been polled
        `settle_after` times — real funding takes 8-20s, so a wallet is not ACTIVE on first read."""
        row = self.rows[sid]
        self.reads[sid] = self.reads.get(sid, 0) + 1
        if (row["_leg"] in self.stall or row["status"] != "FUND_WALLET"
                or self.reads[sid] < self.settle_after):
            return row
        row["status"] = "ACTIVE"
        row["strategyWalletAddress"] = "0xw" + sid[-1]
        row["totalFunded"] = self.creates[int(sid[-1]) - 1]["budget"]
        return row

    def adopt(self, sid, wallet="0xORPHAN"):
        """Plant a wallet as if a PREVIOUS, unrecorded run had created it."""
        self.rows[sid] = {"id": sid, "strategyName": "elephant-trend", "skillName": "elephant",
                          "status": "ACTIVE", "strategyWalletAddress": wallet, "totalFunded": 100,
                          "_leg": "trend"}


class FakeMCP:
    def __init__(self, backend, available=1000.0):
        self.backend, self.available = backend, available

    def mcp_call(self, tool, timeout=None, **kw):
        if tool == "account_get_portfolio":
            return {"data": {"portfolio": {"total_in_hyperliquid": self.available,
                                           "spot_balances": [], "token_balances": []}}}
        if tool == "strategy_create_custom_strategy":
            return self.backend.create(**kw)
        raise AssertionError(f"unexpected MCP call: {tool}")


class FakeCli:
    """Only the surface `cmd_create` touches. Reads SETTLE a wallet, mirroring a real poll."""

    # The payload readers stay REAL — `available_usd` walks the portfolio shape with these, so
    # faking them would stop this test from exercising the balance read at all.
    dig = staticmethod(real_cli.dig)

    def __init__(self, backend):
        self.b = backend

    def strategies_for(self, mcp, skill_name=None, strategy_id=None, wallet=None,
                       timeout=15, statuses=None):
        if strategy_id:
            return [self.b.settle(strategy_id)] if strategy_id in self.b.rows else []
        rows = [self.b.settle(s) for s in list(self.b.rows)]
        if skill_name:
            rows = [r for r in rows if r.get("skillName") == skill_name]
        if statuses:
            rows = [r for r in rows if r["status"] in statuses]
        return rows

    strategies_for_or_none = strategies_for

    def list_runtimes(self):
        return list(self.b.runtimes)

    def find_runtime_by_wallet(self, wallet):
        return {"wallet": wallet} if wallet in self.b.runtimes else None

    @staticmethod
    def runtime_running(rt):
        return bool(rt)

    @staticmethod
    def strategy_id_of(s):
        return s.get("strategyId") or s.get("id")

    @staticmethod
    def strategy_status(s):
        return s["status"]

    @staticmethod
    def strategy_wallet(s):
        return s["strategyWalletAddress"] or None

    @staticmethod
    def strategy_name(s):
        return s.get("strategyName")

    @staticmethod
    def strategy_open(s):
        return s["status"] not in ("CLOSED", "FAILED", "INACTIVE", "TERMINATED", "CLOSING_DONE")


def _inst(name, share):
    return types.SimpleNamespace(name=name, funding_share=share, runtime_name=f"elephant-{name}")


class CreateE2E(unittest.TestCase):
    """`elephant`: trend 0.6 / fade 0.4 — the package from the incident."""

    def setUp(self):
        self.pkg = types.SimpleNamespace(id="elephant", version="1.0.0", dir=Path("/nonexistent"),
                                         instances=[_inst("trend", 0.6), _inst("fade", 0.4)])
        self.state = {"instances": {}}
        self._orig = (deploy._cli, deploy.MCPClient, deploy.load_state, deploy.save_state,
                      deploy.strategy_min, deploy.POLL_EVERY, sys.modules.get("close"))
        deploy.load_state = lambda pkg: self.state
        deploy.save_state = lambda pkg, st: None
        deploy.strategy_min = lambda pkg: {"min_budget": 20.0, "wallet_count": 2,
                                           "binding_wallet": "trend"}
        deploy.POLL_EVERY = 0

    def tearDown(self):
        (deploy._cli, deploy.MCPClient, deploy.load_state, deploy.save_state,
         deploy.strategy_min, deploy.POLL_EVERY, _close) = self._orig
        if _close is None:
            sys.modules.pop("close", None)
        else:
            sys.modules["close"] = _close

    def run_create(self, backend, max_wait=60):
        deploy._cli = FakeCli(backend)
        deploy.MCPClient = lambda: FakeMCP(backend)
        fake_close = types.ModuleType("close")
        fake_close.close_one = lambda pkg_id, s, runtimes, force, log: (
            backend.closed.append(FakeCli.strategy_id_of(s)) or {"status": "closed"})
        sys.modules["close"] = fake_close
        a = argparse.Namespace(budget=100.0, max_wait=max_wait, json=False,
                               dry_run=False, instance=None)
        return deploy.cmd_create(self.pkg, a, lambda m: None)

    # --- commit 1: one wallet at a time -----------------------------------

    def test_second_leg_is_not_submitted_until_the_first_is_active(self):
        b = Backend(stall=["trend"])          # the incident: the LARGER leg loses the race
        out = self.run_create(b, max_wait=0)  # budget lapses immediately
        self.assertEqual(out["status"], "creating")
        # THE assertion. Pre-fix, both legs were submitted back-to-back before any polling — which
        # is what put two funding jobs on one embedded wallet ~1s apart.
        self.assertEqual(len(b.creates), 1, f"submitted {len(b.creates)} legs, expected 1")
        self.assertEqual(b.creates[0]["name"], "elephant-trend")

    def test_both_legs_fund_when_neither_stalls(self):
        b = Backend()
        out = self.run_create(b)
        self.assertEqual(out["status"], "wallets-ready")
        self.assertEqual([c["name"] for c in b.creates], ["elephant-trend", "elephant-fade"])
        self.assertEqual([c["budget"] for c in b.creates], [60.0, 40.0])  # 0.6 / 0.4 split

    # --- commit 2: a re-run adopts, it does not close ---------------------

    def test_rerun_adopts_its_own_funded_wallet_instead_of_closing_it(self):
        """The $39. Leg 1 funded, leg 2 never got submitted, the budget lapsed — now re-run."""
        b = Backend(settle_after=2)         # leg 1 is still funding when the budget lapses
        first = self.run_create(b, max_wait=0)
        self.assertEqual(first["status"], "creating")
        funded = b.creates[0]["id"]

        second = self.run_create(b)           # same state, same backend — the agent's re-run
        self.assertEqual(b.closed, [], f"re-run CLOSED {b.closed} — that is the fee burn")
        self.assertEqual(second["status"], "wallets-ready")
        self.assertIn(funded, [c["id"] for c in b.creates])   # adopted, not replaced
        self.assertEqual(len(b.creates), 2, "a third wallet was created — the leg was not adopted")

    def test_an_unrecorded_wallet_is_still_closed(self):
        """The orphan protection this must not weaken."""
        b = Backend()
        b.adopt("S-orphan")
        out = self.run_create(b)
        self.assertEqual(out["status"], "closing-existing")
        self.assertEqual(b.closed, ["S-orphan"])
        self.assertEqual(b.creates, [], "funded a wallet while an orphan was still open")

    def test_a_live_running_strategy_still_refuses(self):
        b = Backend()
        b.adopt("S-live", wallet="0xLIVE")
        b.runtimes.append("0xLIVE")
        with self.assertRaises(SystemExit) as cm:
            self.run_create(b)
        self.assertIn("already deployed AND running", str(cm.exception))
        self.assertEqual(b.closed, [], "closed a LIVE strategy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
