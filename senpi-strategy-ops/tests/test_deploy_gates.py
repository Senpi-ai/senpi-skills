#!/usr/bin/env python3
"""Hermetic unit tests for the deploy liveness + budget gates.

No MCP, no openclaw, no network — every input is a plain dict/stub. Run:
    python3 senpi-strategy-ops/tests/test_deploy_gates.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import re
import sys
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _pkg    # noqa: E402
import deploy  # noqa: E402


def _inst(name, share):
    return types.SimpleNamespace(name=name, funding_share=share)


def _dsl_inst(runtime_doc):
    """A bare _pkg.Instance with only runtime_doc set — enough for the exit_block/has_dsl properties."""
    i = _pkg.Instance.__new__(_pkg.Instance)
    i.runtime_doc = runtime_doc
    return i


def _scan_inst():
    return types.SimpleNamespace(external_scanner={"name": "sc1"}, interval_seconds=300)


class PlanFunding(unittest.TestCase):
    def test_fits(self):
        amounts, short = deploy.plan_funding([_inst("a", 0.6), _inst("b", 0.4)], 1000, 1100)
        self.assertEqual(amounts, {"a": 600.0, "b": 400.0})
        self.assertIsNone(short)

    def test_incident_single_wallet_underfunded(self):
        # the M-incident: user asked $1000, only $100 available → HALT, never silently fund the floor
        _amounts, short = deploy.plan_funding([_inst("only", 1.0)], 1000, 100)
        self.assertIsNotNone(short)
        self.assertEqual(short["requested"], 1000.0)
        self.assertGreater(short["short_by"], 800)

    def test_floor_covered_by_balance_is_not_short(self):
        # $100 across 95/5 floors the tiny leg to the $10 wallet floor → $105 total; $200 covers it, no halt
        amounts, short = deploy.plan_funding([_inst("a", 0.95), _inst("b", 0.05)], 100, 200)
        self.assertEqual(amounts, {"a": 95.0, "b": 10.0})
        self.assertIsNone(short)

    def test_floor_over_balance_is_short(self):
        # 2 wallets at the $10 floor need $20 + a fee reserve; $21 available (usable ~$18) can't cover it
        _amounts, short = deploy.plan_funding([_inst("a", 0.5), _inst("b", 0.5)], 20, 21)
        self.assertIsNotNone(short)

    def test_available_unknown_never_halts(self):
        _amounts, short = deploy.plan_funding([_inst("a", 1.0)], 1000, None)
        self.assertIsNone(short)


class UnderfundedNote(unittest.TestCase):
    @staticmethod
    def _short(requested, wallets, available, usable, shares=None):
        return {"requested": float(requested), "wallets": wallets,
                "available": float(available), "usable": float(usable),
                "short_by": float(requested) - float(usable),
                "shares": shares if shares is not None else [1.0 / wallets] * wallets}

    def test_zero_balance_never_suggests_lower_budget(self):
        # the $0-accessible-budget case: $0 accessible → the old note said "--budget ≤ $0"
        note = deploy.underfunded_note(self._short(500, 1, 0, 0))
        self.assertIn("[E_FUNDS_BELOW_FLOOR]", note)
        self.assertNotIn("--budget ≤", note)
        self.assertIn("deposit", note.lower())
        # the COMPUTED missing amount — floor + fee reserve, so depositing it actually clears the gate
        self.assertIn("$11.50 more USDC", note)   # $10 wallet floor + $1.50 fee reserve, 1 wallet

    def test_below_floor_hinted_deposit_round_trips(self):
        # Bugbot: `floor - usable` understated the deposit when the balance was below the fee
        # reserve (usable clamps to 0) — "$100 more" at $0.75 accessible left usable at $99.25,
        # re-halting E_FUNDS_BELOW_FLOOR. Depositing EXACTLY the hinted amount must clear the floor.
        for wallets, available in ((1, 0.0), (1, 0.75), (1, 1.49), (2, 1.0), (2, 15.0)):
            usable = max(0.0, round(available - deploy.FEE_BUFFER * wallets, 2))
            note = deploy.underfunded_note(self._short(500, wallets, available, usable))
            self.assertIn("[E_FUNDS_BELOW_FLOOR]", note)
            m = re.search(r"at least \$([\d,]+(?:\.\d{2})?) more USDC", note)
            self.assertIsNotNone(m, note)
            hinted = float(m.group(1).replace(",", ""))
            insts = [_inst(f"i{k}", 1.0 / wallets) for k in range(wallets)]
            _amounts, short = deploy.plan_funding(insts, deploy.MIN_WALLET * wallets, available + hinted)
            self.assertIsNone(short, f"deposited the hinted ${hinted} at ${available}/{wallets}w and still short")

    def test_below_multiwallet_floor_is_below_floor(self):
        # $15 usable cannot fund 2 wallets at the $10/wallet floor ($20) — no valid budget exists
        note = deploy.underfunded_note(self._short(400, 2, 17, 15))
        self.assertIn("[E_FUNDS_BELOW_FLOOR]", note)
        self.assertNotIn("--budget ≤", note)

    def test_above_floor_offers_lower_budget(self):
        note = deploy.underfunded_note(self._short(400, 2, 260, 250))
        self.assertIn("[E_FUNDS_SHORT]", note)
        self.assertIn("--budget ≤ 250", note)

    def test_usable_equals_floor_is_short_with_bound_at_floor(self):
        # usable $200 across 2 even wallets is short of a $400 request but funds evenly, so the feasible
        # bound is the bare usable $200 (the $10 floor never binds an even split this large)
        note = deploy.underfunded_note(self._short(400, 2, 200, 200))
        self.assertIn("[E_FUNDS_SHORT]", note)
        self.assertIn("--budget ≤ 200", note)

    def test_uneven_shares_bound_is_below_usable(self):
        # 2 wallets 0.9/0.1, usable $30: the tiny 0.1 leg floors to the $10 wallet floor, so the true max
        # is $22.22 (0.9·b + $10 = $30), NOT the bare usable $30 (which would re-short). Hint names the bound.
        note = deploy.underfunded_note(self._short(50, 2, 33, 30, shares=[0.9, 0.1]))
        self.assertIn("[E_FUNDS_SHORT]", note)
        self.assertIn("--budget ≤ 22.22", note)
        self.assertNotIn("30", note.split("--budget")[1])  # the ceiling is never the bare usable

    def test_budget_hint_flag_value_is_argparse_parseable(self):
        # Bugbot: usd()'s comma grouping in the --budget clause fails `type=float` at ≥ $1,000.
        # The flag value must round-trip through float() exactly as printed — no $, commas, or %g.
        for usable in (250.0, 1000.0, 99999.99, 1_000_000.01):
            note = deploy.underfunded_note(self._short(usable + 10, 1, usable + deploy.FEE_BUFFER, usable))
            m = re.search(r"--budget ≤ (\S+)", note)
            self.assertIsNotNone(m, note)
            self.assertEqual(float(m.group(1)), deploy.max_feasible_budget([1.0], usable), note)

    def test_hinted_bound_round_trips_no_shortfall(self):
        # THE property F1 exists for: re-running plan_funding at the hinted bound must NOT re-short,
        # for uneven shares AND cent-valued balances (incl. five-figure and $1M+ ranges).
        insts = [_inst("a", 0.6), _inst("b", 0.4)]
        for usable in (230.00, 217.33, 99999.99, 1000000.01):
            available = usable + deploy.FEE_BUFFER * len(insts)
            b_star = deploy.max_feasible_budget([0.6, 0.4], usable)
            _amounts, short = deploy.plan_funding(insts, b_star, available)
            self.assertIsNone(short, f"hinted bound ${b_star} re-shorted at usable ${usable}")

    def test_money_is_never_scientific_notation(self):
        # %g printed "$1e+06" at $1M and rounded "$99,999.99" up to "$100000"; usd() never does
        note = deploy.underfunded_note(self._short(2_000_000, 1, 1_000_000.01, 1_000_000.01))
        self.assertNotIn("e+0", note)
        self.assertNotIn("e-0", note)
        self.assertIn("$1,000,000.01", note)

    def test_usd_formatter(self):
        self.assertEqual(deploy.usd(100.0), "$100")
        self.assertEqual(deploy.usd(99999.99), "$99,999.99")
        self.assertEqual(deploy.usd(1_000_000), "$1,000,000")
        self.assertEqual(deploy.usd(1234567.5), "$1,234,567.50")
        self.assertEqual(deploy.usd(250.5), "$250.50")

    def test_always_states_nothing_was_created(self):
        for usable in (0, 250):
            note = deploy.underfunded_note(self._short(400, 2, usable + 10, usable))
            self.assertIn("no wallet was created", note)


class HasDsl(unittest.TestCase):
    def test_full_preset(self):
        self.assertTrue(_dsl_inst({"exit": {"engine": "dsl", "dsl_preset": {"phase1": {}}}}).has_dsl)

    def test_engine_only(self):
        self.assertTrue(_dsl_inst({"exit": {"engine": "dsl"}}).has_dsl)

    def test_preset_only(self):
        self.assertTrue(_dsl_inst({"exit": {"dsl_preset": {"phase1": {}}}}).has_dsl)

    def test_empty_exit_is_naked(self):
        self.assertFalse(_dsl_inst({"exit": {}}).has_dsl)

    def test_no_exit_is_naked(self):
        self.assertFalse(_dsl_inst({}).has_dsl)

    def test_non_dsl_engine_no_preset_is_naked(self):
        self.assertFalse(_dsl_inst({"exit": {"engine": "none"}}).has_dsl)


def _status_with_scanner(name, health):
    """A minimal `senpi status` (getHealthStatus) entry carrying one scanner's health verdict."""
    return {"components": {"scanners": {"scanners": [{"scannerId": name, "health": health}]}}}


