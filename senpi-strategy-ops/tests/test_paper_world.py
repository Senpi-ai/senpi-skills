#!/usr/bin/env python3
"""`--paper` on deploy.py, close.py and status.py: one world per command.

The fake MCP follows the `strategy_list` contract for simulations: `paper: true` answers simulations
only, with `paper_routing: true` at the head of `data` and `paper: true` on each row. `paper` absent
answers live strategies only. An MCP with no paper router ignores the flag and answers live rows with
no marker, and `PAPER_UNAVAILABLE` comes back as a tool failure.

    python3 -m pytest senpi-strategy-ops/tests/test_paper_world.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli      # noqa: E402
import _journal  # noqa: E402
import close     # noqa: E402
import deploy    # noqa: E402
import status    # noqa: E402
from mcp_client import MCPError  # noqa: E402

LIVE_WALLET = "0x" + "ab" * 20
PAPER_WALLET = "0x0000000000000000" + "1762500000000" + "0a1b2c3d4e5"
PAPER_WALLET_2 = "0x0000000000000000" + "1762500000001" + "0a1b2c3d4e6"
PAPER_ID = "paper-11111111-2222-4333-8444-555555555555"


def _live_row(sid="live-1", wallet=LIVE_WALLET, skill="omega", status_="ACTIVE"):
    return {"id": sid, "strategyWalletAddress": wallet, "status": status_, "strategyName": skill,
            "strategyMetadata": {"skillName": skill}}


def _paper_row(sid=PAPER_ID, wallet=PAPER_WALLET, skill="omega", status_="ACTIVE", marked=True):
    row = {"id": sid, "strategyWalletAddress": wallet, "status": status_, "strategyName": skill,
           "strategyMetadata": {"skillName": skill}}
    if marked:
        row["paper"] = True
    return row


class FakeMCP:
    def __init__(self, live=(), paper=(), router=True, unavailable=False):
        self.live, self.paper = list(live), list(paper)
        self.router, self.unavailable = router, unavailable
        self.lists, self.closed = [], []

    def mcp_call(self, name, timeout=None, **kw):
        if name == "strategy_close":
            self.closed.append(kw)
            return {"success": True, "data": {}}
        assert name == "strategy_list", name
        self.lists.append(dict(kw))
        want = {str(s).upper() for s in kw.get("status") or []}

        def pick(rows):
            return [r for r in rows if not want or str(r.get("status")).upper() in want]
        if kw.get("paper") is True and self.router:
            if self.unavailable:
                raise MCPError("tool failed: PAPER_UNAVAILABLE: paper trading is not available")
            return {"success": True, "data": {"paper_routing": True, "strategies": pick(self.paper)}}
        return {"success": True, "data": {"strategies": pick(self.live)}}


def _gw(kind, payload=None, message=None):
    return {"kind": kind, "payload": payload, "message": message, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


class ReaderOneWorldPerCall(unittest.TestCase):
    def test_a_paper_read_sends_the_flag_and_keeps_only_marked_rows(self):
        mcp = FakeMCP(paper=[_paper_row(), _paper_row(sid="paper-x", wallet=PAPER_WALLET_2, marked=False)])
        rows = _cli.list_strategies_or_none(mcp, statuses=["ACTIVE"], paper=True)
        self.assertEqual([r["id"] for r in rows], [PAPER_ID])
        self.assertEqual(mcp.lists, [{"status": ["ACTIVE"], "paper": True}])

    def test_a_live_read_is_todays_call_and_drops_marked_rows(self):
        mcp = FakeMCP(live=[_live_row(), _paper_row()])
        rows = _cli.list_strategies_or_none(mcp, statuses=["ACTIVE"])
        self.assertEqual([r["id"] for r in rows], ["live-1"])
        self.assertEqual(mcp.lists, [{"status": ["ACTIVE"]}])

    def test_a_paper_reply_without_the_marker_is_no_answer(self):
        why = []
        mcp = FakeMCP(live=[_live_row()], router=False)
        self.assertIsNone(_cli.list_strategies_or_none(mcp, paper=True, why=why))
        self.assertIn("paper_routing: true", why[0])

    def test_a_live_reply_carrying_the_marker_is_no_answer(self):
        class Marked(FakeMCP):
            def mcp_call(self, name, timeout=None, **kw):
                return {"success": True, "data": {"paper_routing": True, "strategies": [_live_row()]}}
        self.assertIsNone(_cli.list_strategies_or_none(Marked()))

    def test_paper_unavailable_is_no_answer(self):
        why = []
        self.assertIsNone(_cli.list_strategies_or_none(FakeMCP(unavailable=True), paper=True, why=why))
        self.assertIn("PAPER_UNAVAILABLE", why[0])


class RuntimeIds(unittest.TestCase):
    def test_live_and_paper_ids(self):
        self.assertEqual(_cli.runtime_id_for("omega", "main", False), "omega-main")
        self.assertEqual(_cli.runtime_id_for("omega", "main", True), "omega-main-paper")

    def test_the_paper_suffix_survives_the_40_character_cut(self):
        rid = _cli.runtime_id_for("a" * 30, "instance", True)
        self.assertEqual(rid, "a" * 30 + "-ins-paper")
        self.assertEqual(len(rid), 40)
        self.assertEqual(_cli.runtime_id_for("a" * 33, "main", True), "a" * 33 + "-paper")

    def test_paper_addresses(self):
        self.assertTrue(_cli.is_paper_address(PAPER_WALLET))
        self.assertTrue(_cli.is_paper_address("  " + PAPER_WALLET.upper().replace("0X", "0x") + " "))
        self.assertFalse(_cli.is_paper_address(LIVE_WALLET))
        self.assertFalse(_cli.is_paper_address(None))

    def test_the_zero_address_is_live(self):
        zero = "0x" + "0" * 40
        for value in (zero, zero.upper().replace("0X", "0x")):
            self.assertFalse(_cli.is_paper_address(value))
            self.assertFalse(_cli.looks_paper(value))
        self.assertFalse(_cli.is_paper_address("0x" + "0" * 29 + "0a1b2c3d4e5"))


class _CloseHarness(unittest.TestCase):
    """Drives `close.main` end to end over a FakeMCP, with the runtime inventory stubbed."""

    def setUp(self):
        self._orig = (close.MCPClient, _cli.list_runtimes, _cli.list_runtimes_or_none, _cli.run_cli,
                      close._pkg.load, _cli.gateway_call)
        self.mcp = FakeMCP()
        self.runtimes = []
        self.deleted = []
        mcp = self.mcp
        close.MCPClient = lambda *a, **k: mcp
        outer = self

        def _inventory(*a, **k):
            return [r for r in outer.runtimes if r["name"] not in outer.deleted]
        _cli.list_runtimes = _inventory
        _cli.list_runtimes_or_none = _inventory
        _cli.run_cli = lambda args, timeout=60: (0, "", "")

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            if method == "senpi.deleteRuntime":
                outer.deleted.append(params["id"])
                return _gw("ok", payload={})
            if method == "senpi.deploy.status":
                return _gw("refused", message="[NOT_FOUND] No deploy has been started on this agent.")
            raise AssertionError(method)
        _cli.gateway_call = _gateway

        class _Inst:
            name = "main"

        class _Pkg:
            id = "omega"
            version = "1.0.0"
            instances = [_Inst()]
            dir = Path("/nonexistent-close-py-paper-test")
        close._pkg.load = lambda _p: _Pkg()

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))

    def tearDown(self):
        (close.MCPClient, _cli.list_runtimes, _cli.list_runtimes_or_none, _cli.run_cli,
         close._pkg.load, _cli.gateway_call) = self._orig

    def _run(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                close.main(["close.py", *args])
        code = ctx.exception.code
        if isinstance(code, str):
            return out.getvalue() + code, 1
        return out.getvalue(), code


class CloseOneWorld(_CloseHarness):
    def _both(self):
        self.mcp.live = [_live_row()]
        self.mcp.paper = [_paper_row()]
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"},
                         {"name": "omega-main-paper", "wallet": PAPER_WALLET, "status": "running"}]

    def test_close_by_name_closes_only_the_live_strategy(self):
        self._both()
        _out, code = self._run("omega")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])
        self.assertEqual(self.deleted, ["omega-main"])
        self.assertTrue(all("paper" not in c for c in self.mcp.lists))

    def test_close_by_name_with_paper_closes_only_the_simulation(self):
        self._both()
        out, code = self._run("omega", "--paper")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": PAPER_ID}])
        self.assertEqual(self.deleted, ["omega-main-paper"])
        self.assertTrue(self.mcp.lists and all(c.get("paper") is True for c in self.mcp.lists))
        self.assertIn("close.py omega --paper", out)

    def test_paper_close_refuses_a_reply_without_the_marker(self):
        self._both()
        self.mcp.router = False
        out, code = self._run("omega", "--paper")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("could not read your simulations", out)
        self.assertIn("Do not re-run without --paper", out)

    def test_paper_close_refuses_on_paper_unavailable(self):
        self._both()
        self.mcp.unavailable = True
        out, code = self._run("omega", "--paper")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertIn("PAPER_UNAVAILABLE", out)

    def test_paper_close_ignores_a_row_without_the_paper_field(self):
        self.mcp.paper = [_paper_row(marked=False)]
        self.runtimes = [{"name": "omega-main-paper", "wallet": PAPER_WALLET, "status": "running"}]
        out, code = self._run("omega", "--paper")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertEqual(self.deleted, [])
        self.assertIn("no strategies for this package", out)

    def test_paper_close_by_instance_finds_the_paper_runtime(self):
        self._both()
        _out, code = self._run("omega", "--instance", "main", "--paper")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": PAPER_ID}])
        self.assertEqual(self.deleted, ["omega-main-paper"])

    def test_close_all_with_paper_closes_only_simulations(self):
        self._both()
        _out, code = self._run("--all", "--paper")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": PAPER_ID}])

    def test_close_all_without_paper_closes_only_live(self):
        self._both()
        _out, code = self._run("--all")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": "live-1"}])

    def test_a_paper_id_without_the_flag_is_not_found_and_points_at_paper(self):
        self._both()
        out, code = self._run("--strategy-id", PAPER_ID)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.mcp.closed, [])
        self.assertIn("--paper", out)

    def test_a_paper_id_with_the_flag_closes_it(self):
        self._both()
        out, code = self._run("--strategy-id", PAPER_ID, "--paper")
        self.assertEqual(code, 0)
        self.assertEqual(self.mcp.closed, [{"strategyId": PAPER_ID}])
        self.assertIn(f"close.py --strategy-id {PAPER_ID} --paper", out)

    def test_a_paper_close_resolves_the_paper_deploy_request_only(self):
        self._both()
        for key, rid in ((_journal.key("omega", False), "dpl-req-20261107T000000Z-aaaaaaaa"),
                         (_journal.key("omega", True), "dpl-req-20261107T000000Z-bbbbbbbb")):
            _journal.write(key, {"packageId": "omega", "requestId": rid, "sentAt": "2026-11-07T00:00:00Z",
                                 "verb": "create", "paramsKey": {}, "resolved": None})
        _out, code = self._run("omega", "--paper")
        self.assertEqual(code, 0)
        self.assertEqual(_journal.read(_journal.key("omega", True))["resolved"], "closed-by-close.py")
        self.assertIsNone(_journal.read(_journal.key("omega", False))["resolved"])


class StatusOneWorld(unittest.TestCase):
    def setUp(self):
        self._orig = (_cli.list_runtimes_or_none, status._openclaw_available, _cli.runtime_health_map)
        self.runtimes = [{"name": "omega-main", "wallet": LIVE_WALLET, "status": "running"},
                         {"name": "omega-main-paper", "wallet": PAPER_WALLET, "status": "running"}]
        _cli.list_runtimes_or_none = lambda *a, **k: list(self.runtimes)
        status._openclaw_available = lambda: True
        _cli.runtime_health_map = lambda: {}

    def tearDown(self):
        _cli.list_runtimes_or_none, status._openclaw_available, _cli.runtime_health_map = self._orig

    def _mcp(self, **kw):
        return FakeMCP(live=[_live_row()], paper=[_paper_row()], **kw)

    def test_status_without_paper_lists_live_only_and_no_paper_orphan(self):
        mcp = self._mcp()
        rows, orphans, _ok = status.build(mcp, deep=False)
        self.assertEqual([r["strategyId"] for r in rows], ["live-1"])
        self.assertEqual(orphans, [])
        self.assertTrue(all("paper" not in c for c in mcp.lists))

    def test_status_with_paper_lists_simulations_only_and_no_live_orphan(self):
        mcp = self._mcp()
        rows, orphans, _ok = status.build(mcp, deep=False, paper=True)
        self.assertEqual([(r["strategyId"], r["runtime"]) for r in rows], [(PAPER_ID, "omega-main-paper")])
        self.assertEqual(orphans, [])
        self.assertTrue(all(c.get("paper") is True for c in mcp.lists))

    def test_status_with_paper_ignores_a_row_without_the_paper_field(self):
        mcp = FakeMCP(paper=[_paper_row(marked=False)])
        rows, _orphans, _ok = status.build(mcp, deep=False, paper=True)
        self.assertEqual(rows, [])

    def test_status_with_paper_refuses_a_reply_without_the_marker(self):
        with self.assertRaises(SystemExit) as ctx:
            status.build(self._mcp(router=False), deep=False, paper=True)
        self.assertIn("could not read your simulations", str(ctx.exception.code))

    def test_status_main_labels_simulations(self):
        orig = status.MCPClient
        status.MCPClient = lambda *a, **k: self._mcp()
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(status.main(["status.py", "--paper", "--fast"]), 0)
        finally:
            status.MCPClient = orig
        out = buf.getvalue()
        self.assertIn("simulation", out)
        self.assertIn(PAPER_ID[:8], out)
        self.assertNotIn("live-1", out)


class StatusDriftPerWorld(unittest.TestCase):
    """A simulation's runtime is `<id>-<inst>-paper`; drift must still find its instance on disk."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "strategy.yaml").write_text("id: omega\n")
        inst = types.SimpleNamespace(name="main", runtime_name="omega-main", runtime_text="x: 1\n",
                                     runtime_doc={"exit": {"dsl_preset": "new"}},
                                     runtime_path=tmp / "main" / "runtime.yaml")
        pkg = types.SimpleNamespace(id="omega", dir=tmp, instances=[inst])
        self._orig = (status._pkg.resolve_pkg_dir, status._pkg.load)
        status._pkg.resolve_pkg_dir = lambda _id: tmp
        status._pkg.load = lambda _id: pkg

    def tearDown(self):
        status._pkg.resolve_pkg_dir, status._pkg.load = self._orig

    def test_a_simulation_running_an_older_recipe_shows_drift(self):
        drift = status._config_drift("omega", "omega-main-paper", {"omega-main-paper": {"dslPreset": "old"}},
                                     paper=True)
        self.assertEqual(drift["diffs"], [{"field": "dsl_preset", "running": "old", "on_disk": "new"}])

    def test_live_drift_is_unchanged(self):
        drift = status._config_drift("omega", "omega-main", {"omega-main": {"dslPreset": "old"}})
        self.assertEqual(drift["diffs"][0]["field"], "dsl_preset")
        self.assertIsNone(status._config_drift("omega", "omega-main-paper",
                                               {"omega-main-paper": {"dslPreset": "old"}}))


