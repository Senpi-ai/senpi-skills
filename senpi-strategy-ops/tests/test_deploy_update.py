#!/usr/bin/env python3
"""Hermetic tests for `deploy.py update` — the in-place edit path. No openclaw, no MCP, no network:
`_cli.run_cli` is stubbed and the package is a plain namespace on a temp dir.

What is pinned: the wrapper gates the package BEFORE the verb is called (a structurally broken
package never reaches `senpi update`), resolves the instance dir from `--id`, sends a selector
always, refuses `--apply` with no proof on disk naming the validate command, relays the verb's
exit codes 0/1/2/3 and text verbatim, and — the one that matters most — on a runtime that has no
`update` verb it stops with the edit left on disk and says NOT to close-and-redeploy.

    python3 -m pytest senpi-strategy-ops/tests/test_deploy_update.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli    # noqa: E402
import deploy  # noqa: E402


def _args(**kw):
    base = dict(package="/pkg/spider", runtime_id=None, address=None, apply=False, code_only=False, json=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


class UpdateHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.calls = []
        self.reply = (0, '{"plan": "ok"}\n', "")
        self.gate = []
        (self.tmp / "main").mkdir()
        (self.tmp / "hedge").mkdir()
        self.pkg = types.SimpleNamespace(
            dir=self.tmp, id="spider", version="1.0.0",
            instances=[types.SimpleNamespace(name="main", runtime_name="spider-main",
                                             runtime_path=self.tmp / "main" / "runtime.yaml")])
        self._saved = (deploy.local_pkg, deploy.full_validate, _cli.run_cli)
        deploy.local_pkg = lambda arg: self.pkg
        deploy.full_validate = lambda pkg: list(self.gate)

        def run_cli(args, timeout=60):
            self.calls.append(list(args))
            return self.reply
        _cli.run_cli = run_cli

    def tearDown(self):
        deploy.local_pkg, deploy.full_validate, _cli.run_cli = self._saved

    def run_update(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = deploy.cmd_update(_args(**kw))
        return rc, out.getvalue(), err.getvalue()


class TestUpdate(UpdateHarness):
    def test_plan_points_the_verb_at_the_instance_dir_with_a_selector(self):
        rc, out, err = self.run_update()
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [["openclaw", "senpi", "update", str(self.tmp / "main"), "--id", "spider-main"]])
        self.assertIn('{"plan": "ok"}', out)                      # the verb's stdout, verbatim
        self.assertIn("PLAN only — nothing changed", err)
        self.assertIn("--apply", err)

    def test_structural_errors_refuse_before_the_verb_is_called(self):
        self.gate = ["instance main: set runtime `group: spider` (found None)"]
        rc, _out, err = self.run_update()
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, [])
        self.assertIn("nothing was changed", err)

    def test_apply_without_a_proof_refuses_and_names_the_validate_command(self):
        rc, _out, err = self.run_update(apply=True)
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, [])
        self.assertIn(f"openclaw senpi validate {self.tmp / 'main'}", err)

    def test_apply_with_a_proof_passes_apply_and_code_only_through(self):
        (self.tmp / "main" / deploy.PROOF_FILE).write_text("{}")
        rc, _out, err = self.run_update(apply=True, code_only=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls[0][-3:], ["spider-main", "--apply", "--code-only"])
        self.assertIn("forward-only", err)

    def test_json_is_passed_through_and_no_prose_note_rides_stdout(self):
        rc, out, err = self.run_update(json=True)
        self.assertEqual(rc, 0)
        self.assertIn("--json", self.calls[0])
        self.assertEqual(out.strip(), '{"plan": "ok"}')
        self.assertNotIn("PLAN only", err)

    def test_unknown_id_refuses_and_lists_the_runtime_ids(self):
        rc, _out, err = self.run_update(runtime_id="spider-swing")
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, [])
        self.assertIn("spider-main", err)

    def test_multi_instance_package_needs_an_id(self):
        self.pkg.instances.append(types.SimpleNamespace(
            name="hedge", runtime_name="spider-hedge", runtime_path=self.tmp / "hedge" / "runtime.yaml"))
        rc, _out, err = self.run_update()
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, [])
        self.assertIn("--id", err)
        rc, _out, _err = self.run_update(runtime_id="spider-hedge")
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls[0][3:], [str(self.tmp / "hedge"), "--id", "spider-hedge"])

    def test_the_verbs_refusal_is_relayed_with_its_code(self):
        self.reply = (2, "", "[E_UPDATE_PROOF_MISMATCH] the proof covers other bytes — re-run validate\n")
        rc, _out, err = self.run_update()
        self.assertEqual(rc, 2)
        self.assertIn("[E_UPDATE_PROOF_MISMATCH]", err)

    def test_a_failed_apply_is_exit_1_with_the_message(self):
        (self.tmp / "main" / deploy.PROOF_FILE).write_text("{}")
        self.reply = (1, "", "[E_UPDATE_FAILED] apply failed after registering; restored previous recipe\n")
        rc, _out, err = self.run_update(apply=True)
        self.assertEqual(rc, 1)
        self.assertIn("restored previous recipe", err)

    def test_a_runtime_without_the_verb_stops_and_forbids_the_redeploy_fallback(self):
        # What a pre-verb box really prints: Commander's root action swallows the subcommand.
        self.reply = (1, "", "error: too many arguments for 'senpi'. Expected 0 arguments but got 2.\n")
        rc, _out, err = self.run_update()
        self.assertEqual(rc, 1)
        self.assertIn("no `senpi update` verb", err)
        self.assertIn("NOT applied", err)
        self.assertIn("Do NOT close and redeploy", err)

    def test_a_package_not_on_disk_is_refused_never_fetched(self):
        deploy.local_pkg = lambda arg: None
        rc, _out, err = self.run_update()
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, [])
        self.assertIn("never fetches", err)


if __name__ == "__main__":
    unittest.main()
