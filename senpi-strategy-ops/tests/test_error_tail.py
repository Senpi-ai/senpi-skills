#!/usr/bin/env python3
"""Hermetic unit tests for _cli.error_tail — the noise-filtered error-tail extractor.

No MCP, no openclaw, no network. Run:
    python3 senpi-strategy-ops/tests/test_error_tail.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli  # noqa: E402


class ErrorTail(unittest.TestCase):
    def test_real_error_survives_banner_flood(self):
        # the M407593 shape: dozens of banner lines, real error on the last line —
        # a head-truncating capture returns only banners
        err = "\n".join(["[plugins] [senpi-runtime] plugin registered …"] * 60) \
              + "\nError: exit block invalid: retrace_threshold must be > 0"
        tail = _cli.error_tail(err)
        self.assertIn("retrace_threshold must be > 0", tail)

    def test_plugin_banner_lines_dropped(self):
        err = "[plugins] loading senpi-runtime\nError: boom"
        self.assertEqual(_cli.error_tail(err), "Error: boom")

    def test_blank_lines_dropped(self):
        self.assertEqual(_cli.error_tail("\n\nError: boom\n\n"), "Error: boom")

    def test_falls_back_to_stdout_when_stderr_empty(self):
        self.assertIn("stdout says why", _cli.error_tail("", "Error: stdout says why"))

    def test_keeps_last_limit_chars_of_a_long_error(self):
        err = "x" * 1000 + " FINAL CAUSE"
        tail = _cli.error_tail(err, limit=100)
        self.assertLessEqual(len(tail), 100)
        self.assertTrue(tail.endswith("FINAL CAUSE"))

    def test_all_noise_falls_back_to_raw_tail(self):
        # filtering must never turn a non-empty capture into an empty message
        err = "[plugins] only banners here"
        self.assertEqual(_cli.error_tail(err), "[plugins] only banners here")

    def test_empty_everything_returns_empty(self):
        self.assertEqual(_cli.error_tail("", ""), "")

    def test_ansi_escapes_stripped_from_cause(self):
        # a colorized error must not ship raw \x1b[…m sequences into the state file / report
        tail = _cli.error_tail("\x1b[31mError: boom\x1b[0m")
        self.assertEqual(tail, "Error: boom")
        self.assertNotIn("\x1b", tail)

    def test_ansi_colored_banner_is_still_dropped(self):
        # a color-coded [plugins] banner evaded the plain startswith filter before ANSI stripping
        err = "\x1b[90m[plugins] loading senpi-runtime\x1b[0m\nError: real cause here"
        tail = _cli.error_tail(err)
        self.assertIn("Error: real cause here", tail)
        self.assertNotIn("[plugins]", tail)
        self.assertNotIn("\x1b", tail)

    def test_stderr_all_noise_falls_back_to_stdout_cause(self):
        # Node CLI prints banners to stderr and the real error to stdout — surface the stdout cause,
        # not the stderr banner (the old code committed to stderr the moment it was non-empty)
        err = "[plugins] banner one\n[plugins] banner two"
        out = "[plugins] boot line\nError: the real cause is on stdout"
        tail = _cli.error_tail(err, out)
        self.assertIn("the real cause is on stdout", tail)
        self.assertNotIn("banner", tail)


if __name__ == "__main__":
    unittest.main()
