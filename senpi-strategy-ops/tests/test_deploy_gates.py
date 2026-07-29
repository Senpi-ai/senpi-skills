#!/usr/bin/env python3
"""Hermetic unit tests for the deploy liveness + budget gates.

No MCP, no openclaw, no network — every input is a plain dict/stub. Run:
    python3 senpi-strategy-ops/tests/test_deploy_gates.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
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
        # $200 across 60/40 floors the small leg to $100 → $220 total; $300 available covers it, no halt
        amounts, short = deploy.plan_funding([_inst("a", 0.6), _inst("b", 0.4)], 200, 300)
        self.assertEqual(amounts, {"a": 120.0, "b": 100.0})
        self.assertIsNone(short)

    def test_floor_over_balance_is_short(self):
        _amounts, short = deploy.plan_funding([_inst("a", 0.6), _inst("b", 0.4)], 200, 200)
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
        # the M381223 case: $0 accessible → the old note said "--budget ≤ $0"
        note = deploy.underfunded_note(self._short(500, 1, 0, 0))
        self.assertIn("[E_FUNDS_BELOW_FLOOR]", note)
        self.assertNotIn("--budget ≤", note)
        self.assertIn("deposit", note.lower())
        self.assertIn("$100 more USDC", note)  # the COMPUTED missing amount, not the $100/wallet boilerplate

    def test_below_multiwallet_floor_is_below_floor(self):
        # $180 usable cannot fund 2 wallets at $100/wallet — no valid budget exists
        note = deploy.underfunded_note(self._short(400, 2, 190, 180))
        self.assertIn("[E_FUNDS_BELOW_FLOOR]", note)
        self.assertNotIn("--budget ≤", note)

    def test_above_floor_offers_lower_budget(self):
        note = deploy.underfunded_note(self._short(400, 2, 260, 250))
        self.assertIn("[E_FUNDS_SHORT]", note)
        self.assertIn("--budget ≤ $250", note)

    def test_usable_equals_floor_is_short_with_bound_at_floor(self):
        # boundary: usable == wallets × $100 → E_FUNDS_SHORT, and the only feasible budget is the floor
        note = deploy.underfunded_note(self._short(400, 2, 200, 200))
        self.assertIn("[E_FUNDS_SHORT]", note)
        self.assertIn("--budget ≤ $200", note)

    def test_uneven_shares_bound_is_below_usable(self):
        # 2 wallets 0.6/0.4, usable $230: the small leg floors to $100 so the true max is $216.67,
        # NOT the old bare-usable $230 (which re-shorts). The hint must name the feasible bound.
        note = deploy.underfunded_note(self._short(238, 2, 233, 230, shares=[0.6, 0.4]))
        self.assertIn("[E_FUNDS_SHORT]", note)
        self.assertIn("--budget ≤ $216.67", note)
        self.assertNotIn("$230", note.split("--budget")[1])  # the ceiling is never the bare usable

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

    def test_multi_instance_truly_absent_is_none(self):
        # no <id> wallet anywhere on the backend → genuinely "none" (create is safe)
        w, kind, _ = deploy._recover_wallet(self._multi("long", "short"),
                                            types.SimpleNamespace(name="long"), [])
        self.assertIsNone(w)
        self.assertEqual(kind, "none")


if __name__ == "__main__":
    unittest.main(verbosity=2)