class ScannerVerdict(unittest.TestCase):
    # --- state readable: the rich per-scanner row drives the verdict ---
    def test_ticked(self):
        st = {"scanners": [{"name": "sc1", "runCount": 5, "enabled": True}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "ticked")

    def test_scheduled(self):
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": True}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "scheduled")

    def test_heartbeat_without_runcount_is_ticked(self):
        # external scanner that has POSTed (barren, no-signal heartbeat) but runCount still 0
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": True, "lastAliveAt": 1784740000000}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "ticked")

    def test_disabled_is_broken(self):
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": False}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "broken")

    def test_erroring_is_broken(self):
        st = {"scanners": [{"name": "sc1", "runCount": 3, "lastError": "boom"}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "broken")

    def test_unhealthy_health_field_is_broken(self):
        st = {"scanners": [{"name": "sc1", "runCount": 0, "enabled": True, "health": "unhealthy"}]}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, None)[0], "broken")

    # --- state UNreadable: fall back to `senpi status`, never fail closed ---
    def test_state_none_status_healthy_is_live(self):
        # THE regression: getSystemState threw, but status says the scanner is healthy → live, not broken
        status = _status_with_scanner("sc1", "healthy")
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), None, status)[0], "ticked")

    def test_state_none_status_unhealthy_is_broken(self):
        status = _status_with_scanner("sc1", "unhealthy")
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), None, status)[0], "broken")

    def test_state_none_status_none_is_supervised(self):
        # BOTH reads flaky-empty — but the caller only calls this after confirming the runtime is
        # RUNNING (via `runtime list`), and the runtime supervises the declared scanner → live, not broken
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), None, None)[0], "supervised")

    def test_absent_from_state_and_status_is_supervised_not_broken(self):
        # the old false 'scanner not mounted' path — now live-but-unmeasured (runtime running + supervised)
        st = {"scanners": []}
        status = {"components": {"scanners": {"scanners": []}}}
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), st, status)[0], "supervised")

    # --- unwired detection against the CANONICAL RuntimeHealthStatus shape ---
    # Pins that `_deep_first(status, ["scanners"])` lands on the scanners COMPONENT dict (which
    # carries `unwired`), not the per-scanner LIST nested inside it — the shape-drift scenario
    # where the isinstance(dict) guard would silently skip the check and an unwired (blind)
    # runtime would fall through to `supervised` = live (PR #505 Bugbot finding).
    @staticmethod
    def _canonical_health_entry(**scanners_extra):
        """A full getHealthStatus statuses[] entry, field order as the runtime emits it."""
        return {
            "runtimeId": "rt-1", "runtimeName": "demo-main", "startedAt": "2026-08-03T00:00:00Z",
            "generatedAt": "2026-08-03T00:01:00Z", "health": "unknown", "logLevel": "info",
            "components": {"scanners": {
                "component": "scanners", "health": "unknown", "updatedAt": "2026-08-03T00:01:00Z",
                "totals": {"registered": 0, "enabled": 0, "inFlight": 0, "degraded": 0, "unhealthy": 0},
                "scanners": [],  # the nested per-scanner LIST that must NOT shadow the component dict
                **scanners_extra,
            }},
        }

    def test_unwired_in_canonical_health_payload_is_broken(self):
        status = self._canonical_health_entry(unwired=True, unwiredPhase="launch")
        verdict, detail = deploy._scanner_verdict(_scan_inst(), None, status)
        self.assertEqual(verdict, "broken")
        self.assertIn("launch", detail)

    def test_wired_canonical_health_payload_is_not_flagged_unwired(self):
        status = self._canonical_health_entry()
        status["components"]["scanners"]["scanners"] = [{"scannerId": "sc1", "health": "healthy"}]
        self.assertEqual(deploy._scanner_verdict(_scan_inst(), None, status)[0], "ticked")


