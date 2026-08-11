#!/usr/bin/env python3
"""Hermetic unit tests for `close.py`'s teardown primitive.

No MCP, no openclaw, no network — every input is a plain dict/stub. Run:
    python3 senpi-strategy-ops/tests/test_close.py

Rescued from `test_deploy_gates.py`, which was deleted when `deploy.py` became a thin wrapper over
`openclaw senpi deploy`: the gates it covered moved into the runtime verb, but THIS class never
tested a deploy gate — it tests `close.py::close_one`, which is untouched by that rewrite and is
still the only sanctioned teardown path. Deleting it with its old home would have left the money
path it guards with no coverage at all. (Same move as `18a72d34`, which kept the deleted suite's
`has_dsl` branch coverage by relocating it into `test_pkg.py`.)
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli    # noqa: E402
import close   # noqa: E402


class CloseRuntimeDeleteConfirm(unittest.TestCase):
    """close_one must judge runtime-delete success by `runtime list` (reliable), NOT the delete's exit
    code — which is non-zero both on a flaky gateway hiccup AND when the runtime is already gone
    (NOT_FOUND). Trusting rc broke idempotent re-runs and false-aborted the money-critical strategy_close."""

    def setUp(self):
        self._orig = (_cli.run_cli, _cli.list_runtimes_or_none, close.MCPClient)
        self.deletes = []          # every `runtime delete` invocation
        self.closed = []           # every strategy_close strategyId
        _cli.run_cli = lambda args, timeout=60: (self.deletes.append(args) or (1, "", "[⚡HyperDX] banner…"))
        outer = self

        class _FakeMCP:
            def mcp_call(self, name, timeout=None, **kw):
                if name == "strategy_close":
                    outer.closed.append(kw.get("strategyId"))
                return {"success": True}
        close.MCPClient = _FakeMCP

    def tearDown(self):
        _cli.run_cli, _cli.list_runtimes_or_none, close.MCPClient = self._orig

    _STRAT = {"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}
    _RUNTIMES = [{"name": "pkg-main", "wallet": "0xabc"}]

    def test_gone_after_delete_is_success_and_triggers_close(self):
        # delete returns non-zero (banner noise), but the inventory reads cleanly and the runtime is gone
        _cli.list_runtimes_or_none = lambda: []
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closing")   # NOT 'failed' despite rc=1
        self.assertEqual(self.closed, ["s1"])         # money-critical close DID fire
        self.assertEqual(len(self.deletes), 1)        # gone on first try → no retry

    def test_still_present_after_retry_fails_without_closing(self):
        # runtime never leaves `runtime list` → genuine failure: report it, do NOT strategy_close
        _cli.list_runtimes_or_none = lambda: [{"name": "pkg-main", "wallet": "0xabc"}]
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(self.closed, [])             # never close while the runtime can re-enter
        self.assertEqual(len(self.deletes), 2)        # one retry before giving up
        self.assertNotIn("HyperDX", rec.get("error", ""))  # clean message, not banner spam

    def test_unreadable_inventory_fails_closed(self):
        # THE money-path guard: `runtime list` unreadable (None) must NOT read as 'gone' → no strategy_close
        _cli.list_runtimes_or_none = lambda: None
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(self.closed, [])             # unreadable inventory ⇒ never flatten a maybe-live strategy
        self.assertEqual(len(self.deletes), 2)

    def test_already_closed_is_idempotent_noop(self):
        _cli.list_runtimes_or_none = lambda: []
        rec = close.close_one("main", dict(self._STRAT, status="CLOSED"), self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closed")
        self.assertEqual(self.deletes, [])            # nothing to stop
        self.assertEqual(self.closed, [])             # nothing to close


class ReadOrRefuse(unittest.TestCase):
    """An unreadable `strategy_list` must not become "no OPEN strategies to close." + exit 0 — a
    positive all-clear on the one path where being wrong strands live, funded wallets."""

    def test_a_readable_answer_passes_through(self):
        self.assertEqual(close._read_or_refuse([], [], "spider"), [])
        self.assertEqual(close._read_or_refuse([{"id": "s1"}], [], "spider"), [{"id": "s1"}])

    def test_an_unreadable_list_refuses_instead_of_reporting_nothing_to_close(self):
        with self.assertRaises(SystemExit) as ctx:
            close._read_or_refuse(None, ["the MCP `strategy_list` call failed (no token)"], "spider")
        msg = str(ctx.exception)
        self.assertIn("NOTHING was closed", msg)
        self.assertIn("no token", msg)          # the cause reaches the operator
        self.assertNotIn("no OPEN strategies", msg)

    def test_the_refusal_is_not_exit_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            close._read_or_refuse(None, [], "all open strategies")
        # SystemExit carrying a string exits 1 — the point is only that it is never 0.
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
