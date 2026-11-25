#!/usr/bin/env python3
"""`deploy.py create --simulation --variant-of <id> [--batch <id>]`: a variation joins its parent's group.

    python3 -m pytest senpi-strategy-ops/tests/test_deploy_variations.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli      # noqa: E402
import deploy    # noqa: E402

PARENT = "simulation-11111111-2222-4333-8444-555555555555"


def _gw(kind, payload=None):
    return {"kind": kind, "payload": payload, "message": None, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


class Variations(unittest.TestCase):
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
        self.answer = _gw("timeout")

        def _gateway(method, params=None, rpc_timeout_ms=None, timeout_s=None):
            self.calls.append((method, dict(params or {})))
            return self.answer
        _cli.gateway_call = _gateway

    def tearDown(self):
        _cli.gateway_call = self._orig

    def _args(self, **kw):
        base = dict(budget=30.0, decision_model=None, tick_wait=None, max_wait=60, json=False,
                    dry_run=False, request_ids=True, cmd="create", simulation=True)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _pkg(self):
        return types.SimpleNamespace(dir=str(self.root / "omega"), id="omega", version="1.0.0", instances=[])

    def _start(self, a):
        self.answer = _gw("ok", payload={"deployId": "dpl-1"})
        with contextlib.redirect_stderr(io.StringIO()):
            return deploy.start_deploy(self._pkg(), a, lambda m: None)

    def _main(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            try:
                deploy.main(["deploy.py", *argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, err.getvalue()

    def _refused(self, *flags, mentions=()):
        code, err = self._main("create", "omega", "--budget", "30", *flags)
        self.assertEqual(code, deploy.EXIT_CODES["refused"], flags)
        self.assertIn("[INVALID_REQUEST]", err)
        self.assertIn("Nothing was started", err)
        for m in mentions:
            self.assertIn(m, err)
        self.assertEqual(self.calls, [], flags)

    def test_the_simulation_start_carries_parent_and_batch(self):
        self._start(self._args(variant_of=PARENT, batch="penguin-1"))
        method, params = self.calls[0]
        self.assertEqual(method, "senpi.deploy.startSimulation")
        self.assertEqual(params["parentId"], PARENT)
        self.assertEqual(params["batchId"], "penguin-1")

    def test_a_variant_without_a_batch_sends_no_batch(self):
        self._start(self._args(variant_of=PARENT))
        self.assertEqual(self.calls[0][1]["parentId"], PARENT)
        self.assertNotIn("batchId", self.calls[0][1])

    def test_a_start_without_them_sends_neither(self):
        self._start(self._args())
        self.assertNotIn("parentId", self.calls[0][1])
        self.assertNotIn("batchId", self.calls[0][1])

    def test_the_dry_run_plans_them(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            deploy.start_deploy(self._pkg(), self._args(variant_of=PARENT, batch="b-1", dry_run=True),
                                lambda m: None)
        self.assertIn("parentId", buf.getvalue())
        self.assertIn(PARENT, buf.getvalue())
        self.assertIn("b-1", buf.getvalue())
        self.assertEqual(self.calls, [])

    def test_variant_of_without_simulation_is_refused(self):
        self._refused("--variant-of", PARENT, mentions=("--variant-of", "--simulation"))

    def test_variant_of_with_from_simulation_is_refused(self):
        self._refused("--simulation", "--variant-of", PARENT, "--from-simulation", PARENT,
                      mentions=("--variant-of", "--from-simulation"))

    def test_a_malformed_parent_is_refused(self):
        for bad in ("simulation-abc", "11111111-2222-4333-8444-555555555555", "omega", PARENT + "x"):
            self._refused("--simulation", "--variant-of", bad, mentions=("--variant-of",))

    def test_batch_without_variant_of_is_refused(self):
        self._refused("--simulation", "--batch", "b-1", mentions=("--batch", "--variant-of"))

    def test_a_bad_batch_is_refused(self):
        for bad in ("", "x" * 101, "has space", "semi;colon", "slash/y"):
            self._refused("--simulation", "--variant-of", PARENT, "--batch", bad, mentions=("--batch",))

    def test_a_batch_of_100_valid_chars_passes_validation(self):
        code, err = self._main("create", "omega", "--budget", "30", "--simulation", "--variant-of", PARENT,
                               "--batch", "a" * 100, "--dry-run")
        self.assertNotIn("[INVALID_REQUEST] --batch", err)

    def test_the_resume_hint_keeps_the_flags(self):
        a = self._args(variant_of=PARENT, batch="b-1", from_simulation=None)
        sfx = deploy._rerun_sfx(a)
        self.assertIn(f"--variant-of {PARENT}", sfx)
        self.assertIn("--batch b-1", sfx)
        self.assertIn("--simulation", sfx)

    def test_the_durable_root_refusal_keeps_the_flags(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, True)
        pkg = types.SimpleNamespace(dir=str(outside / "omega"), id="omega")
        msg = deploy.outside_durable_root(pkg, "create", simulation=True, variant_of=PARENT, batch="b-1")
        self.assertIn(f"--variant-of {PARENT}", msg)
        self.assertIn("--batch b-1", msg)

    def test_runtime_does_not_take_them(self):
        code, _err = self._main("runtime", "omega", "--variant-of", PARENT)
        self.assertEqual(code, 2)
        self.assertEqual(self.calls, [])

    def test_a_changed_parent_or_batch_is_a_changed_request(self):
        base = {"packageDir": "/p", "budget": 30.0, "parentId": PARENT, "batchId": "a"}
        self.assertNotEqual(deploy._params_key(base), deploy._params_key({**base, "batchId": "b"}))
        self.assertNotEqual(deploy._params_key(base),
                            deploy._params_key({**base, "parentId": "simulation-99999999-2222-4333-8444-555555555555"}))


class AFailedVariationIsTheUsersCall(unittest.TestCase):
    def test_the_skill_asks_before_any_standalone_fallback(self):
        text = (SCRIPTS.parent / "SKILL.md").read_text()
        self.assertIn("A variation refused or failed = stop there", text)
        self.assertIn("Never deploy them without `--variant-of` unasked", text)
        self.assertIn("run them as standalone simulations", text)
        self.assertIn("retry, or stop?", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