class DslVerdict(unittest.TestCase):
    def test_config_missing(self):
        v = deploy._dsl_verdict(types.SimpleNamespace(has_dsl=False), {"dsl": {"enabled": True}})
        self.assertEqual(v[0], "config-missing")

    def test_wired_when_status_unreadable(self):
        self.assertEqual(deploy._dsl_verdict(types.SimpleNamespace(has_dsl=True), None)[0], "wired")

    def test_monitor_down(self):
        v = deploy._dsl_verdict(types.SimpleNamespace(has_dsl=True), {"dsl": {"enabled": False}})
        self.assertEqual(v[0], "monitor-down")

    def test_wired(self):
        v = deploy._dsl_verdict(types.SimpleNamespace(has_dsl=True), {"dsl": {"enabled": True}})
        self.assertEqual(v[0], "wired")


class BudgetVerdict(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(deploy._budget_verdict({"requested": 1000, "strategyId": "x"}, {"x": 1000.0})[0], "ok")

    def test_underfunded(self):
        self.assertEqual(deploy._budget_verdict({"requested": 1000, "strategyId": "x"}, {"x": 100.0})[0], "underfunded")

    def test_no_requested_is_ok(self):
        self.assertEqual(deploy._budget_verdict({"strategyId": "x"}, {"x": 100.0})[0], "ok")

    def test_funded_unreadable_is_ok(self):
        self.assertEqual(deploy._budget_verdict({"requested": 1000, "strategyId": "x"}, {})[0], "ok")


import _cli    # noqa: E402
import close   # noqa: E402


class CloseRuntimeDeleteConfirm(unittest.TestCase):
    """close_one must judge runtime-delete success by `runtime list` (reliable), NOT the delete's exit
    code — which is non-zero both on a flaky gateway hiccup AND when the runtime is already gone
    (NOT_FOUND). Trusting rc broke idempotent re-runs and false-aborted the money-critical strategy_close."""

    def setUp(self):
        self._orig = (_cli.run_cli, _cli.list_runtimes_or_none, close.MCPClient)
        self.deletes = []          # every `runtime delete` invocation
        self.closed = []           # every strategy_close strategyId
        _cli.run_cli = lambda args, timeout=60: (self.deletes.append(args) or (1, "", "[⚡HyperDX] banner…"))
        outer = self

        class _FakeMCP:
            def mcp_call(self, name, timeout=None, **kw):
                if name == "strategy_close":
                    outer.closed.append(kw.get("strategyId"))
                return {"success": True}
        close.MCPClient = _FakeMCP

    def tearDown(self):
        _cli.run_cli, _cli.list_runtimes_or_none, close.MCPClient = self._orig

    _STRAT = {"strategyId": "s1", "strategyWalletAddress": "0xabc", "status": "ACTIVE"}
    _RUNTIMES = [{"name": "pkg-main", "wallet": "0xabc"}]

    def test_gone_after_delete_is_success_and_triggers_close(self):
        # delete returns non-zero (banner noise), but the inventory reads cleanly and the runtime is gone
        _cli.list_runtimes_or_none = lambda: []
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "closing")   # NOT 'failed' despite rc=1
        self.assertEqual(self.closed, ["s1"])         # money-critical close DID fire
        self.assertEqual(len(self.deletes), 1)        # gone on first try → no retry

    def test_still_present_after_retry_fails_without_closing(self):
        # runtime never leaves `runtime list` → genuine failure: report it, do NOT strategy_close
        _cli.list_runtimes_or_none = lambda: [{"name": "pkg-main", "wallet": "0xabc"}]
        rec = close.close_one("main", self._STRAT, self._RUNTIMES, False, lambda m: None)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(self.closed, [])             # never close while the runtime can re-enter
        self.assertEqual(len(self.deletes), 2)        # one retry before giving up
        self.assertNotIn("HyperDX", rec.get("error", ""))  # clean message, not banner spam

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


