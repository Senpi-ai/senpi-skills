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
import os
import shutil
import sys
import tempfile
import types
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli    # noqa: E402
import close   # noqa: E402
import _journal  # noqa: E402


def _gw(kind, payload=None, message=None):
    """A `_cli.gateway_call` result, as the real classifier returns it."""
    return {"kind": kind, "payload": payload, "message": message, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


class CloseRuntimeDeleteConfirm(unittest.TestCase):
    """close_one must judge runtime-delete success by `senpi.listRuntimes` (reliable), NOT the
    delete's answer — which is an error both on a flaky gateway hiccup AND when the runtime is already
    gone (not found). Trusting it broke idempotent re-runs and false-aborted the money-critical
    strategy_close."""

    def setUp(self):
        self._orig = (_cli.gateway_call, _cli.list_runtimes_or_none, close.MCPClient)
        self.deletes = []          # every `senpi.deleteRuntime` call: (params, rpc ms, wall s)
        self.closed = []           # every strategy_close strategyId

        def fake_gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            assert method == "senpi.deleteRuntime", method
            self.deletes.append((params, rpc_timeout_ms, timeout_s))
            return _gw("error", message="No running runtime found for id pkg-main")
        _cli.gateway_call = fake_gateway
        outer = self

        class _FakeMCP:
            def mcp_call(self, name, timeout=None, **kw):
                if name == "strategy_close":
                    outer.closed.append(kw.get("strategyId"))
                return {"success": True}
        close.MCPClient = _FakeMCP

    def tearDown(self):
        _cli.gateway_call, _cli.list_runtimes_or_none, close.MCPClient = self._orig

    _STRAT = {"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}
    _RUNTIMES = [{"name": "pkg-main", "wallet": "0xabc"}]

    def test_gone_after_delete_is_success_and_triggers_close(self):
        # delete returns non-zero (banner noise), but the inventory reads cleanly and the runtime is gone
        _cli.list_runtimes_or_none = lambda why=None: []
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closing")   # NOT 'failed' despite rc=1
        self.assertEqual(self.closed, ["s1"])         # money-critical close DID fire
        self.assertEqual(len(self.deletes), 1)        # gone on first try → no retry

    def test_the_delete_is_a_direct_gateway_call_by_id_with_its_own_budget(self):
        _cli.list_runtimes_or_none = lambda why=None: []
        close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        # `{id}` only: the handler ignores `address` when `id` is given. 90 s RPC / 100 s wall —
        # a DSL teardown with open positions makes MCP calls.
        self.assertEqual(self.deletes, [({"id": "pkg-main"}, 90000, 100)])

    def test_still_present_after_retry_fails_without_closing(self):
        # runtime never leaves `runtime list` → genuine failure: report it, do NOT strategy_close
        _cli.list_runtimes_or_none = lambda why=None: [{"name": "pkg-main", "wallet": "0xabc"}]
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(self.closed, [])             # never close while the runtime can re-enter
        self.assertEqual(len(self.deletes), 2)        # one retry before giving up
        self.assertNotIn("No running runtime", rec.get("error", ""))  # clean message, not the raw answer
        self.assertIn("senpi.deleteRuntime", rec["error"])      # names the direct call to retry with
        self.assertIn("strategy_close was NOT sent", rec["error"])

    def test_unreadable_inventory_fails_closed(self):
        # THE money-path guard: `runtime list` unreadable (None) must NOT read as 'gone' → no strategy_close
        _cli.list_runtimes_or_none = lambda why=None: None
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(self.closed, [])             # unreadable inventory ⇒ never flatten a maybe-live strategy
        self.assertEqual(len(self.deletes), 2)

    def _killed(self, rc, message=None):
        def fake_gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.deletes.append((params, rpc_timeout_ms, timeout_s))
            return dict(_gw("unreadable", message=message or _cli.killed_note(rc)), rc=rc)
        _cli.gateway_call = fake_gateway

    def test_a_killed_delete_is_not_retried_and_says_read_status_not_re_run(self):
        self._killed(-9)
        _cli.list_runtimes_or_none = lambda why=None: [{"name": "pkg-main", "wallet": "0xabc"}]
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(len(self.deletes), 1)        # no second delete into a box that killed the first
        self.assertEqual(self.closed, [])
        self.assertIn("killed by SIGKILL", rec["error"])
        self.assertIn("out of memory", rec["error"])
        self.assertIn(_cli.STATUS_PY, rec["error"])
        self.assertIn("Do not re-run this or any other command now", rec["error"])
        self.assertEqual((rec["oom"], rec["killed_rc"]), (True, -9))

    def test_a_retry_that_answered_gets_the_manual_delete_whatever_killed_the_first(self):
        # First delete SIGTERMed (a gateway restart), the retry ANSWERED, and the runtime is still
        # listed: a stuck runtime. The steer is the hand-run delete, not "killed … after the retry".
        answers = [dict(_gw("unreadable", message=_cli.killed_note(-15)), rc=-15),
                   _gw("error", message="still running")]

        def fake_gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.deletes.append((params, rpc_timeout_ms, timeout_s))
            return answers.pop(0)
        _cli.gateway_call = fake_gateway
        _cli.list_runtimes_or_none = lambda why=None: [{"name": "pkg-main", "wallet": "0xabc"}]
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(len(self.deletes), 2)
        self.assertEqual(self.closed, [])
        self.assertIn("openclaw gateway call senpi.deleteRuntime", rec["error"])
        self.assertNotIn("killed by SIGTERM", rec["error"])

    def test_a_sigkilled_delete_names_the_way_back_to_close(self):
        # Round 3: "do not re-run close" with no condition stranded a funded strategy. The stop now
        # says what is true, when close is safe again (a clean status.py read), and the exact command.
        self._killed(-9)
        _cli.list_runtimes_or_none = lambda why=None: [{"name": "pkg-main", "wallet": "0xabc"}]
        rerun = "python3 /x/close.py omega"
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None, rerun)
        err = rec["error"]
        self.assertIn("NOT closed yet", err)
        self.assertIn("may still be trading", err)
        self.assertIn(f"`python3 {_cli.STATUS_PY}` exiting 0 is the sign", err)
        self.assertIn("it exits 0 only once it has read the runtime list;", err)
        self.assertIn(f"Then re-run: {rerun}", err)
        self.assertIn("Do not call strategy_close directly", err)

    def test_a_sigkilled_retry_gets_the_kill_steer_not_hand_run_the_delete(self):
        # First delete answers an error (runtime still listed), the retry is SIGKILLed: the steer is
        # the out-of-memory stop, never "hand-run senpi.deleteRuntime" into the box that just killed it.
        answers = iter([_gw("error", message="boom"), dict(_gw("unreadable", message=_cli.killed_note(-9)), rc=-9)])

        def fake_gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.deletes.append(params)
            return next(answers)
        _cli.gateway_call = fake_gateway
        _cli.list_runtimes_or_none = lambda why=None: [{"name": "pkg-main", "wallet": "0xabc"}]
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(len(self.deletes), 2)
        self.assertEqual(self.closed, [])
        self.assertIn("killed by SIGKILL", rec["error"])
        self.assertIn("NOT closed yet", rec["error"])
        self.assertNotIn("openclaw gateway call", rec["error"])

    def test_a_sigkilled_confirming_read_gets_the_kill_steer_too(self):
        def killed_read(why=None):
            why.append(_cli.Cause("senpi.listRuntimes: unreadable: " + _cli.killed_note(-9), -9))
            return None
        _cli.list_runtimes_or_none = killed_read
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(len(self.deletes), 1)
        self.assertEqual(self.closed, [])
        self.assertIn("the read that confirms the delete was killed by SIGKILL", rec["error"])
        self.assertIn("NOT closed yet", rec["error"])

    def test_a_delete_killed_by_another_signal_is_retried_and_not_called_out_of_memory(self):
        # Only SIGKILL skips the retry: a SIGTERM or a crash is not the box's memory, the delete is
        # idempotent, and the retry is what lets the teardown finish in the same run.
        self._killed(-15)
        _cli.list_runtimes_or_none = lambda why=None: None
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(len(self.deletes), 2)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("killed by SIGTERM", rec["error"])
        self.assertNotIn("memory", rec["error"])
        self.assertIn(_cli.STATUS_PY, rec["error"])

    def test_a_sigterm_then_a_clean_retry_still_closes(self):
        answers = iter([dict(_gw("unreadable", message=_cli.killed_note(-15)), rc=-15), _gw("ok", payload={})])
        gone = iter([None, []])

        def fake_gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.deletes.append(params)
            return next(answers)
        _cli.gateway_call = fake_gateway
        _cli.list_runtimes_or_none = lambda why=None: next(gone)
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closing")
        self.assertEqual(self.closed, ["s1"])

    def test_a_killed_delete_that_did_stop_the_runtime_still_closes(self):
        self._killed(-9)
        _cli.list_runtimes_or_none = lambda why=None: []
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closing")
        self.assertEqual(self.closed, ["s1"])

    def test_the_log_clip_never_cuts_the_kill(self):
        kill = _cli.killed_note(-9)
        self._killed(-9, message="x" * 500 + "\n" + kill)
        _cli.list_runtimes_or_none = lambda why=None: []
        lines = []
        close.close_one("main", self._STRAT, self._RUNTIMES, False, lines.append)
        logged = next(m for m in lines if "deleteRuntime" in m)
        self.assertIn(kill, logged)

    def test_already_closed_is_idempotent_noop(self):
        _cli.list_runtimes_or_none = lambda why=None: []
        rec = close.close_one("main", dict(self._STRAT, status="CLOSED"), self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closed")
        self.assertEqual(self.deletes, [])            # nothing to stop
        self.assertEqual(self.closed, [])             # nothing to close

    def test_failed_status_is_idempotent_noop_not_closing(self):
        # FAILED is terminal (in `_cli.DEAD_STATUSES`) but was missing from close.py's OWN, separately
        # maintained `_CLOSED` tuple — a FAILED row matched neither the early-return NOR the ACTIVE
        # submit condition below, so it fell through to the final `rec["status"] = "closing"` having
        # done nothing: a terminal, already-dead strategy misreported as freshly triggered.
        _cli.list_runtimes_or_none = lambda why=None: []
        rec = close.close_one("main", dict(self._STRAT, status="FAILED"), self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closed")
        self.assertEqual(self.deletes, [])             # nothing to stop — terminal, return before step 1
        self.assertEqual(self.closed, [])              # nothing to submit — already terminal

    def test_dry_run_labels_failed_as_already_closed(self):
        rec = close.close_one("main", dict(self._STRAT, status="FAILED"), self._RUNTIMES, True, lambda m: None)
        self.assertIn("(already closed)", rec["plan"])


class CloseLastRuntimeOnTheBox(unittest.TestCase):
    """The teardown money path, driven through the REAL `list_runtimes_or_none` reader rather than a
    stub of it, because that reader is what decides empty-vs-unreadable here.

    Deleting the LAST runtime on a box leaves an inventory of `{"runtimes": []}`. Reading that as
    unreadable made `_runtime_gone` False, so `close_one` retried the delete and then reported
    `status: "failed"` on a teardown that had actually succeeded (seen with the old `runtime list`
    text parser, which missed the "No runtimes installed." line)."""

    def setUp(self):
        self._orig = (_cli.gateway_call, close.MCPClient)
        self.deletes, self.closed = [], []
        self.inventory = _gw("ok", {"runtimes": []})     # what a box with nothing installed answers
        outer = self

        def fake_gateway(method, params=None, **k):
            if method == "senpi.listRuntimes":
                return outer.inventory
            outer.deletes.append((method, params))
            return _gw("error", message="No running runtime found")   # delete answer is unreliable
        _cli.gateway_call = fake_gateway

        class _FakeMCP:
            def mcp_call(self, name, timeout=None, **kw):
                if name == "strategy_close":
                    outer.closed.append(kw.get("strategyId"))
                return {"success": True}
        close.MCPClient = _FakeMCP

    def tearDown(self):
        _cli.gateway_call, close.MCPClient = self._orig

    _STRAT = {"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}
    _RUNTIMES = [{"name": "pkg-main", "wallet": "0xabc"}]

    def test_the_empty_inventory_left_behind_reads_as_gone(self):
        self.assertEqual(_cli.list_runtimes_or_none(), [])
        self.assertTrue(close._runtime_gone("pkg-main"))

    def test_tearing_down_the_last_runtime_reports_success_not_failure(self):
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closing")     # was "failed" on a delete that worked
        self.assertEqual(rec["runtime"], "stopped")
        self.assertEqual(self.closed, ["s1"])
        self.assertEqual(len(self.deletes), 1)         # gone on the first read => no redundant retry

    def test_an_unreadable_inventory_on_the_same_path_still_fails_closed(self):
        # The guard must survive: an answer with no runtimes list, or no answer, is NOT an empty box.
        for inventory in (_gw("ok", {"garbled": True}), _gw("timeout")):
            self.deletes, self.closed = [], []
            self.inventory = inventory
            rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
            self.assertEqual(rec["status"], "failed")
            self.assertEqual(self.closed, [])
            self.assertEqual(len(self.deletes), 2)


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
        _cli.list_runtimes = lambda *a, **k: []
        _cli.list_runtimes_or_none = lambda *a, **k: []
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

    def test_unreadable_runtime_list_refuses_without_closing(self):
        self.rows = [{"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}]
        _cli.list_runtimes_or_none = lambda *a, **k: None   # openclaw is here, `senpi.listRuntimes` timed out
        _out, code = self._run("--strategy-id", "s1")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.closed, [])

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

    def test_an_unreadable_runtime_inventory_refuses_and_closes_nothing(self):
        # Was fail-open: `list_runtimes()` read a failed inventory as "no runtime", so every target
        # skipped the runtime delete and strategy_close fired while its runtime kept trading.
        self.rows = [{"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}]
        _cli.list_runtimes_or_none = lambda *a, **k: (k["why"].append("senpi.listRuntimes: timeout: x")
                                                      if k.get("why") is not None else None)
        with self.assertRaises(SystemExit) as ctx:
            close.main(["close.py", "--strategy-id", "s1"])
        msg = str(ctx.exception.code)
        self.assertIn("NOTHING WAS CLOSED", msg)
        self.assertIn("senpi.listRuntimes: timeout", msg)      # the cause reaches the operator
        self.assertIn("Do not call strategy_close directly", msg)
        self.assertEqual(self.closed, [])

    def test_no_openclaw_on_this_host_refuses_and_names_the_agent_box(self):
        self.rows = [{"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}]
        _cli.run_cli = lambda args, timeout=60: (-1, "", _cli.SPAWN_FAILED_PREFIX + "openclaw")
        _cli.list_runtimes_or_none = lambda *a, **k: None
        with self.assertRaises(SystemExit) as ctx:
            close.main(["close.py", "--strategy-id", "s1"])
        self.assertIn("Run this command on the agent's own box", str(ctx.exception.code))
        self.assertEqual(self.closed, [])

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


class MainPackagePollReportsClosed(unittest.TestCase):
    """`close.py <pkg>` is the POLL the module docstring documents: "reports `closed` once the strategy
    leaves the active set".

    It could never print it. The package read is filtered server-side by `LIVE_STATUSES`, so the moment
    `strategy_close` settles the row becomes `CLOSED` (a `DEAD_STATUSES` member), the read returns zero
    rows, and `main` falls through to the generic `"no OPEN strategies to close."` + exit 0 — byte
    identical to "this package was never deployed here". An agent polling for `closed` reads that
    all-clear as completion and runs `deploy.py create`, which mints a fresh wallet. A close/redeploy
    loop therefore leaks one wallet per pass, each carrying whatever funding it was given.

    `--strategy-id`/`--address` was already fixed for exactly this (it reads UNFILTERED by status,
    because "a status-filtered read cannot tell 'no such id' from 'already closed'"). This is that same
    fix on the package path."""

    def setUp(self):
        self._orig = (close.MCPClient, _cli.strategies_for_or_none, _cli.list_runtimes,
                      _cli.list_runtimes_or_none, _cli.run_cli, close._pkg.load, _cli.gateway_call)
        self.closed = []
        self.rows = []
        self.unreadable = False
        # What `senpi.deploy.status` answers. Default: no deploy ever ran on this agent.
        self.job_status = _gw("refused", message="[NOT_FOUND] No deploy has been started on this agent.")
        self.status_reads = 0
        outer = self

        def _fake_gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            if method != "senpi.deploy.status":
                raise AssertionError(f"unexpected gateway call {method!r}")
            outer.status_reads += 1
            return outer.job_status
        _cli.gateway_call = _fake_gateway

        class _FakeMCP:
            def mcp_call(self, name, timeout=None, **kw):
                if name == "strategy_close":
                    outer.closed.append(kw.get("strategyId"))
                    return {"success": True}
                raise AssertionError(f"unexpected mcp_call {name!r}")
        close.MCPClient = _FakeMCP

        def _fake_strategies_for(mcp, skill_name=None, strategy_id=None, wallet=None, timeout=15,
                                 statuses=None, why=None):
            # The point of the test: HONOUR `statuses`, the way the real server-side filter does.
            if statuses is None and outer.unreadable:
                return None                      # unreadable second read -> must fail closed
            rows = list(outer.rows)
            if statuses is not None:
                want = {str(s).upper() for s in statuses}
                rows = [s for s in rows if str(_cli.strategy_status(s) or "").upper() in want]
            return rows
        _cli.strategies_for_or_none = _fake_strategies_for
        _cli.list_runtimes = lambda *a, **k: []
        _cli.list_runtimes_or_none = lambda *a, **k: []
        _cli.run_cli = lambda args, timeout=60: (0, "", "")

        class _FakeInst:
            name = "main"

        class _FakePkg:
            id = "omega"
            version = "1.0.0"
            instances = [_FakeInst()]
            dir = Path("/nonexistent-close-py-test-path")   # .deploy-state.json unlink is OSError-guarded
        close._pkg.load = lambda _p: _FakePkg()

    def tearDown(self):
        (close.MCPClient, _cli.strategies_for_or_none, _cli.list_runtimes,
         _cli.list_runtimes_or_none, _cli.run_cli, close._pkg.load, _cli.gateway_call) = self._orig

    def _run(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                close.main(["close.py", *args])
        return buf.getvalue(), ctx.exception.code

    def _run_oom(self, *args):
        """The out-of-memory stop: exit `EXIT_OOM`, the stop on stderr. Returns the stderr text."""
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                close.main(["close.py", *args])
        self.assertEqual(ctx.exception.code, _cli.EXIT_OOM)
        return err.getvalue()

    _CLOSED_ROW = {"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "CLOSED"}
    _ACTIVE_ROW = {"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}

    def _seed_open_request(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))
        rid = "dpl-req-20261028T000000Z-deadbeef"
        _journal.write("omega", {"packageId": "omega", "requestId": rid, "sentAt": "2026-10-28T00:00:00Z",
                                 "verb": "create", "paramsKey": {"packageDir": f"{tmp}/omega", "budget": 30.0},
                                 "resolved": None})
        return Path(tmp), rid

    def _next_create_request_id(self, root):
        import deploy
        _cli.gateway_call = self._orig[-1]      # the real classifier over the stubbed _spawn below
        real = _cli._spawn
        self.addCleanup(setattr, _cli, "_spawn", real)
        sent = []
        _cli._spawn = lambda argv, timeout=60: (sent.append(argv) or
                                                (0, json.dumps({"deployId": "dpl-new"}), "", False, None))
        pkg = types.SimpleNamespace(dir=str(root / "omega"), id="omega", version="1.0.0", instances=[])
        args = types.SimpleNamespace(budget=30.0, decision_model=None, tick_wait=None, max_wait=60,
                                     json=False, dry_run=False, request_ids=True, cmd="create")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(deploy.start_deploy(pkg, args, lambda m: None), "dpl-new")
        return json.loads(sent[0][sent[0].index("--params") + 1])["requestId"]

    def test_a_triggered_close_closes_the_packages_open_deploy_request(self):
        # START lost → recovery missed → the user closes → redeploys: the reused id would hand the next
        # create the OLD (now closed) job as `repeated`, and nothing would run.
        root, rid = self._seed_open_request()
        self.rows = [self._ACTIVE_ROW]
        _out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(_journal.read("omega")["resolved"], "closed-by-close.py")
        self.assertNotEqual(self._next_create_request_id(root), rid)

    def test_a_killed_inventory_read_never_adds_a_re_run(self):
        # The cause already says stop (out of memory); a "Re-run this command" cure under it contradicts it.
        # The kill is read off the cause's rc, not its wording.
        def killed(why=None):
            why.append(_cli.Cause("senpi.listRuntimes: unreadable: " + _cli.killed_note(-9)
                                  + _cli.read_killed_hint(-9), -9))
            return None
        _cli.list_runtimes_or_none = killed
        self.rows = [self._ACTIVE_ROW]
        msg = self._run_oom("omega")
        self.assertIn("NOTHING WAS CLOSED", msg)
        self.assertIn("out of memory", msg)
        self.assertNotIn("Re-run this command", msg)
        self.assertEqual(self.closed, [])

    def test_a_killed_inventory_read_names_when_close_is_safe_again(self):
        def killed(why=None):
            why.append(_cli.Cause("senpi.listRuntimes: unreadable: reworded, no hint", -9))
            return None
        _cli.list_runtimes_or_none = killed
        self.rows = [self._ACTIVE_ROW]
        msg = self._run_oom("omega")
        self.assertIn("NOT closed yet", msg)
        self.assertIn("re-run: python3 ", msg)
        self.assertIn("close.py omega", msg)
        self.assertEqual(msg.count("Do not call strategy_close directly"), 1)

    def test_instance_close_off_an_unreadable_inventory_stops_and_never_says_close_all(self):
        # `--instance` maps the instance to its wallet through the inventory. An unreadable read used
        # to be "no runtime" and steered at "Omit --instance to close all", which tears down the
        # instances the user wanted kept.
        _cli.list_runtimes_or_none = lambda why=None: (
            why.append(_cli.Cause("senpi.listRuntimes: timeout: no answer within 25s", -1)), None)[1]
        self.rows = [self._ACTIVE_ROW]
        _out, code = self._run("omega", "--instance", "main")
        self.assertIn("NOTHING WAS CLOSED", str(code))
        self.assertNotIn("Omit --instance", str(code))
        self.assertNotIn("close all", str(code))
        self.assertEqual(self.closed, [])

    def test_a_killed_openclaw_version_is_not_openclaw_missing(self):
        _cli.run_cli = lambda args, timeout=60: (-9, "", _cli.killed_note(-9))   # tearDown restores it
        _cli.list_runtimes_or_none = lambda why=None: (why.append(_cli.Cause("senpi.listRuntimes: error: x", 1)), None)[1]
        self.rows = [self._ACTIVE_ROW]
        msg = self._run_oom("omega")
        self.assertNotIn("not available on this host", msg)
        self.assertIn("out of memory", msg)

    def test_box_p2_03_every_strategy_already_closed_closes_the_open_request(self):
        # Box A T7b (2026-10-29): a lost START left the entry open; `close.py <id>` found nothing live
        # ("closed — 4 strategies, all CLOSED.") and exited early, before the journal was touched, so
        # the next create reused dpl-req-…-cd383dba.
        root, rid = self._seed_open_request()
        self.rows = [dict(self._CLOSED_ROW, strategyId=f"s{i}") for i in range(4)]
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertIn("closed — 4 strategies, all CLOSED.", out)
        self.assertEqual(_journal.read("omega")["resolved"], "closed-by-close.py")
        self.assertNotEqual(self._next_create_request_id(root), rid)

    def test_a_package_with_no_strategies_at_all_closes_the_open_request(self):
        # Only because the job record answers that no deploy is running (the gateway's [NOT_FOUND]).
        _root, _rid = self._seed_open_request()
        self.rows = []
        _out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.status_reads, 1)
        self.assertEqual(_journal.read("omega")["resolved"], "closed-by-close.py")

    def _running_job(self, rid=None, pkg="omega"):
        return _gw("ok", payload={"state": {"status": "running"},
                                  "meta": {"deployId": "dpl-1", "packageId": pkg, "requestId": rid}})

    def test_review_4473504980_a_lost_starts_running_job_keeps_the_request_open(self):
        # START lost, recovery found nothing, the job is still before wallet creation: no strategy
        # yet. Closing the entry here would let the next create mint a fresh id and START again.
        _root, rid = self._seed_open_request()
        for rows in ([], [self._CLOSED_ROW]):          # the empty and the all-terminal branch
            self.rows = rows
            self.job_status = self._running_job(rid)
            out, code = self._run("omega")
            self.assertEqual(code, 2)
            self.assertIsNone(_journal.read("omega")["resolved"])
            self.assertIn("NOT torn down", out)
            self.assertIn(rid, out)
            self.assertIn("deploy.py status", out)
            self.assertNotIn("nothing to close", out)
            self.assertNotIn("closed —", out)

    def test_review_4482329029_a_triggered_close_over_a_running_job_keeps_the_request_open(self):
        # A lost START is still running (e.g. creating the next instance's wallet) while one wallet is
        # already ACTIVE. The triggered close must not resolve the entry: the poll after it finds
        # nothing live, and only the open entry makes it refuse instead of reporting `closed`.
        _root, rid = self._seed_open_request()
        self.rows = [self._ACTIVE_ROW]
        self.job_status = self._running_job(None, pkg="omega")
        out, code = self._run("omega")
        self.assertEqual(code, 2)
        self.assertEqual(self.closed, ["s1"])                  # the close itself was still triggered
        self.assertIsNone(_journal.read("omega")["resolved"])
        self.assertIn("NOT torn down", out)
        self.assertIn(rid, out)
        self.assertIn("deploy.py status", out)
        # Poll 2: the close settled, the job still runs → refuse, do not report closed.
        self.rows = [self._CLOSED_ROW]
        out, code = self._run("omega")
        self.assertEqual(code, 2)
        self.assertNotIn("closed —", out)
        self.assertIsNone(_journal.read("omega")["resolved"])
        # Poll 3: the job finished → closed, and the entry closes.
        self.job_status = _gw("ok", payload={"state": {"status": "done"},
                                             "meta": {"deployId": "dpl-1", "packageId": "omega"}})
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertIn("closed — 1 strategy, all CLOSED.", out)
        self.assertEqual(_journal.read("omega")["resolved"], "closed-by-close.py")

    def test_an_unreadable_job_record_keeps_the_request_open(self):
        _root, _rid = self._seed_open_request()
        self.rows = []
        for answer in (_gw("timeout", message="x"), _gw("unknown_method", message="unknown method"),
                       _gw("ok", payload={"ok": False})):
            self.job_status = answer
            _out, code = self._run("omega")
            self.assertEqual(code, 2)
            self.assertIsNone(_journal.read("omega")["resolved"])

    def test_a_start_younger_than_its_wall_clock_keeps_the_request_open_without_a_read(self):
        _root, rid = self._seed_open_request()
        entry = _journal.read("omega")
        entry["sentAt"] = _journal.utc_stamp(time.time())
        _journal.write("omega", entry)
        self.rows = []
        out, code = self._run("omega")
        self.assertEqual(code, 2)
        self.assertEqual(self.status_reads, 0)
        self.assertIsNone(_journal.read("omega")["resolved"])
        self.assertIn("may not show yet", out)

    def test_a_finished_job_closes_the_request(self):
        _root, rid = self._seed_open_request()
        self.rows = [self._CLOSED_ROW]
        self.job_status = _gw("ok", payload={"state": {"status": "done"},
                                             "meta": {"deployId": "dpl-1", "packageId": "omega",
                                                      "requestId": rid}})
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertIn("closed — 1 strategy, all CLOSED.", out)
        self.assertEqual(_journal.read("omega")["resolved"], "closed-by-close.py")

    def test_no_open_request_reads_no_job_record(self):
        _root, rid = self._seed_open_request()
        _journal.resolve("omega", rid, "dpl-1")          # an answered START: nothing open
        self.rows = []
        _out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.status_reads, 0)

    def test_a_close_that_leaves_something_live_or_refuses_keeps_the_request_open(self):
        # strategy_close submit fails → the strategy stays live (exit 2, "failed").
        _root, _rid = self._seed_open_request()
        self.rows = [self._ACTIVE_ROW]

        class _FailingMCP:
            def mcp_call(self, name, timeout=None, **kw):
                raise close.MCPError("backend down")
        close.MCPClient = _FailingMCP          # tearDown restores MCPClient and the _cli stubs
        _out, code = self._run("omega")
        self.assertEqual(code, 2)
        self.assertIsNone(_journal.read("omega")["resolved"])
        # An unreadable runtime inventory refuses: nothing closed, the request stays open.
        _cli.list_runtimes_or_none = lambda *a, **k: None
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            close.main(["close.py", "omega"])
        self.assertIsNone(_journal.read("omega")["resolved"])

    def test_closing_one_instance_keeps_the_request_open(self):
        _root, _rid = self._seed_open_request()
        self.rows = [self._ACTIVE_ROW]
        # `--instance` maps through the inventory read: the instance's runtime is on the open wallet,
        # so the instance close really triggers (not the "can't map" refusal) and the guard in
        # `_close_open_request` is what keeps the package's START request open.
        status_read = _cli.gateway_call
        _cli.gateway_call = lambda method, *a, **k: (
            {"kind": "ok", "payload": {}, "message": "", "rc": 0, "elapsed": 0}
            if method == "senpi.deleteRuntime" else status_read(method, *a, **k))
        gone = iter([[{"name": "omega-main", "wallet": "0xabc"}], []])   # listed, then gone
        _cli.list_runtimes_or_none = lambda *a, **k: next(gone, [])
        out, code = self._run("omega", "--instance", "main")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.closed, ["s1"])                  # the instance close went out
        self.assertIsNone(_journal.read("omega")["resolved"])

    def test_poll_after_a_completed_close_reports_closed(self):
        """THE defect: this is the terminal poll and it must say `closed`, not the all-clear."""
        self.rows = [self._CLOSED_ROW]
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertIn("closed", out.lower())
        self.assertNotIn("no OPEN strategies to close", out)

    def test_a_package_never_deployed_here_does_not_claim_closed(self):
        """The other half of the ambiguity — absence must not render as a completed close."""
        self.rows = []
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertNotIn("closed.", out)
        self.assertIn("no strategies", out.lower())

    def test_the_two_outcomes_do_not_print_the_same_thing(self):
        """Whatever the wording, a caller must be able to tell them apart — that is the whole bug."""
        self.rows = [self._CLOSED_ROW]
        closed_out, _ = self._run("omega")
        self.rows = []
        never_out, _ = self._run("omega")
        self.assertNotEqual(closed_out.strip(), never_out.strip())

    def test_a_live_strategy_still_closes_normally(self):
        """Regression guard: the fix must not touch the path that actually has work to do."""
        self.rows = [self._ACTIVE_ROW]
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.closed, ["s1"])
        self.assertIn("closing", out.lower())

    def test_an_unreadable_second_read_refuses_rather_than_claiming_closed(self):
        """Money-path guard, same rule as `_read_or_refuse`: "couldn't read it" must never render as a
        finished close, or the agent redeploys over a strategy that may still be live and funded."""
        self.rows = [self._CLOSED_ROW]
        self.unreadable = True
        out, code = self._run("omega")
        self.assertNotEqual(code, 0)
        self.assertNotIn("closed.", out)

    def test_a_never_closed_dead_package_names_its_statuses(self):
        """Rows exist, none LIVE — but FAILED/TERMINATED means no close ever ran here.

        Printing a bare `closed.` would read as "the close you polled for just completed" to an
        agent that never issued one. Naming the terminal statuses keeps the two distinguishable.
        """
        self.rows = [{"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "FAILED"},
                     {"strategyId": "s2", "strategyWalletAddress": "0xdef", "status": "TERMINATED"}]
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertIn("FAILED", out)
        self.assertIn("TERMINATED", out)
        self.assertNotIn("no strategies", out.lower())   # they DO exist — not the absent case

    def test_a_settled_close_says_so_without_inventing_a_status(self):
        self.rows = [self._CLOSED_ROW]
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertIn("CLOSED", out)
        self.assertNotIn("FAILED", out)

    def test_dry_run_still_touches_no_mcp(self):
        self.rows = [self._CLOSED_ROW]
        self._run("omega", "--dry-run")
        self.assertEqual(self.closed, [])


class StrandedRuntimeSweep(unittest.TestCase):
    """A strategy closed outside close.py (the app, a raw strategy_close) is terminal, so the LIVE read
    misses it, but its runtime keeps ticking against the emptied wallet. Seen 2026-10-21: a strategy
    closed from the app kept its scanner emitting OPEN_POSITION against a $0 wallet until the runtime
    was deleted by hand. close.py now stops that runtime and never re-sends strategy_close."""

    setUp = MainPackagePollReportsClosed.setUp
    tearDown = MainPackagePollReportsClosed.tearDown
    _run = MainPackagePollReportsClosed._run
    _CLOSED_ROW = MainPackagePollReportsClosed._CLOSED_ROW

    def _box(self, runtimes, every):
        self.deletes = []
        self.inventory = list(runtimes)
        outer = self

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            if method == "senpi.deleteRuntime":
                outer.deletes.append(params["id"])
                outer.inventory = [r for r in outer.inventory if r["name"] != params["id"]]
                return _gw("ok", payload={"deleted": True})
            if method == "senpi.deploy.status":
                return outer.job_status
            raise AssertionError(f"unexpected gateway call {method!r}")
        _cli.gateway_call = _gateway
        _cli.list_runtimes_or_none = lambda *a, **k: list(outer.inventory)
        orig = _cli.list_strategies_or_none
        self.addCleanup(setattr, _cli, "list_strategies_or_none", orig)
        _cli.list_strategies_or_none = lambda mcp, timeout=15, statuses=None, why=None: list(every)

    def test_close_one_sweep_stops_the_runtime_of_a_closed_strategy_and_sends_nothing(self):
        self._box([{"name": "omega-main", "wallet": "0xabc"}], [])
        rec = close.close_one("omega[1]", self._CLOSED_ROW, [{"name": "omega-main", "wallet": "0xabc"}],
                              False, lambda m: None, sweep=True)
        self.assertEqual((rec["status"], rec["runtime"]), ("closed", "stopped"))
        self.assertEqual(self.deletes, ["omega-main"])
        self.assertEqual(self.closed, [])

    def test_package_poll_deletes_the_runtime_left_by_an_app_close(self):
        self.rows = [dict(self._CLOSED_ROW, skillName="omega")]
        self._box([{"name": "omega-main", "wallet": "0xabc"}], self.rows)
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.deletes, ["omega-main"])
        self.assertEqual(self.closed, [])                     # strategy_close is never re-sent
        self.assertIn("omega v1.0.0: closed", out)

    def test_a_wallet_an_open_strategy_uses_keeps_its_runtime(self):
        """The runtime on a wallet that an open strategy now uses may belong to the live one."""
        self.rows = [dict(self._CLOSED_ROW, skillName="omega")]
        live = {"strategyId": "s9", "strategyWalletAddress": "0xABC", "status": "ACTIVE", "skillName": "other"}
        self._box([{"name": "other-main", "wallet": "0xabc"}], self.rows + [live])
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.deletes, [])
        self.assertEqual(self.closed, [])

    def test_all_with_nothing_live_sweeps_closed_strategies_only(self):
        every = [dict(self._CLOSED_ROW, skillName="omega"),
                 {"strategyId": "s2", "strategyWalletAddress": "0xdef", "status": "FAILED", "skillName": "beta"}]
        self._box([{"name": "omega-main", "wallet": "0xabc"}, {"name": "beta-main", "wallet": "0xdef"}], every)
        out, code = self._run("--all")
        self.assertEqual(code, 0)
        self.assertEqual(self.deletes, ["omega-main"])     # FAILED may hold a position its DSL guards
        self.assertEqual(self.closed, [])

    def test_no_runtime_left_reads_nothing_more(self):
        self.rows = [dict(self._CLOSED_ROW, skillName="omega")]
        self._box([], self.rows)
        _cli.list_strategies_or_none = lambda *a, **k: self.fail("no inventory, no extra read")
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.deletes, [])
        self.assertIn("closed", out)

    def test_unreadable_inventory_reports_closed_with_runtimes_not_checked(self):
        """The strategy is closed whatever the inventory read says: no 'NOTHING WAS CLOSED' refusal,
        no delete, and exit 2 so the poll re-runs until the runtimes were checked."""
        self.rows = [dict(self._CLOSED_ROW, skillName="omega")]
        self._box([], self.rows)
        _cli.list_runtimes_or_none = lambda *a, **k: None
        for argv in ("omega", "--all"):
            out, code = self._run(argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("Runtimes not checked", out)
            self.assertNotIn("NOTHING WAS CLOSED", out)
            self.assertEqual(self.deletes, [])
        self.assertIn("omega v1.0.0: closed", self._run("omega")[0])

    def test_a_package_never_deployed_here_reads_no_inventory(self):
        self.rows = []
        self._box([], [])
        _cli.list_runtimes_or_none = lambda *a, **k: self.fail("nothing deployed, no inventory read")
        out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertIn("nothing to close", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
