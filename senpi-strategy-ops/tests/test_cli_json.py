#!/usr/bin/env python3
"""cli_json stream handling.

`openclaw senpi state --json` exits 0 but writes its JSON payload to STDERR while
stdout carries only banner/log noise. Reading stdout alone returned None for a read
that had SUCCEEDED — permanently hiding the rich per-scanner row (runCount /
lastAliveAt / lastError / consecutiveErrorCount) that only `state` carries.

Run:
    python3 senpi-strategy-ops/tests/test_cli_json.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli  # noqa: E402

# abridged from a live lion deploy (M408027): stdout = banner noise, stderr = the payload
NOISE = "[openclaw] connecting to gateway…\n[openclaw] senpi state -r lion-main\n"
PAYLOAD = ('{"states":[{"components":{"scanners":{"state":{"scanners":['
           '{"scannerId":"lion_scan","runCount":3,"health":"healthy"}]}}}}]}')


class CliJsonStreams(unittest.TestCase):
    def _run(self, rc, out, err):
        orig = _cli.run_cli
        _cli.run_cli = lambda *_a, **_k: (rc, out, err)
        try:
            return _cli.cli_json(["openclaw", "senpi", "state", "--json"])
        finally:
            _cli.run_cli = orig

    def test_payload_on_stdout(self):
        self.assertIsNotNone(self._run(0, PAYLOAD, ""))

    def test_payload_on_stderr_with_noisy_stdout(self):
        """The real shape — this returned None before the fix."""
        got = self._run(0, NOISE, PAYLOAD)
        self.assertIsNotNone(got, "JSON on stderr must be parsed when stdout has no JSON")
        scanners = got["states"][0]["components"]["scanners"]["state"]["scanners"]
        self.assertEqual(scanners[0]["scannerId"], "lion_scan")

    def test_payload_on_stderr_with_empty_stdout(self):
        self.assertIsNotNone(self._run(0, "", PAYLOAD))

    def test_stdout_wins_when_both_carry_json(self):
        got = self._run(0, PAYLOAD, '{"states":[{"decoy":true}]}')
        self.assertNotIn("decoy", got["states"][0])

    def test_nonzero_exit_is_none(self):
        """A genuine throw stays unreadable — the verdict then fails OPEN, by design."""
        self.assertIsNone(self._run(1, "", PAYLOAD))

    def test_no_json_anywhere_is_none(self):
        self.assertIsNone(self._run(0, NOISE, "Error: getSystemState threw"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