class WalletsUnrecoverableNote(unittest.TestCase):
    def test_no_wallets_names_create(self):
        note = deploy.wallets_unrecoverable_note(
            "spider", [("main", "none", "no ACTIVE spider wallet on the backend for instance 'main'")])
        self.assertIn("[E_STATE_NO_WALLETS]", note)
        self.assertIn("deploy.py create spider --budget", note)

    def test_ambiguous_never_names_teardown(self):
        note = deploy.wallets_unrecoverable_note(
            "spider", [("main", "ambiguous", "2 ACTIVE spider wallets match instance 'main'")])
        self.assertIn("[E_STATE_AMBIGUOUS_WALLETS]", note)
        self.assertNotIn("close.py", note)           # a live funded wallet may be in the set
        self.assertNotIn("deploy.py create", note)   # recreate is not a valid escape here either
        self.assertIn("status.py spider", note)      # read-only triage is the named next step

    def test_mixed_causes_use_the_conservative_ambiguous_text(self):
        note = deploy.wallets_unrecoverable_note(
            "spider", [("long", "none", "no ACTIVE wallet"),
                       ("short", "ambiguous", "2 ACTIVE wallets match")])
        self.assertIn("[E_STATE_AMBIGUOUS_WALLETS]", note)
        self.assertNotIn("close.py", note)

    def test_unnamed_uses_conservative_triage_not_nothing_exists(self):
        # a wallet exists but isn't name-matched → must NOT claim "nothing exists"; conservative triage
        note = deploy.wallets_unrecoverable_note(
            "spider", [("long", "unnamed", "1 ACTIVE spider wallet(s) exist but none is named 'spider-long'")])
        self.assertIn("[E_STATE_AMBIGUOUS_WALLETS]", note)
        self.assertNotIn("[E_STATE_NO_WALLETS]", note)
        self.assertNotIn("nothing", note.lower())     # never "nothing exists / nothing at risk"
        self.assertNotIn("close.py", note)            # never steer to teardown
        self.assertIn("status.py", note)              # read-only triage is the named next step

    def test_unreadable_uses_conservative_triage(self):
        note = deploy.wallets_unrecoverable_note(
            "spider", [("main", "unreadable", "found 1 ACTIVE spider strategy ... wallet address is unreadable")])
        self.assertIn("[E_STATE_AMBIGUOUS_WALLETS]", note)
        self.assertNotIn("close.py", note)

    def test_refusal_next_steps_are_absolute_runnable_paths(self):
        # F7: the hinted commands must resolve from any cwd (Path(__file__)-anchored), not bare filenames
        no_wallets = deploy.wallets_unrecoverable_note("spider", [("main", "none", "no ACTIVE wallet")])
        self.assertIn("/deploy.py create spider --budget", no_wallets)
        ambiguous = deploy.wallets_unrecoverable_note("spider", [("main", "ambiguous", "2 match")])
        self.assertIn("/status.py spider", ambiguous)
        self.assertIn("/deploy.py runtime spider", ambiguous)


