#!/usr/bin/env python3
"""Hermetic tests for the status.py health taxonomy (no MCP, no openclaw).

Pins the fail-closed contract: any PRESENT verdict _cli.health_verdict cannot classify as
healthy/broken — the runtime's `unknown` (scanner not yet proven by a tick), `disabled`,
future vocabulary — must map to "unknown", never None: None lets the caller's "running"
fallback paint an unproven runtime ✅. None is only for payloads with no health field. Run:
    python3 senpi-strategy-ops/tests/test_status_health.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli    # noqa: E402
import status  # noqa: E402


class TestHealthVerdict(unittest.TestCase):
    def test_unknown_passes_through(self):
        self.assertEqual(_cli.health_verdict({"health": "unknown"}), "unknown")

    def test_unknown_is_not_coerced_to_none(self):
        # None would trigger status.py's `or "running"` fallback — the fail-open path.
        self.assertIsNotNone(_cli.health_verdict({"overallHealth": "unknown"}))

    def test_known_verdicts(self):
        self.assertEqual(_cli.health_verdict({"health": "healthy"}), "healthy")
        self.assertEqual(_cli.health_verdict({"health": "degraded"}), "degraded")
        self.assertEqual(_cli.health_verdict({"health": "unhealthy"}), "unhealthy")

    def test_disabled_is_not_coerced_to_none(self):
        # `disabled` is real runtime vocabulary (ComponentHealth): every component disabled.
        # None would trigger the `or "running"` fallback — a fully-disabled runtime painted ✅.
        self.assertEqual(_cli.health_verdict({"overallHealth": "disabled"}), "unknown")

    def test_unrecognized_present_verdict_is_unknown(self):
        # Fail-closed against future vocabulary: a verdict we can't classify is unproven, not green.
        self.assertEqual(_cli.health_verdict({"health": "something-new"}), "unknown")

    def test_absent_health_field_is_none(self):
        # None is reserved for payloads with NO health field — the caller's "running" fallback
        # is only correct when the runtime said nothing at all.
        self.assertIsNone(_cli.health_verdict({}))
        self.assertIsNone(_cli.health_verdict({"positions": 3}))

    def test_a_bare_run_state_is_never_healthy(self):
        # A RUN state is not a health verdict. The runtime's own vocabulary (ComponentHealth in
        # senpi-trading-runtime `src/health/types.ts`) is healthy|degraded|unhealthy|disabled|unknown
        # — "running"/"live"/"true" are not in it, and a real `senpi status --json` entry always
        # carries `health`. So an entry whose only signal is a run state is UNPROVEN, not green.
        for entry in ({"name": "spider-main", "status": "running"}, {"status": "live"},
                      {"overall": "live"}, {"status": "true"}):
            self.assertEqual(_cli.health_verdict(entry), "unknown", entry)

    def test_a_run_state_key_can_still_downgrade(self):
        # Fail-closed cuts one way: positive BROKEN evidence is believed wherever it is found.
        self.assertEqual(_cli.health_verdict({"status": "unhealthy"}), "unhealthy")
        self.assertEqual(_cli.health_verdict({"status": "degraded"}), "degraded")

    def test_a_real_health_field_still_reads_healthy(self):
        # The happy path must not go blind: every RuntimeHealthStatus carries `health`.
        self.assertEqual(_cli.health_verdict({"runtimeName": "spider-main", "health": "healthy",
                                              "components": {}}), "healthy")
        self.assertEqual(_cli.health_verdict({"overallHealth": "healthy"}), "healthy")


class TestRuntimeRunning(unittest.TestCase):
    def test_no_entry_scanners_is_running(self):
        # "running — NO ENTRY SCANNERS" is a RUNNING runtime with a wiring failure. Reading it
        # as stopped would send deploy.py's create down the close-and-recreate path on a live
        # runtime (the Bugbot finding on PR #505).
        self.assertTrue(_cli.runtime_running({"status": "running — NO ENTRY SCANNERS"}))
        self.assertTrue(_cli.runtime_running({"status": "running"}))
        self.assertFalse(_cli.runtime_running({"status": "stopped"}))

    def test_no_entry_scanners_predicate(self):
        self.assertTrue(_cli.runtime_no_entry_scanners({"status": "running — NO ENTRY SCANNERS"}))
        self.assertFalse(_cli.runtime_no_entry_scanners({"status": "running"}))
        self.assertFalse(_cli.runtime_no_entry_scanners({}))


class TestStatusBuckets(unittest.TestCase):
    def test_every_health_class_is_rendered(self):
        # Every health class status.py can assign must have an icon — a missing entry
        # renders a blank and drops the row from every summary bucket.
        for cls in ("healthy", "running", "degraded", "unhealthy", "unknown", "no-entry-scanners",
                    "runtime-stopped", "no-runtime", "runtime-unknown", "copy", "manual"):
            self.assertIn(cls, status._ICON, f"no icon for health class {cls!r}")


class UnreadableStrategyListRefuses(unittest.TestCase):
    """`status.py` is where every money decision is checked — the collision row, the ambiguous /
    PAUSED / no-runtime triage and every budget warn send the reader here before they decide
    whether to fund anything. So an unreadable `strategy_list` (transport error, or a payload whose
    shape carries no list) must never render as "No open strategies." + exit 0. A genuinely empty
    list is an answer and still exits 0 — the same line `close.py`'s `_read_or_refuse` draws."""

    CAUSE = "the MCP `strategy_list` call failed (no SENPI_AUTH_TOKEN)"

    def setUp(self):
        self._or_none, self._runtimes = _cli.list_strategies_or_none, _cli.list_runtimes
        self._avail = status._openclaw_available
        status._openclaw_available = lambda: False   # no openclaw here: the runtime read is skipped
        _cli.list_runtimes = lambda *a, **k: []

    def tearDown(self):
        _cli.list_strategies_or_none, _cli.list_runtimes = self._or_none, self._runtimes
        status._openclaw_available = self._avail

    @staticmethod
    def _unreadable(mcp, timeout=15, statuses=None, why=None):
        if why is not None:
            why.append(UnreadableStrategyListRefuses.CAUSE)
        return None

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = status.main(["status.py", *argv])
        return code, out.getvalue()

    def test_a_genuinely_empty_list_is_an_answer_and_exits_zero(self):
        _cli.list_strategies_or_none = lambda *a, **k: []
        code, text = self._run()
        self.assertEqual(code, 0)
        self.assertIn("No open strategies.", text)

    def test_an_unreadable_list_refuses_instead_of_reporting_no_open_strategies(self):
        _cli.list_strategies_or_none = self._unreadable
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            status.main(["status.py"])
        msg = str(ctx.exception)
        self.assertIn("could not read the strategy list", msg)
        self.assertIn("no SENPI_AUTH_TOKEN", msg)          # the cause reaches the operator
        self.assertNotIn("No open strategies", msg)
        self.assertNotIn("No open strategies", out.getvalue())

    def test_the_refusal_is_not_exit_zero(self):
        _cli.list_strategies_or_none = self._unreadable
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            status.main(["status.py"])
        # SystemExit carrying a string exits 1 — the point is only that it is never 0.
        self.assertNotEqual(ctx.exception.code, 0)

    def test_json_mode_refuses_too_rather_than_emitting_an_empty_document(self):
        # `--json` is the machine-read path: `{"strategies": []}` is the same false all-clear,
        # and a parser cannot tell it from a real empty fleet.
        _cli.list_strategies_or_none = self._unreadable
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            status.main(["status.py", "--json"])
        self.assertNotIn("strategies", out.getvalue())

    def test_a_package_filter_does_not_soften_the_refusal(self):
        _cli.list_strategies_or_none = self._unreadable
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            status.main(["status.py", "spider"])
        self.assertIn("could not read the strategy list", str(ctx.exception))

    def test_a_package_filter_reads_the_stamp_case_folded(self):
        # The stamp is written from `pkg.id` VERBATIM and stored case-normalized, so an exact
        # compare renders NO rows for a package whose wallets are live — and this is the read every
        # refusal names first ("`status.py <id>` before you decide anything").
        live = {"id": "sid-1", "status": "ACTIVE", "strategyWalletAddress": "0xabc",
                "totalFunded": 300, "strategyMetadata": {"skillName": "warpath"}}
        _cli.list_strategies_or_none = lambda *a, **k: [live]
        code, text = self._run("Warpath")
        self.assertEqual(code, 0)
        self.assertNotIn("No open strategies", text)
        self.assertIn("0xabc", text)

    def test_a_package_filter_still_excludes_another_package(self):
        live = {"id": "sid-1", "status": "ACTIVE", "strategyWalletAddress": "0xabc",
                "strategyMetadata": {"skillName": "polar"}}
        _cli.list_strategies_or_none = lambda *a, **k: [live]
        code, text = self._run("warpath")
        self.assertEqual(code, 0)
        self.assertNotIn("0xabc", text)