class UpdateFollowsTheDriftCommand(unittest.TestCase):
    """The command `status.py --paper` prints for a drifted simulation must reach the update verb,
    aimed at the simulation's runtime."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "main").mkdir()
        inst = types.SimpleNamespace(name="main", runtime_name="omega-main",
                                     runtime_path=self.tmp / "main" / "runtime.yaml")
        self.pkg = types.SimpleNamespace(id="omega", dir=self.tmp, instances=[inst])
        self.calls = []
        self._orig = (status.build, deploy.local_pkg, deploy.full_validate, deploy.outside_durable_root,
                      _cli.run_cli)
        row = {"package": "omega", "is_pkg": True, "name": "omega", "name_source": "strategyName",
               "strategyId": PAPER_ID, "wallet": PAPER_WALLET, "status": "ACTIVE", "funded": "100",
               "positions": None, "paused": None, "reason": None, "runtime": "omega-main-paper",
               "health": "running",
               "config_drift": {"diffs": [{"field": "dsl_preset", "running": "old", "on_disk": "new"}],
                                "dir": str(self.tmp / "main"), "package_dir": str(self.tmp)}}
        status.build = lambda *a, **k: ([row], [], True)
        deploy.local_pkg = lambda arg: self.pkg
        deploy.full_validate = lambda pkg: []
        deploy.outside_durable_root = lambda pkg, cmd, paper=False: None
        _cli.run_cli = lambda argv, timeout=60: (self.calls.append(list(argv)) or (0, "", ""))

    def tearDown(self):
        (status.build, deploy.local_pkg, deploy.full_validate, deploy.outside_durable_root,
         _cli.run_cli) = self._orig

    def test_the_printed_command_updates_the_simulation(self):
        import shlex
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status.main(["status.py", "omega", "--paper"])
        line = next(ln for ln in out.getvalue().splitlines() if "deploy.py update" in ln)
        cmd = shlex.split(line.split("(add --apply")[0].split("python3 ", 1)[1])
        self.assertEqual(cmd[0], "deploy.py")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                deploy.main(cmd)
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(self.calls, [["openclaw", "senpi", "update", str(self.tmp / "main"),
                                       "--id", "omega-main-paper"]])

    def test_an_unknown_runtime_id_is_still_refused(self):
        inst, _sel, refusal = deploy.update_target(self.pkg, "omega-other-paper")
        self.assertIsNone(inst)
        self.assertIn("no instance of this package runs as", refusal)


class DeployPaper(unittest.TestCase):
    def setUp(self):
        self._orig = _cli.gateway_call
        self.calls = []
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.root = Path(tmp)
        old = os.environ.get("SENPI_STRATEGIES_DIR")
        os.environ["SENPI_STRATEGIES_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SENPI_STRATEGIES_DIR", old) if old is not None
                        else os.environ.pop("SENPI_STRATEGIES_DIR", None))
        self.answer = _gw("ok", payload={"deployId": "dpl-1"})

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append((method, dict(params or {})))
            return self.answer
        _cli.gateway_call = _gateway

    def tearDown(self):
        _cli.gateway_call = self._orig

    def _args(self, paper, **kw):
        base = dict(budget=30.0, decision_model=None, tick_wait=None, max_wait=60, json=False,
                    dry_run=False, request_ids=True, cmd="create", paper=paper)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _pkg(self):
        return types.SimpleNamespace(dir=str(self.root / "omega"), id="omega", version="1.0.0", instances=[])

    def _start(self, a):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                return deploy.start_deploy(self._pkg(), a, lambda m: None), err.getvalue(), None
            except SystemExit as e:
                return None, err.getvalue(), e.code

    def test_create_with_paper_calls_start_paper_with_the_same_params(self):
        deploy_id, _err, _code = self._start(self._args(True))
        self.assertEqual(deploy_id, "dpl-1")
        method, params = self.calls[0]
        self.assertEqual(method, "senpi.deploy.startPaper")
        self.assertEqual(params["budget"], 30.0)
        self.assertEqual(params["packageDir"], str(self.root / "omega"))
        self.assertNotIn("paper", params)

    def test_create_without_paper_calls_start(self):
        self._start(self._args(False))
        self.assertEqual(self.calls[0][0], "senpi.deploy.start")

    def test_dry_run_plans_start_paper(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            deploy.start_deploy(self._pkg(), self._args(True, dry_run=True), lambda m: None)
        self.assertIn("senpi.deploy.startPaper", buf.getvalue())
        self.assertEqual(self.calls, [])

    def test_a_runtime_without_start_paper_refuses_and_never_steers_to_live(self):
        self.answer = _gw("unknown_method", message="unknown method: senpi.deploy.startPaper")
        _id, err, code = self._start(self._args(True))
        self.assertEqual(code, deploy.EXIT_INTERNAL)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("no simulation deploy", err)
        self.assertIn("Do not re-run without --paper", err)

    def test_a_paper_start_never_reuses_a_live_open_request(self):
        live_rid = "dpl-req-20261107T000000Z-aaaaaaaa"
        _journal.write(_journal.key("omega", False),
                       {"packageId": "omega", "requestId": live_rid, "sentAt": "2026-11-07T00:00:00Z",
                        "verb": "create", "paramsKey": {"packageDir": str(self.root / "omega"), "budget": 30.0},
                        "resolved": None})
        self._start(self._args(True))
        self.assertNotEqual(self.calls[0][1].get("requestId"), live_rid)
        self.assertIsNone(_journal.read(_journal.key("omega", False))["resolved"])
        self.assertEqual(_journal.read(_journal.key("omega", True))["resolved"], "dpl-1")

    def test_the_outside_root_refusal_keeps_paper_in_its_rerun(self):
        pkg = types.SimpleNamespace(dir="/elsewhere/omega", id="omega")
        self.assertIn("create /", deploy.outside_durable_root(pkg, "create"))
        text = deploy.outside_durable_root(pkg, "create", paper=True)
        self.assertIn(f"create {self.root.resolve() / 'omega'} --paper", text)

    def test_the_unnamed_template_refusal_keeps_paper_in_its_rerun(self):
        orig = deploy._username
        deploy._username = lambda: None
        pkg = types.SimpleNamespace(id="omega", manifest={}, catalog={"tier": "core"})
        a = types.SimpleNamespace(owner=None, name=None, package="omega", cmd="create", paper=True)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                deploy._fork_for_deploy(pkg, a, lambda m: None)
        finally:
            deploy._username = orig
        self.assertIn('--budget <usd> --paper', err.getvalue())

    def _lost_start_reading(self, job_meta, paper):
        fresh = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 1))
        job = {"meta": {"deployId": "dpl-other", "packageId": "omega", "startedAt": fresh, **job_meta},
               "state": {"status": "running"}}

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append((method, dict(params or {})))
            return _gw("timeout") if method.startswith("senpi.deploy.start") else _gw("ok", payload=job)
        _cli.gateway_call = _gateway
        orig_sleep = deploy.time.sleep
        deploy.time.sleep = lambda s: None
        try:
            return self._start(self._args(paper, request_ids=False))[0]
        finally:
            deploy.time.sleep = orig_sleep

    def test_a_lost_paper_start_never_adopts_a_live_job(self):
        self.assertIsNone(self._lost_start_reading({}, paper=True))

    def test_a_lost_live_start_never_adopts_a_paper_job(self):
        self.assertIsNone(self._lost_start_reading({"paper": True}, paper=False))

    def test_a_lost_paper_start_adopts_its_own_world_job(self):
        self.assertEqual(self._lost_start_reading({"paper": True}, paper=True), "dpl-other")

    def test_the_dry_run_json_refusal_keeps_paper_in_its_rerun(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit):
            deploy.main(["deploy.py", "create", "omega", "--budget", "30", "--paper", "--dry-run", "--json"])
        text = err.getvalue()
        self.assertIn("create omega --paper", text)
        self.assertIn("senpi.deploy.startPaper", text)
        self.assertNotIn("senpi.deploy.start ", text)

    def test_the_parser_takes_paper_on_create_and_runtime(self):
        seen = []
        orig = deploy.run_deploy, deploy.ensure_pkg, deploy._fork_for_deploy, deploy.outside_durable_root, \
            deploy.full_validate, deploy.start_request_ids_supported
        pkg = self._pkg()
        deploy.run_deploy = lambda p, a, log: seen.append((a.cmd, a.paper)) or 0
        deploy.ensure_pkg = lambda *a, **k: pkg
        deploy._fork_for_deploy = lambda p, a, log: p
        deploy.outside_durable_root = lambda p, cmd, paper=False: None
        deploy.full_validate = lambda p: []
        deploy.start_request_ids_supported = lambda log: True
        try:
            for argv in (["create", "omega", "--budget", "30", "--paper"], ["runtime", "omega", "--paper"],
                         ["create", "omega", "--budget", "30"]):
                with self.assertRaises(SystemExit):
                    deploy.main(["deploy.py", *argv])
        finally:
            (deploy.run_deploy, deploy.ensure_pkg, deploy._fork_for_deploy, deploy.outside_durable_root,
             deploy.full_validate, deploy.start_request_ids_supported) = orig
        self.assertEqual(seen, [("create", True), ("runtime", True), ("create", False)])


class VerifySeesSimulations(unittest.TestCase):
    """`verify` reads live strategies only. When the package has no live wallet but runs as a
    simulation, it must say so and name no live `create`."""

    def setUp(self):
        self._orig = (deploy.verify_reads, deploy.deploy_job_facts, deploy.MCPClient)
        deploy.verify_reads = lambda pkg: ([], [], {})
        deploy.deploy_job_facts = lambda pkg_id: ([], False)
        self.mcp = FakeMCP(paper=[_paper_row()])
        deploy.MCPClient = lambda *a, **k: self.mcp

    def tearDown(self):
        deploy.verify_reads, deploy.deploy_job_facts, deploy.MCPClient = self._orig

    def _verify(self):
        inst = types.SimpleNamespace(name="main", runtime_name="omega-main")
        pkg = types.SimpleNamespace(id="omega", instances=[inst])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = deploy.cmd_verify(pkg, types.SimpleNamespace(json=False))
        return code, err.getvalue()

    def test_a_simulation_only_package_names_no_live_create(self):
        code, err = self._verify()
        self.assertEqual(code, deploy.VERIFY_NOT)
        self.assertNotIn("create omega", err)
        self.assertIn("simulation", err)
        self.assertIn("status.py omega --paper", err)
        self.assertEqual([c.get("paper") for c in self.mcp.lists], [True])

    def test_no_simulation_keeps_the_create_step(self):
        self.mcp.paper = []
        _code, err = self._verify()
        self.assertIn("create omega --budget <usd>", err)

    def test_no_paper_router_keeps_todays_step(self):
        self.mcp.router = False
        _code, err = self._verify()
        self.assertIn("create omega --budget <usd>", err)

    def test_paper_unavailable_keeps_todays_step(self):
        self.mcp.unavailable = True
        _code, err = self._verify()
        self.assertIn("create omega --budget <usd>", err)

    def test_a_timed_out_simulation_lookup_names_no_live_create(self):
        class Timeout(FakeMCP):
            def mcp_call(self, name, timeout=None, **kw):
                if kw.get("paper") is True:
                    raise TimeoutError("timed out")
                return super().mcp_call(name, timeout=timeout, **kw)
        self.mcp = Timeout(paper=[_paper_row()])
        _code, err = self._verify()
        self.assertNotIn("create omega", err)
        self.assertIn("could not be read", err)
        self.assertIn("status.py omega --paper", err)

    def test_a_simulation_reply_with_no_list_names_no_live_create(self):
        class Drift(FakeMCP):
            def mcp_call(self, name, timeout=None, **kw):
                if kw.get("paper") is True:
                    return {"success": True, "data": {"paper_routing": True, "oops": []}}
                return super().mcp_call(name, timeout=timeout, **kw)
        self.mcp = Drift()
        _code, err = self._verify()
        self.assertNotIn("create omega", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