class RecoverWalletKinds(unittest.TestCase):
    def _pkg(self, n_instances=1):
        return types.SimpleNamespace(id="spider",
                                     instances=[types.SimpleNamespace(name=f"i{k}") for k in range(n_instances)])

    def test_no_candidates_is_kind_none(self):
        w, kind, why = deploy._recover_wallet(self._pkg(), types.SimpleNamespace(name="main"), [])
        self.assertIsNone(w)
        self.assertEqual(kind, "none")
        self.assertIn("no ACTIVE", why)

    def test_multiple_wallets_is_kind_ambiguous(self):
        active = [{"strategyWalletAddress": "0xaaa", "status": "ACTIVE"},
                  {"strategyWalletAddress": "0xbbb", "status": "ACTIVE"}]
        w, kind, why = deploy._recover_wallet(self._pkg(), types.SimpleNamespace(name="main"), active)
        self.assertIsNone(w)
        self.assertEqual(kind, "ambiguous")

    def test_single_wallet_recovers(self):
        active = [{"strategyWalletAddress": "0xaaa", "status": "ACTIVE"}]
        w, kind, _ = deploy._recover_wallet(self._pkg(), types.SimpleNamespace(name="main"), active)
        self.assertEqual(w, "0xaaa")
        self.assertIsNone(kind)

    def test_single_candidate_unreadable_wallet_is_unreadable_not_ambiguous(self):
        # F5: one ACTIVE candidate whose wallet address is unreadable (backend field drift) → "unreadable",
        # NOT the "1 ... wallets ... ambiguous" self-contradiction
        active = [{"strategyName": "spider", "status": "ACTIVE"}]  # no wallet field
        w, kind, why = deploy._recover_wallet(self._pkg(), types.SimpleNamespace(name="main"), active)
        self.assertIsNone(w)
        self.assertEqual(kind, "unreadable")
        self.assertIn("unreadable", why)

    # --- multi-instance: match by the sanitized name create assigned each wallet ---
    @staticmethod
    def _multi(*names):
        return types.SimpleNamespace(id="spider",
                                     instances=[types.SimpleNamespace(name=n) for n in names])

    def test_multi_instance_matches_by_name(self):
        active = [{"strategyName": "spider-long", "strategyWalletAddress": "0xaaa", "status": "ACTIVE"},
                  {"strategyName": "spider-short", "strategyWalletAddress": "0xbbb", "status": "ACTIVE"}]
        w, kind, _ = deploy._recover_wallet(self._multi("long", "short"),
                                            types.SimpleNamespace(name="long"), active)
        self.assertEqual(w, "0xaaa")
        self.assertIsNone(kind)

    def test_multi_instance_matches_sanitized_name(self):
        # create sanitizes "spider-a.b" → "spider-ab"; recovery must re-derive the same sanitized name
        active = [{"strategyName": "spider-ab", "strategyWalletAddress": "0xaaa", "status": "ACTIVE"}]
        w, kind, _ = deploy._recover_wallet(self._multi("a.b", "c"),
                                            types.SimpleNamespace(name="a.b"), active)
        self.assertEqual(w, "0xaaa")
        self.assertIsNone(kind)

    def test_multi_instance_unnamed_instance_matches_bare_id(self):
        # an unnamed instance (name=None) is funded under the bare package id
        active = [{"strategyName": "spider", "strategyWalletAddress": "0xaaa", "status": "ACTIVE"}]
        w, kind, _ = deploy._recover_wallet(self._multi(None, "short"),
                                            types.SimpleNamespace(name=None), active)
        self.assertEqual(w, "0xaaa")
        self.assertIsNone(kind)

    def test_multi_instance_name_rejection_fallback_is_unnamed_not_none(self):
        # create hit a name rejection and funded WITHOUT a custom name → the wallet exists but under
        # the bare id, not "spider-long". Recovery must refuse ("unnamed"), NEVER claim "none" (which
        # would steer to create → teardown of the funded wallet).
        active = [{"strategyName": "spider", "strategyWalletAddress": "0xaaa", "status": "ACTIVE"}]
        w, kind, why = deploy._recover_wallet(self._multi("long", "short"),
                                              types.SimpleNamespace(name="long"), active)
        self.assertIsNone(w)
        self.assertEqual(kind, "unnamed")
        self.assertNotEqual(kind, "none")
        self.assertIn("spider", why)  # names the existing wallet(s), not "nothing exists"

    def test_multi_instance_all_unreadable_unmatched_is_unnamed_not_none(self):
        # Bugbot: ACTIVE strategies exist but none name-matches AND every wallet address is
        # unreadable (backend field drift). The old readable-address filter dropped them all →
        # kind "none" → "nothing at risk" → create, tearing down a possibly funded live strategy.
        active = [{"strategyName": "spider", "status": "ACTIVE"},
                  {"strategyName": "renamed-by-user", "status": "ACTIVE"}]  # no wallet fields
        w, kind, why = deploy._recover_wallet(self._multi("long", "short"),
                                              types.SimpleNamespace(name="long"), active)
        self.assertIsNone(w)
        self.assertEqual(kind, "unnamed")
        self.assertIn("2 ACTIVE", why)

    def test_multi_instance_truly_absent_is_none(self):
        # no <id> wallet anywhere on the backend → genuinely "none" (create is safe)
        w, kind, _ = deploy._recover_wallet(self._multi("long", "short"),
                                            types.SimpleNamespace(name="long"), [])
        self.assertIsNone(w)
        self.assertEqual(kind, "none")


# USDC contracts per chain, only used to make the fixtures below look like the real payload.
_USDC_ETH = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


def _portfolio(**over):
    """An `account_get_portfolio` response with the REAL field set (see the live payload in
    references/funding-sources.md). `total_in_hyperliquid`, `total_spot_usd_in_hyperliquid` and
    `token_balances` are DISJOINT components of `total_balance_usd`, so a funding preflight has to
    add them up — reading one of them is reading a fraction of the fundable balance."""
    port = {"total_balance_usd": 0.0, "total_allocated_in_strategy": 0.0, "total_withdrawable": 0.0,
            "total_in_hyperliquid": 0.0, "total_token_balance_usd": 0.0,
            "total_spot_usd_in_hyperliquid": 0.0, "token_balances": [], "spot_balances": [],
            "positions": []}
    port.update(over)
    return {"success": True, "data": {"portfolio": port}}


def _evm(sym, amount, chain_id, addr=_USDC_BASE):
    return {"tokenSymbol": sym, "formattedBalance": amount, "balanceInUSD": amount,
            "chainId": chain_id, "tokenAddress": addr, "decimals": 6}


def _spot(sym, usd_value):
    return {"tokenSymbol": sym, "tokenId": 0, "total": str(usd_value), "hold": "0.0",
            "usdValue": str(usd_value)}


class _StubMCP:
    """Returns a canned `account_get_portfolio`; raises if `raise_with` is set."""

    def __init__(self, payload=None, raise_with=None):
        self.payload, self.raise_with = payload, raise_with

    def mcp_call(self, tool, timeout=None, **kw):
        if self.raise_with:
            raise self.raise_with
        assert tool == "account_get_portfolio", tool
        return self.payload


