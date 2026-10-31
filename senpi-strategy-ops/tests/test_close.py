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
        _cli.list_runtimes_or_none = lambda: []
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closing")   # NOT 'failed' despite rc=1
        self.assertEqual(self.closed, ["s1"])         # money-critical close DID fire
        self.assertEqual(len(self.deletes), 1)        # gone on first try → no retry

    def test_the_delete_is_a_direct_gateway_call_by_id_with_its_own_budget(self):
        _cli.list_runtimes_or_none = lambda: []
        close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        # `{id}` only: the handler ignores `address` when `id` is given. 90 s RPC / 100 s wall —
        # a DSL teardown with open positions makes MCP calls.
        self.assertEqual(self.deletes, [({"id": "pkg-main"}, 90000, 100)])

    def test_still_present_after_retry_fails_without_closing(self):
        # runtime never leaves `runtime list` → genuine failure: report it, do NOT strategy_close
        _cli.list_runtimes_or_none = lambda: [{"name": "pkg-main", "wallet": "0xabc"}]
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(self.closed, [])             # never close while the runtime can re-enter
        self.assertEqual(len(self.deletes), 2)        # one retry before giving up
        self.assertNotIn("No running runtime", rec.get("error", ""))  # clean message, not the raw answer
        self.assertIn("senpi.deleteRuntime", rec["error"])      # names the direct call to retry with
        self.assertIn("strategy_close was NOT sent", rec["error"])

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
                      _cli.list_runtimes_or_none, _cli.run_cli, close._pkg.load)
        self.closed = []
        self.rows = []
        self.unreadable = False
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
         _cli.list_runtimes_or_none, _cli.run_cli, close._pkg.load) = self._orig

    def _run(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                close.main(["close.py", *args])
        return buf.getvalue(), ctx.exception.code

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
        _root, _rid = self._seed_open_request()
        self.rows = []
        self._run("omega")
        self.assertEqual(_journal.read("omega")["resolved"], "closed-by-close.py")

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
        self.addCleanup(setattr, _cli, "find_runtime", _cli.find_runtime)
        _cli.find_runtime = lambda name: {"id": name, "walletAddress": "0xabc"}
        self.addCleanup(setattr, _cli, "runtime_wallet", _cli.runtime_wallet)
        _cli.runtime_wallet = lambda rt: "0xabc"
        self._run("omega", "--instance", "main")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
