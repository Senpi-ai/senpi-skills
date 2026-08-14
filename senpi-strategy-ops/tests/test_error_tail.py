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
        # the banner-flood shape: dozens of banner lines, real error on the last line —
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

    def test_long_error_keeps_head_and_tail_with_loud_omission(self):
        # over-limit CLEANED text: keep both ends — the head opens the message, the tail
        # carries the final cause — and say out loud that the middle was cut
        err = "HEAD-CODE-LINE " + "x" * 1000 + " FINAL CAUSE"
        tail = _cli.error_tail(err, limit=100)
        self.assertTrue(tail.startswith("HEAD-CODE-LINE"))
        self.assertTrue(tail.endswith("FINAL CAUSE"))
        self.assertIn("omitted", tail)

    def test_long_refusal_keeps_the_code_line_agents_branch_on(self):
        # the start-phase decapitation shape: a refusal whose [CODE] line is at the HEAD,
        # followed by one line per offender and a ~430-char Why paragraph — a tail-only cut
        # loses exactly the part an agent branches on
        code_line = "[INVALID_REQUEST] Unsupported `enabled` key on scanners — delete these lines:"
        offenders = "\n".join(f"  - instances/main/runtime.yaml scanners[{i}].enabled" for i in range(10))
        why = "Why: " + "scanner-level enabled is inert in the engine and refused by deploy. " * 6
        err = f"{code_line}\n{offenders}\n{why.strip()}"
        self.assertGreater(len(err), 600)  # the shape only decapitates when over the relay cap
        tail = _cli.error_tail(err)
        self.assertIn("[INVALID_REQUEST]", tail)
        self.assertIn("omitted", tail)
        self.assertTrue(tail.endswith(why.strip()[-40:]))

    def test_raw_fallback_stays_tail_only(self):
        # the raw fallback relays UNFILTERED text, where the head is the banner flood the
        # noise filter failed to catch — keeping a head there would re-open the
        # banner-flood blackout, so it stays a plain last-`limit` cut with no marker
        err = "[plugins] " + "y" * 200  # one huge banner line: filters to nothing, over limit
        tail = _cli.error_tail(err, limit=50)
        self.assertEqual(tail, err[-50:])
        self.assertNotIn("omitted", tail)

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