class AvailableUsd(unittest.TestCase):
    """`available_usd` must report what `strategy_create_custom_strategy` can actually FUND FROM:
    Hyperliquid perps → Hyperliquid spot USDC → EVM USDC bridged in (Base, Arbitrum, Optimism,
    Ethereum, BNB, Polygon). Reading perps alone makes the hard `underfunded` halt fire on money
    the create call would have pulled in by itself."""

    def test_incident_starling_base_usdc_is_counted(self):
        # THE incident: $440 Starling deploy refused with [E_FUNDS_SHORT] "only $198.42 is accessible"
        # while ~$248 USDC sat on Base — which create auto-bridges. Nothing was actually short.
        mcp = _StubMCP(_portfolio(total_in_hyperliquid=198.42, total_token_balance_usd=248.0,
                                  token_balances=[_evm("USDC", 248.0, 8453)]))
        avail = deploy.available_usd(mcp)
        self.assertAlmostEqual(avail, 446.42, places=2)
        # and the gate it feeds must not halt a $440 ask
        _amounts, short = deploy.plan_funding([_inst("main", 1.0)], 440, avail)
        self.assertIsNone(short)

    def test_counts_hl_spot_usdc(self):
        # create pulls perps FIRST, then spot USDC — both are fundable without touching a bridge
        mcp = _StubMCP(_portfolio(total_in_hyperliquid=60.0, total_spot_usd_in_hyperliquid=45.0,
                                  spot_balances=[_spot("USDC", 45.0)]))
        self.assertAlmostEqual(deploy.available_usd(mcp), 105.0, places=2)

    def test_counts_usdc_on_every_chain(self):
        # Base, Optimism, BNB, Polygon, Ethereum, Arbitrum — and deliberately NO chain allowlist, so
        # a chain added to the bridge later can't silently start failing this gate closed again
        mcp = _StubMCP(_portfolio(token_balances=[
            _evm("USDC", 10.0, 1), _evm("USDC", 10.0, 10), _evm("USDC", 10.0, 56),
            _evm("USDC", 10.0, 137), _evm("USDC", 10.0, 8453), _evm("USDC", 10.0, 42161)]))
        self.assertAlmostEqual(deploy.available_usd(mcp), 60.0, places=2)

    def test_counts_usdc_on_unrecognised_chain(self):
        # an unknown chainId is counted, not dropped: over-counting is the safe direction (create
        # auto-funds and reports a real shortfall as SERR037), under-counting is a false halt
        mcp = _StubMCP(_portfolio(total_in_hyperliquid=100.0,
                                  token_balances=[_evm("USDC", 250.0, 999999)]))
        self.assertAlmostEqual(deploy.available_usd(mcp), 350.0, places=2)

    def test_ignores_non_usdc_holdings(self):
        # only USDC is bridged/pulled; spot HYPE and EVM WETH are not fundable balance
        mcp = _StubMCP(_portfolio(total_in_hyperliquid=100.0,
                                  total_spot_usd_in_hyperliquid=900.0,
                                  spot_balances=[_spot("HYPE", 900.0)],
                                  token_balances=[_evm("WETH", 5000.0, 8453)]))
        self.assertAlmostEqual(deploy.available_usd(mcp), 100.0, places=2)

    def test_spot_scalar_used_when_rows_absent(self):
        # no per-token spot rows to filter → fall back to the scalar rather than dropping spot to 0
        mcp = _StubMCP(_portfolio(total_in_hyperliquid=10.0, total_spot_usd_in_hyperliquid=25.0))
        self.assertAlmostEqual(deploy.available_usd(mcp), 35.0, places=2)

    def test_null_usd_field_falls_through_to_balance(self):
        # `balanceInUSD` present but null must fall through to `formattedBalance`, not zero the row —
        # `dig` alone stops at the first key PRESENT, which would under-count into a false halt
        row = _evm("USDC", 250.0, 8453)
        row["balanceInUSD"] = None
        mcp = _StubMCP(_portfolio(total_in_hyperliquid=100.0, token_balances=[row]))
        self.assertAlmostEqual(deploy.available_usd(mcp), 350.0, places=2)

    def test_garbage_row_does_not_poison_the_sum(self):
        mcp = _StubMCP(_portfolio(total_in_hyperliquid=100.0, token_balances=[
            {"tokenSymbol": "USDC", "balanceInUSD": "not-a-number", "chainId": 8453},
            _evm("USDC", 40.0, 8453)]))
        self.assertAlmostEqual(deploy.available_usd(mcp), 140.0, places=2)

    def test_unreadable_returns_none_so_gate_never_halts(self):
        mcp = _StubMCP(raise_with=deploy.MCPError("boom"))
        self.assertIsNone(deploy.available_usd(mcp))
        self.assertIsNone(deploy.plan_funding([_inst("a", 1.0)], 1000, None)[1])

    def test_empty_portfolio_is_zero_not_none(self):
        # a genuinely empty account must still HALT (0.0), not read as unknown and sail through
        self.assertEqual(deploy.available_usd(_StubMCP(_portfolio())), 0.0)


class ScopePkgSingleArm(unittest.TestCase):
    """--instance scoping for a single-arm (re)deploy/upgrade: narrow to one sleeve, fund it fully,
    leave siblings untouched, and reject an unknown name with a helpful list."""

    def _pkg(self):
        return types.SimpleNamespace(
            id="cub",
            instances=[_inst("long", 0.45), _inst("short", 0.45), _inst("preipo", 0.10)],
        )

    def test_narrows_to_the_named_arm(self):
        pkg = self._pkg()
        deploy._scope_pkg(pkg, "preipo")
        self.assertEqual([i.name for i in pkg.instances], ["preipo"])

    def test_scoped_arm_funds_fully_not_scaled_by_its_share(self):
        # a 0.10-share sleeve deployed alone must take the WHOLE --budget, else $15 → ~$1.50 → floored.
        pkg = self._pkg()
        deploy._scope_pkg(pkg, "preipo")
        self.assertEqual(pkg.instances[0].funding_share, 1.0)

    def test_unknown_instance_raises_with_the_valid_names(self):
        pkg = self._pkg()
        with self.assertRaises(SystemExit) as ctx:
            deploy._scope_pkg(pkg, "nope")
        msg = str(ctx.exception)
        self.assertIn("no instance 'nope'", msg)
        self.assertIn("long", msg)  # lists what IS valid so the agent can self-correct
        self.assertIn("preipo", msg)


