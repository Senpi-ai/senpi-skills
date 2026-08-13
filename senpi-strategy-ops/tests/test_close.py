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
import contextlib
import io
import json
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

    def test_failed_status_is_idempotent_noop_not_closing(self):
        # FAILED is terminal (in `_cli.DEAD_STATUSES`) but was missing from close.py's OWN, separately
        # maintained `_CLOSED` tuple — a FAILED row matched neither the early-return NOR the ACTIVE
        # submit condition below, so it fell through to the final `rec["status"] = "closing"` having
        # done nothing: a terminal, already-dead strategy misreported as freshly triggered.
        _cli.list_runtimes_or_none = lambda: []
        rec = close.close_one("main", dict(self._STRAT, status="FAILED"), self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closed")
        self.assertEqual(self.deletes, [])             # nothing to stop — terminal, return before step 1
        self.assertEqual(self.closed, [])              # nothing to submit — already terminal

    def test_dry_run_labels_failed_as_already_closed(self):
        rec = close.close_one("main", dict(self._STRAT, status="FAILED"), self._RUNTIMES, True, lambda m: None)
        self.assertIn("(already closed)", rec["plan"])


class SelectDirectTarget(unittest.TestCase):
    """`--strategy-id`/`--address` address ONE specific strategy directly — unlike `--all`/`<package>`,
    which answer a SET question ("what's open for X"), where an empty result is a legitimate true
    fact. Naming one target and getting zero rows back must refuse, not fall through to the generic
    "no OPEN strategies to close" + exit 0 all-clear — that would read as success on a typo'd id
    over a live, funded wallet. Matching more than one refuses rather than guessing which to close."""

    def test_no_match_refuses_rather_than_reporting_nothing_to_close(self):
        with self.assertRaises(SystemExit) as ctx:
            close._select_direct_target([], "ef758844-notreal")
        msg = str(ctx.exception)
        self.assertIn("no strategy found", msg)
        self.assertIn("ef758844-notreal", msg)

    def test_exactly_one_match_returns_it(self):
        row = {"strategyId": "s1"}
        self.assertIs(close._select_direct_target([row], "s1"), row)

    def test_multiple_matches_refuses_rather_than_guessing(self):
        rows = [{"strategyId": "s1"}, {"strategyId": "s2"}]
        with self.assertRaises(SystemExit) as ctx:
            close._select_direct_target(rows, "0xsamewallet")
        msg = str(ctx.exception)
        self.assertIn("2 strategies", msg)
        self.assertIn("s1", msg)
        self.assertIn("s2", msg)


class MainDirectAddressing(unittest.TestCase):
    """End-to-end wiring for `--strategy-id`/`--address` through `main()`: argparse mutual exclusion,
    the unfiltered `strategies_for_or_none` lookup, and that a single-target close reports its OWN
    strategy id — not the `--all` branch's `"ALL"` label, which `out["strategy"]` fell back to for
    every non-package close before this fix."""

    def setUp(self):
        self._orig = (close.MCPClient, _cli.strategies_for_or_none, _cli.list_runtimes,
                     _cli.list_runtimes_or_none, _cli.run_cli)
        self.closed = []
        self.rows = []
        outer = self

        class _FakeMCP:
            def mcp_call(self, name, timeout=None, **kw):
                if name == "strategy_close":
                    outer.closed.append(kw.get("strategyId"))
                    return {"success": True}
                raise AssertionError(f"unexpected mcp_call {name!r}")
        close.MCPClient = _FakeMCP

        def _fake_strategies_for(mcp, skill_name=None, strategy_id=None, wallet=None, timeout=15,
                                 statuses=None, why=None):
            return [s for s in outer.rows
                    if (strategy_id is None or _cli.strategy_id_of(s) == strategy_id)
                    and (wallet is None
                         or str(_cli.strategy_wallet(s) or "").lower() == str(wallet).lower())]
        _cli.strategies_for_or_none = _fake_strategies_for
        _cli.list_runtimes = lambda: []
        _cli.list_runtimes_or_none = lambda: []
        _cli.run_cli = lambda args, timeout=60: (0, "", "")

    def tearDown(self):
        (close.MCPClient, _cli.strategies_for_or_none, _cli.list_runtimes,
         _cli.list_runtimes_or_none, _cli.run_cli) = self._orig

    def _run(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                close.main(["close.py", *args])
        return buf.getvalue(), ctx.exception.code

    def test_strategy_id_and_address_together_refuses(self):
        _out, code = self._run("--strategy-id", "s1", "--address", "0xabc")
        self.assertNotEqual(code, 0)

    def test_strategy_id_with_package_refuses(self):
        _out, code = self._run("spider", "--strategy-id", "s1")
        self.assertNotEqual(code, 0)

    def test_strategy_id_with_all_refuses(self):
        _out, code = self._run("--all", "--strategy-id", "s1")
        self.assertNotEqual(code, 0)

    def test_no_target_at_all_refuses(self):
        _out, code = self._run()
        self.assertNotEqual(code, 0)

    def test_unknown_strategy_id_refuses_not_the_all_clear(self):
        self.rows = []
        out, code = self._run("--strategy-id", "nope")
        self.assertNotEqual(code, 0)
        self.assertNotIn("no OPEN strategies to close", out)

    def test_direct_close_reports_its_own_id_not_all(self):
        self.rows = [{"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}]
        out, code = self._run("--strategy-id", "s1", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["strategy"], "s1")
        self.assertEqual(payload["status"], "closing")
        self.assertEqual(self.closed, ["s1"])

    def test_address_matches_case_insensitively(self):
        self.rows = [{"strategyId": "s1", "strategyWalletAddress": "0xABC", "status": "ACTIVE"}]
        out, code = self._run("--address", "0xabc", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(self.closed, ["s1"])

    def test_direct_close_poll_hint_names_the_target_not_all(self):
        self.rows = [{"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}]
        out, _code = self._run("--strategy-id", "s1")
        self.assertIn("close.py --strategy-id s1", out)
        self.assertNotIn("close.py --all", out)

    def test_direct_dry_run_touches_no_mcp(self):
        self.rows = [{"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}]
        out, code = self._run("--address", "0xabc", "--dry-run", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["instances"][0]["status"], "planned")
        self.assertEqual(self.closed, [])


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
