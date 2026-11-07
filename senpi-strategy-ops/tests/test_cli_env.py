#!/usr/bin/env python3
"""The child env every `openclaw` call gets (`_cli._cli_env`, via `_cli._spawn`).

`SENPI_OPENCLAW_CLI_SKIP_PLUGIN_CAPTURE=1` is what the senpi-agent #102 dist patch reads to let an
`openclaw senpi …` call skip the 10.x plugin generation capture (validate 24.7 s → 6.6 s on Box A).
It must reach the child on every path (`run_cli`, `cli_json`, `gateway_call`), sit beside the
existing `OPENCLAW_NO_RESPAWN=1` / `SENPI_LOG_LEVEL=error`, and never leak into this process's own
`os.environ`.

Run:  python3 senpi-strategy-ops/tests/test_cli_env.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import _cli  # noqa: E402

SKIP = "SENPI_OPENCLAW_CLI_SKIP_PLUGIN_CAPTURE"


class _FakePopen:
    """Records the Popen kwargs and answers at once, rc 0."""
    calls = []

    def __init__(self, args, **kw):
        _FakePopen.calls.append((list(args), kw))
        self.returncode = 0
        self.pid = 0

    def communicate(self, timeout=None):
        return '{"ok": true}', ""


class ChildEnv(unittest.TestCase):
    def setUp(self):
        _FakePopen.calls = []
        p = mock.patch.object(_cli.subprocess, "Popen", _FakePopen)
        p.start()
        self.addCleanup(p.stop)

    def _env_of_only_call(self):
        self.assertEqual(len(_FakePopen.calls), 1)
        return _FakePopen.calls[0][1]["env"]

    def _assert_child_env(self, env):
        self.assertEqual(env.get(SKIP), "1")
        self.assertEqual(env.get("OPENCLAW_NO_RESPAWN"), "1")
        self.assertEqual(env.get("SENPI_LOG_LEVEL"), "error")

    def test_run_cli_sets_the_skip_var_beside_no_respawn(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(SKIP, None)
            before = dict(os.environ)
            rc, _o, _e = _cli.run_cli(["openclaw", "senpi", "validate", "/x", "--stage", "live"])
            self.assertEqual(rc, 0)
            env = self._env_of_only_call()
            self._assert_child_env(env)
            # merged onto the caller's env, not a replacement of it
            for k, v in before.items():
                if k not in (SKIP, "OPENCLAW_NO_RESPAWN", "SENPI_LOG_LEVEL"):
                    self.assertEqual(env.get(k), v, k)
            # and this process's own env is untouched
            self.assertEqual(dict(os.environ), before)
            self.assertNotIn(SKIP, os.environ)
            self.assertIsNot(env, os.environ)

    def test_cli_json_and_gateway_call_get_it_too(self):
        _cli.cli_json(["openclaw", "senpi", "status", "-r", "rt", "--json"], 10)
        self._assert_child_env(self._env_of_only_call())
        _FakePopen.calls = []
        _cli.gateway_call("senpi.listRuntimes", {})
        # inert on a built-in call (it loads no plugins) — set only because the env is shared
        self._assert_child_env(self._env_of_only_call())

    def test_a_caller_value_is_overridden_to_exactly_1(self):
        # The #102 gate is `=== "1"`; a stray "0"/"true" in the agent's env must not disable the skip.
        with mock.patch.dict(os.environ, {SKIP: "0"}):
            _cli.run_cli(["openclaw", "senpi", "status"])
            self.assertEqual(self._env_of_only_call()[SKIP], "1")
            self.assertEqual(os.environ[SKIP], "0")


class RealChild(unittest.TestCase):
    def test_a_real_child_sees_the_var(self):
        before = dict(os.environ)
        rc, out, _e = _cli.run_cli([sys.executable, "-c",
                                    "import os;print(os.environ.get('SENPI_OPENCLAW_CLI_SKIP_PLUGIN_CAPTURE'),"
                                    "os.environ.get('OPENCLAW_NO_RESPAWN'))"], timeout=10)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "1 1")
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