class UpgradeGates(unittest.TestCase):
    """`upgrade` refuses the two ways it must before touching MCP: a multi-arm package with no --instance
    (which arm?), and a missing --budget (the fresh wallet needs funding)."""

    def _pkg(self, names):
        return types.SimpleNamespace(id="cub", dir=Path("/nonexistent"),
                                     version="1", instances=[_inst(n, 1.0) for n in names])

    def _args(self, **kw):
        base = dict(budget=None, dry_run=False, yes=False, instance=None, json=False,
                    max_wait=0, decision_model=None, ref=None, package="cub")
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_multiarm_without_instance_refuses(self):
        with self.assertRaises(SystemExit) as e:
            deploy.cmd_upgrade(self._pkg(["long", "short"]), self._args(budget=25), lambda m: None)
        self.assertIn("ONE arm", str(e.exception))
        self.assertIn("long", str(e.exception))  # names the valid arms

    def test_budget_required_when_not_dry_run(self):
        with self.assertRaises(SystemExit) as e:
            deploy.cmd_upgrade(self._pkg(["preipo"]), self._args(budget=None), lambda m: None)
        self.assertIn("--budget", str(e.exception))


class UpgradeDispatch(unittest.TestCase):
    """PHASE B advances ONE tested step per call, chosen by the arm's deploy-state status:
    pending→create, active→runtime, registered→verify. Money-path calls are stubbed — this asserts the
    routing, not the steps themselves (those have their own tests + live integration)."""

    _PATCH = ("load_state", "save_state", "MCPClient", "cmd_create", "cmd_runtime", "cmd_verify")

    def setUp(self):
        self._saved = {k: getattr(deploy, k) for k in self._PATCH}
        self.calls = []
        deploy.MCPClient = lambda *a, **k: object()
        deploy.save_state = lambda pkg, st: None
        deploy.cmd_create = lambda pkg, a, log: (self.calls.append("create"), {"status": "wallets-ready"})[1]
        deploy.cmd_runtime = lambda pkg, a, log: (self.calls.append("runtime"), {"status": "registered"})[1]
        deploy.cmd_verify = lambda pkg, a, log: (self.calls.append("verify"), {"status": "live"})[1]

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(deploy, k, v)

    def _pkg(self):
        return types.SimpleNamespace(id="cub", dir=Path("/x"), version="1",
                                     instances=[_inst("preipo", 1.0)])

    def _args(self):
        return types.SimpleNamespace(budget=25, dry_run=False, yes=True, instance="preipo",
                                     json=False, max_wait=0, decision_model=None, ref=None, package="cub")

    def _state(self, inst_status, **inst_extra):
        arm = {"status": inst_status}
        arm.update(inst_extra)
        return {"id": "cub", "_upgrade": {"phase": "redeploy"}, "instances": {"preipo": arm}}

    def test_redeploy_pending_routes_to_create(self):
        deploy.load_state = lambda pkg: self._state("pending")
        deploy.cmd_upgrade(self._pkg(), self._args(), lambda m: None)
        self.assertEqual(self.calls, ["create"])

    def test_redeploy_active_routes_to_runtime(self):
        deploy.load_state = lambda pkg: self._state("active", strategyId="0xsid", wallet="0xw")
        deploy.cmd_upgrade(self._pkg(), self._args(), lambda m: None)
        self.assertEqual(self.calls, ["runtime"])

    def test_redeploy_registered_routes_to_verify(self):
        deploy.load_state = lambda pkg: self._state("registered", strategyId="0xsid", wallet="0xw")
        deploy.cmd_upgrade(self._pkg(), self._args(), lambda m: None)
        self.assertEqual(self.calls, ["verify"])


class UpgradeArity(unittest.TestCase):
    """`_scope_pkg` narrows pkg.instances to 1, but `_wallet_name` keys off arity — so the scoped arm's
    name must be derived from the TRUE pre-scope arity, else a multi-arm arm's wallet collapses to the
    bare `<id>` and every by-name lookup (upgrade fallback + scoped create's naming) misses."""

    def _multi(self):
        arm = lambda n: types.SimpleNamespace(name=n, funding_share=1 / 3, runtime_name=f"cub-{n}")
        return types.SimpleNamespace(id="cub", dir=Path("/x"), version="1",
                                     instances=[arm("long"), arm("short"), arm("preipo")])

    def test_scoped_multiarm_keeps_full_name(self):
        pkg = self._multi()
        deploy._scope_pkg(pkg, "preipo")
        self.assertEqual(deploy._wallet_name(pkg, pkg.instances[0]), "cub-preipo")  # NOT the bare "cub"

    def test_true_single_arm_is_bare_id(self):
        pkg = types.SimpleNamespace(id="solo", dir=Path("/x"), version="1",
                                    instances=[types.SimpleNamespace(name="main", funding_share=1.0)])
        self.assertEqual(deploy._wallet_name(pkg, pkg.instances[0]), "solo")       # unscoped 1-arm unchanged