class TriageCommandsAreReadOnly(unittest.TestCase):
    """status.py is where SKILL.md sends an agent to MONITOR, so nothing it prints per row may be a
    command that funds, installs or starts trading. That command is `deploy.py runtime <pkg>` (and
    `create` with a budget) — the resume path. `deploy.py verify` is NOT one: it is the read-only
    check again, so status.py must not describe it as money-moving either."""

    def setUp(self):
        self._build, self._mcp = status.build, status.MCPClient

    def tearDown(self):
        status.build, status.MCPClient = self._build, self._mcp

    @staticmethod
    def _row(health):
        return {"package": "spider", "is_pkg": True, "strategyId": "str-1234abcd",
                "wallet": "0x1234567890abcdef", "status": "ACTIVE", "funded": "$300.00",
                "positions": 0, "runtime": "spider-main", "health": health}

    def _render(self, health):
        status.MCPClient = lambda *a, **k: None
        status.build = lambda *a, **k: ([self._row(health)], [], True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status.main(["status.py"])
        return out.getvalue()

    def test_a_degraded_row_emits_read_only_triage_only(self):
        text = self._render("degraded")
        self.assertNotIn("deploy.py runtime spider", text)  # would install + start trading
        self.assertIn("openclaw senpi scanner -r spider-main", text)

    def test_an_unknown_row_emits_read_only_checks_only(self):
        text = self._render("unknown")
        self.assertNotIn("deploy.py runtime spider", text)
        self.assertIn("openclaw senpi scanner -r spider-main", text)

    def test_the_resume_escape_is_named_once_and_says_it_moves_money(self):
        text = self._render("degraded")
        self.assertIn("deploy.py runtime <id>", text)       # named as the escape, not per row
        self.assertIn("can move money", text)

    def test_verify_is_offered_as_a_read_only_check_never_as_the_money_path(self):
        # The escape and the check are different commands now. A monitoring surface that still calls
        # `verify` the money path teaches the opposite of what the command does.
        text = self._render("degraded")
        self.assertNotIn("verify` — it runs the deploy verb", text)


# At the END of the file, not the middle: `unittest.main()` only runs the classes already defined
# above it, so a mid-file call silently skipped every class declared after it.
if __name__ == "__main__":
    unittest.main()