class UpgradeConsentPhase(unittest.TestCase):
    """PHASE A: detect a live arm — via its runtime, OR (runtime gone) via the arm's stable strategyName
    among ACTIVE strategies — gate the flatten on --yes, surface a failed close, and emit under --json.

    The fixture is a REAL 3-arm package narrowed by `_scope_pkg` (exactly what main() hands cmd_upgrade),
    and `_wallet_name` is deliberately NOT stubbed — the name it derives after scoping IS the mechanism
    under test. Stubbing it (as the first cut did) hid the arity bug that made the fallback search 'cub'."""

    _PATCH = ("_cli", "MCPClient", "load_state", "save_state", "cmd_create")

    def setUp(self):
        self._saved = {k: getattr(deploy, k) for k in self._PATCH}
        self._closed = []
        self._created = []
        self._close_result = {"status": "closed"}
        deploy.MCPClient = lambda *a, **k: object()
        deploy.load_state = lambda pkg: {"id": pkg.id, "instances": {}}
        deploy.save_state = lambda pkg, st: None
        deploy.cmd_create = lambda pkg, a, log: (self._created.append("create"), {"status": "wallets-ready"})[1]
        close_mod = types.ModuleType("close")

        def close_one(label, s, rts, dry, log):
            self._closed.append(s)
            return self._close_result
        close_mod.close_one = close_one
        sys.modules["close"] = close_mod

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(deploy, k, v)
        sys.modules.pop("close", None)

    def _pkg(self):
        """A real 3-arm package narrowed to `preipo` by `_scope_pkg` — what main() hands cmd_upgrade for
        `--instance preipo`. Multi-arm on purpose: scoping a 1-arm fixture would hide the arity trap."""
        arm = lambda n: types.SimpleNamespace(name=n, funding_share=1 / 3, runtime_name=f"cub-{n}")
        pkg = types.SimpleNamespace(id="cub", dir=Path("/x"), version="1",
                                    instances=[arm("long"), arm("short"), arm("preipo")])
        deploy._scope_pkg(pkg, "preipo")
        return pkg

    def _args(self, yes=False, json=False):
        return types.SimpleNamespace(budget=25, dry_run=False, yes=yes, instance="preipo",
                                     json=json, max_wait=0, decision_model=None, ref=None, package="cub")

    def _cli(self, runtime=None, strategies=()):
        s = types.SimpleNamespace()
        s.find_runtime = lambda name: runtime
        s.runtime_wallet = lambda rt: (rt or {}).get("wallet")
        s.list_runtimes = lambda: []
        s.strategy_open = lambda x: x.get("status", "ACTIVE") not in ("CLOSED", "FAILED", "TERMINATED")
        s.strategy_wallet = lambda x: x.get("wallet")
        s.strategy_name = lambda x: x.get("name")
        s.wallet_match = lambda a, b: bool(a) and bool(b) and str(a).lower() == str(b).lower()

        def strategies_for(mcp, skill_name=None, statuses=None, **kw):
            rows = list(strategies)
            if statuses:
                rows = [x for x in rows if x.get("status", "ACTIVE") in statuses]
            return rows
        s.strategies_for = strategies_for
        return s

    def test_live_arm_without_yes_needs_consent(self):
        deploy._cli = self._cli(runtime={"wallet": "0xAAA"},
                                strategies=[{"wallet": "0xAAA", "name": "cub-preipo", "status": "ACTIVE"}])
        out = deploy.cmd_upgrade(self._pkg(), self._args(yes=False), lambda m: None)
        self.assertEqual(out["status"], "needs-consent")
        self.assertEqual(self._closed, [])            # nothing flattened without consent
        self.assertEqual(self._created, [])           # and it did NOT fall through to create

    def test_live_arm_with_yes_closes_that_arm(self):
        deploy._cli = self._cli(runtime={"wallet": "0xAAA"},
                                strategies=[{"wallet": "0xAAA", "name": "cub-preipo", "status": "ACTIVE"}])
        out = deploy.cmd_upgrade(self._pkg(), self._args(yes=True), lambda m: None)
        self.assertEqual(out["status"], "closing")
        self.assertEqual(len(self._closed), 1)

    def test_runtime_gone_but_strategy_open_still_gated(self):
        # THE FIX (regression guard): no runtime, but an ACTIVE strategy carries the arm's REAL name
        # (cub-preipo, only correct once arity survives scoping) → resolve the wallet by name and STILL
        # gate consent, instead of treating the arm as undeployed and double-funding.
        deploy._cli = self._cli(runtime=None,
                                strategies=[{"wallet": "0xBBB", "name": "cub-preipo", "status": "ACTIVE"}])
        out = deploy.cmd_upgrade(self._pkg(), self._args(yes=False), lambda m: None)
        self.assertEqual(out["status"], "needs-consent")
        self.assertEqual(self._created, [])           # must NOT have jumped to create

    def test_runtime_gone_sibling_active_not_mismatched(self):
        # no runtime for preipo, and the only ACTIVE strategy is a SIBLING (cub-long) → no name match →
        # treat as not-deployed and redeploy; must never bind preipo to a sibling's wallet.
        deploy._cli = self._cli(runtime=None,
                                strategies=[{"wallet": "0xLONG", "name": "cub-long", "status": "ACTIVE"}])
        deploy.cmd_upgrade(self._pkg(), self._args(yes=False), lambda m: None)
        self.assertEqual(self._closed, [])
        self.assertEqual(self._created, ["create"])   # genuinely undeployed arm → PHASE B

    def test_truly_undeployed_proceeds_to_create(self):
        deploy._cli = self._cli(runtime=None, strategies=[])
        deploy.cmd_upgrade(self._pkg(), self._args(yes=False), lambda m: None)
        self.assertEqual(self._created, ["create"])

    def test_failed_close_surfaces_and_does_not_advance(self):
        # close_one can report failed (runtime still listed after delete → may re-enter). It must SURFACE
        # as `failed`, not get swallowed and advance to a `closing` poll nothing will ever clear.
        self._close_result = {"status": "failed", "error": "runtime still listed"}
        deploy._cli = self._cli(runtime={"wallet": "0xAAA"},
                                strategies=[{"wallet": "0xAAA", "name": "cub-preipo", "status": "ACTIVE"}])
        out = deploy.cmd_upgrade(self._pkg(), self._args(yes=True), lambda m: None)
        self.assertEqual(out["status"], "failed")
        self.assertIn("runtime still listed", out["note"])

    def test_json_emits_consent_to_stdout(self):
        # under --json, `log` is a no-op; the PHASE-A verdict must still reach stdout as JSON, else the
        # agent (which runs --json) gets exit code 2 and an empty stdout — losing the consent text.
        deploy._cli = self._cli(runtime={"wallet": "0xAAA"},
                                strategies=[{"wallet": "0xAAA", "name": "cub-preipo", "status": "ACTIVE"}])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            deploy.cmd_upgrade(self._pkg(), self._args(yes=False, json=True), lambda m: None)
        self.assertIn("needs-consent", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
