#!/usr/bin/env python3
"""Hermetic tests for the `_cli` READ layer — the helpers every lifecycle script quotes from.

Two contracts live here, both of them about not answering a question the surface never answered:

  * **unreadable != empty.** `find_list`/`list_strategies` degrade to `[]` on a payload they cannot
    navigate, which reads as "nothing is deployed" at every call site that trusts them. The
    fail-closed pair (`find_list_or_none` / `list_strategies_or_none`) keeps the two apart.
  * **a requested amount is never a funded one.** `strategy_funded` reports the backend's own
    figure or nothing at all.
  * **…and empty != unreadable, the same contract inverted.** `list_runtimes_or_none` must call a box
    with no runtimes EMPTY (`senpi.listRuntimes` answered `{"runtimes": []}`), not unreadable — otherwise `verify` cannot answer on exactly the boxes
    where the answer is "nothing is running".

Run:  python3 senpi-strategy-ops/tests/test_cli_reads.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import sys
import time
import unittest
from unittest import mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli  # noqa: E402


class FakeMCP:
    def __init__(self, payload=None, raises=None):
        self.payload, self.raises, self.calls = payload, raises, []

    def mcp_call(self, tool, timeout=15, **kw):
        self.calls.append((tool, kw))
        if self.raises is not None:
            raise self.raises
        return self.payload


class FindListOrNone(unittest.TestCase):
    def test_an_empty_list_is_an_answer(self):
        self.assertEqual(_cli.find_list_or_none({"strategies": []}, "strategies"), [])
        self.assertEqual(_cli.find_list_or_none([], "strategies"), [])

    def test_an_unnavigable_shape_is_none(self):
        self.assertIsNone(_cli.find_list_or_none({"ok": True, "count": 0}, "strategies"))
        self.assertIsNone(_cli.find_list_or_none("nope", "strategies"))
        self.assertIsNone(_cli.find_list_or_none({"data": {"records": 3}}, "strategies"))

    def test_the_wrappers_are_still_navigated(self):
        self.assertEqual(_cli.find_list_or_none({"data": {"strategies": [1]}}, "strategies"), [1])
        self.assertEqual(_cli.find_list_or_none({"result": [2]}, "strategies"), [2])

    def test_find_list_keeps_its_forgiving_contract(self):
        # The lenient helper is unchanged — callers that legitimately want [] still get it.
        self.assertEqual(_cli.find_list({"ok": True}, "strategies"), [])
        self.assertEqual(_cli.find_list({"strategies": [1]}, "strategies"), [1])


class ListStrategiesOrNone(unittest.TestCase):
    """The fail-CLOSED strategy read. Both no-answer shapes return the SAME sentinel, because the
    caller's verdict is the same for both: render nothing, say the surface was unreadable."""

    def test_a_transport_failure_is_none_instead_of_reading_as_empty(self):
        self.assertIsNone(_cli.list_strategies_or_none(FakeMCP(raises=RuntimeError("no SENPI_AUTH_TOKEN"))))

    def test_an_unnavigable_payload_is_none(self):
        # The half that main's version missed: it routed through `find_list`, so a drifted shape
        # came back `[]` and read as "nothing is funded here" on a money path.
        self.assertIsNone(_cli.list_strategies_or_none(FakeMCP(payload={"ok": True, "records": {"count": 0}})))

    def test_a_genuinely_empty_list_is_an_answer_not_a_failure(self):
        self.assertEqual(_cli.list_strategies_or_none(FakeMCP(payload={"strategies": []})), [])

    def test_the_status_filter_is_forwarded_server_side(self):
        mcp = FakeMCP(payload={"strategies": []})
        _cli.list_strategies_or_none(mcp, statuses=_cli.LIVE_STATUSES)
        self.assertEqual(mcp.calls[0][1]["status"], _cli.LIVE_STATUSES)

    def test_the_lenient_lister_still_degrades(self):
        # `list_strategies` is unchanged: status.py / close.py keep their current behaviour.
        self.assertEqual(_cli.list_strategies(FakeMCP(raises=RuntimeError("boom"))), [])

    def test_why_carries_the_transport_cause_for_a_caller_that_renders_it(self):
        # One sentinel for both modes, but "could not check" is an operator-facing line: the cause
        # is what tells them whether to fix a token or report a payload drift.
        why = []
        _cli.list_strategies_or_none(FakeMCP(raises=RuntimeError("no SENPI_AUTH_TOKEN")), why=why)
        self.assertIn("no SENPI_AUTH_TOKEN", why[0])

    def test_why_distinguishes_the_unnavigable_payload(self):
        why = []
        _cli.list_strategies_or_none(FakeMCP(payload={"ok": True}), why=why)
        self.assertIn("no recognisable strategies list", why[0])

    def test_why_stays_empty_when_the_read_answered(self):
        why = []
        _cli.list_strategies_or_none(FakeMCP(payload={"strategies": []}), why=why)
        self.assertEqual(why, [])


class StrategiesForOrNone(unittest.TestCase):
    """The filtered read inherits the sentinel — a money path must never see `[]` for an unread list."""

    def test_a_transport_failure_is_none(self):
        self.assertIsNone(_cli.strategies_for_or_none(FakeMCP(raises=RuntimeError("boom")), skill_name="spider"))

    def test_an_unnavigable_payload_is_none(self):
        self.assertIsNone(_cli.strategies_for_or_none(FakeMCP(payload={"ok": True}), skill_name="spider"))

    def test_no_match_in_a_readable_list_is_an_empty_answer(self):
        mcp = FakeMCP(payload={"strategies": [{"strategyMetadata": {"skillName": "polar"}}]})
        self.assertEqual(_cli.strategies_for_or_none(mcp, skill_name="spider"), [])

    def test_the_fail_open_twin_still_degrades_to_empty(self):
        # `strategies_for` is the fail-OPEN reader for reads that only ADD work. Unchanged.
        self.assertEqual(_cli.strategies_for(FakeMCP(raises=RuntimeError("boom")), skill_name="spider"), [])

    def test_a_mixed_case_package_id_still_finds_its_own_stamped_wallet(self):
        # The teardown that reported a false all-clear. The verb stamps `pkg.id` VERBATIM and the
        # backend stores it case-normalized, so a package id `Warpath` funds a wallet that reads back
        # stamped "warpath". Under an exact compare `close.py Warpath` matched nothing and printed
        # "no OPEN strategies to close." while the wallet stayed live, funded and trading.
        live = {"id": "sid-1", "status": "ACTIVE", "strategyMetadata": {"skillName": "warpath"}}
        mcp = FakeMCP(payload={"strategies": [live]})
        self.assertEqual(_cli.strategies_for_or_none(mcp, skill_name="Warpath"), [live])
        # …and the other direction, for a record the backend handed back with capitals intact.
        loud = {"id": "sid-2", "status": "ACTIVE", "strategyMetadata": {"skillName": "  WARPATH  "}}
        mcp = FakeMCP(payload={"strategies": [loud]})
        self.assertEqual(_cli.strategies_for(mcp, skill_name="warpath"), [loud])

    def test_another_packages_wallet_is_still_not_this_packages(self):
        # Case-folding widened the match; it must not have widened it past the package boundary.
        mcp = FakeMCP(payload={"strategies": [{"strategyMetadata": {"skillName": "Polar"}}]})
        self.assertEqual(_cli.strategies_for_or_none(mcp, skill_name="warpath"), [])


class StrategyFunded(unittest.TestCase):
    def test_the_backends_own_figure_is_rendered(self):
        self.assertEqual(_cli.strategy_funded({"totalFunded": 300}), "$300")
        self.assertEqual(_cli.strategy_funded({"netFunded": 42.5}), "$42.5")

    def test_a_requested_budget_is_never_reported_as_funded(self):
        # `initialBudget` is what was ASKED FOR. Printing it as funded is how a $500 request over a
        # $60 partial fund reads as fully funded.
        self.assertIsNone(_cli.strategy_funded({"initialBudget": 500}))

    def test_an_unreadable_record_is_none(self):
        self.assertIsNone(_cli.strategy_funded({}))
        self.assertIsNone(_cli.strategy_funded({"totalFunded": "n/a"}))


class StrategyActive(unittest.TestCase):
    def test_only_active_is_trading(self):
        self.assertTrue(_cli.strategy_active({"status": "ACTIVE"}))
        for status in ("PAUSED", "CLOSING_POSITIONS", "CREATE_WALLET", "FUND_WALLET",
                       "INITIALIZE_POSITIONS", "CLOSED", ""):
            self.assertFalse(_cli.strategy_active({"status": status}), status)

    def test_every_transitional_status_is_still_open(self):
        # `strategy_active` narrows the steer; it must not redefine what counts as live/dead.
        for status in ("PAUSED", "CLOSING_POSITIONS"):
            self.assertTrue(_cli.strategy_open({"status": status}), status)


class StrategyName(unittest.TestCase):
    """The name a strategy was created under — what `verify` matches an instance back to its wallet by."""

    def test_a_present_but_null_name_falls_through_to_the_next_key(self):
        # The MCP now SELECTS `strategyName`, so the key is present on every row — and the column is
        # NULLABLE (null on 21 of 23 rows in a live sample). `dig` answers with the first key that
        # EXISTS, null included, so a present-null would answer for the whole chain and switch off
        # the `tradingStrategyName` fallback that is the only name most rows carry. Silence at one
        # leg must not be an answer for the legs behind it.
        self.assertEqual(_cli.strategy_name(
            {"strategyName": None, "tradingStrategyName": "spider"}), "spider")
        self.assertEqual(_cli.strategy_name(
            {"strategyName": "", "tradingStrategyName": "spider"}), "spider")

    def test_a_name_that_was_written_still_wins_over_the_fallbacks(self):
        self.assertEqual(_cli.strategy_name(
            {"strategyName": "spider-swing", "tradingStrategyName": "spider"}), "spider-swing")

    def test_silence_at_every_leg_is_none(self):
        self.assertIsNone(_cli.strategy_name({"strategyName": None, "tradingStrategyName": None}))
        self.assertIsNone(_cli.strategy_name({"strategyName": "", "name": ""}))
        self.assertIsNone(_cli.strategy_name({}))


class StrategyNameMatch(unittest.TestCase):
    """Is this live strategy the one this instance derives a name for?

    ONE producer for the comparison, because the runtime's deploy verb asks the same question of the
    same field and the two answering differently is how a wallet gets funded twice."""

    def test_the_compare_is_case_insensitive(self):
        # The backend case-normalizes what it stores ("WARPATH" in, "warpath" back) while
        # `_sanitize_strategy_name` deliberately preserves capitals — so a mixed-case package id
        # derives a name a case-SENSITIVE compare could never match.
        self.assertTrue(_cli.strategy_name_match("Spider-Swing", "spider-swing"))
        self.assertTrue(_cli.strategy_name_match("  spider  ", "SPIDER"))

    def test_two_absences_are_not_an_identity(self):
        # An unnamed strategy would otherwise bind to the first instance that asked.
        for a, b in ((None, None), ("", ""), ("spider", None), (None, "spider"), ("spider", "  ")):
            self.assertFalse(_cli.strategy_name_match(a, b), (a, b))

    def test_different_names_still_do_not_match(self):
        self.assertFalse(_cli.strategy_name_match("spider-swing", "spider-scalp"))


class StrategySkillMatch(unittest.TestCase):
    """Does this wallet's attribution stamp name this package? Same normalisation as the name
    compare, and for the same reason: the runtime reads the stamp as `.trim().toLowerCase()` while
    stamping `pkg.id` verbatim, so an exact compare here diverges from the layer that wrote it."""

    def test_the_compare_is_case_and_whitespace_insensitive(self):
        self.assertTrue(_cli.strategy_skill_match("warpath", "Warpath"))
        self.assertTrue(_cli.strategy_skill_match("  WARPATH ", "warpath"))

    def test_two_absences_are_not_an_identity(self):
        # An unattributed strategy must never be handed to a teardown that asked about one package.
        for a, b in ((None, None), ("", ""), ("spider", None), (None, "spider"), ("spider", "  ")):
            self.assertFalse(_cli.strategy_skill_match(a, b), (a, b))

    def test_a_different_package_is_still_a_different_package(self):
        self.assertFalse(_cli.strategy_skill_match("spider", "spider-swing"))
        self.assertFalse(_cli.strategy_skill_match("Polar", "warpath"))


class StrategySkillDeclared(unittest.TestCase):
    """The reader that decides whether a wallet belongs to SOMEONE ELSE. It must never guess."""

    def test_a_written_attribution_is_read_from_either_shape(self):
        self.assertEqual(_cli.strategy_skill_declared(
            {"strategyName": "spider-swing", "strategyMetadata": {"skillName": "spider"}}), "spider")
        self.assertEqual(_cli.strategy_skill_declared(
            {"strategyName": "spider-swing", "skillName": "spider"}), "spider")

    def test_silence_is_none_and_never_the_strategy_name(self):
        # `strategy_skill` guesses the NAME when nobody attributed — usable for filing, fatal for
        # exclusion: an unattributed wallet named `spider-swing` would read as owned by a package
        # called `spider-swing` and be dropped out of `verify spider`'s match.
        record = {"strategyName": "spider-swing", "tradingStrategyName": "spider-swing"}
        self.assertEqual(_cli.strategy_skill(record), "spider-swing")
        self.assertIsNone(_cli.strategy_skill_declared(record))
        self.assertIsNone(_cli.strategy_skill_declared({"strategyMetadata": {}}))

    def test_an_empty_stamp_is_silence_at_every_leg(self):
        # An effectively-silent attribution read verbatim is a FOREIGN owner to every caller that
        # compares it against a package id — so the user's own live funded wallet drops out of the
        # match and the check reports the name as "attributed to" ''.
        for record in ({"strategyName": "spider", "skillName": ""},
                       {"strategyName": "spider", "strategyMetadata": {"skillName": ""}},
                       {"strategyName": "spider", "strategyMetadata": {"skillName": ""},
                        "skillName": ""}):
            self.assertIsNone(_cli.strategy_skill_declared(record), record)
        # The metadata leg being empty must not hide a top-level stamp that WAS written.
        self.assertEqual(_cli.strategy_skill_declared(
            {"strategyMetadata": {"skillName": ""}, "skillName": "polar"}), "polar")
        # And `strategy_skill`'s filing answer still falls through to the name.
        self.assertEqual(_cli.strategy_skill({"strategyName": "spider", "skillName": "",
                                              "tradingStrategyName": "spider"}), "spider")

    def test_a_json_encoded_metadata_payload_is_parsed_not_skipped(self):
        # The MCP passes the backend's strategyMetadata scalar through verbatim, so a string-shaped
        # payload is the backend's shape, not the MCP's contract. Skipped, a genuinely foreign wallet
        # reads as unattributed → not foreign → ADOPTED: the cross-package adoption 40df6a2b fixed,
        # resurfacing on a response-shape drift.
        self.assertEqual(_cli.strategy_skill_declared(
            {"strategyName": "spider-swing",
             "strategyMetadata": '{"skillName": "spider-swing", "skillVersion": "1.0.0"}'}),
            "spider-swing")
        # Unparseable or non-object stays None — a shape nobody can read is not an owner.
        self.assertIsNone(_cli.strategy_skill_declared(
            {"strategyName": "spider", "strategyMetadata": "{not json"}))
        self.assertIsNone(_cli.strategy_skill_declared(
            {"strategyName": "spider", "strategyMetadata": '"spider"'}))


class RunState(unittest.TestCase):
    """The run/job state, for QUOTING beside a verdict `health_verdict` reached without a health
    field. It is evidence, never health — a caller that prints it under the word "health" is how an
    unproven runtime reads as a healthy one."""

    def test_reads_either_run_state_key(self):
        self.assertEqual(_cli.run_state({"name": "spider-main", "status": "failed"}), "failed")
        self.assertEqual(_cli.run_state({"state": {"overall": "live"}}), "live")

    def test_no_run_state_at_all_is_none(self):
        self.assertIsNone(_cli.run_state({"name": "spider-main"}))
        self.assertIsNone(_cli.run_state({}))

    def test_it_is_not_health_and_never_promotes(self):
        # The pair the verify row depends on: a bare "running" carries no verdict beyond `unknown`,
        # while a broken run state downgrades — that asymmetry is what makes quoting it safe.
        self.assertEqual(_cli.run_state({"status": "running"}), "running")
        self.assertEqual(_cli.health_verdict({"status": "running"}), "unknown")
        self.assertEqual(_cli.health_verdict({"status": "failed"}), "unhealthy")
        self.assertEqual(_cli.health_verdict({"status": "degraded"}), "degraded")


class CliJson(unittest.TestCase):
    """A CLI call's exit code and its stdout answer different questions — `deploy status --json`
    exits with the JOB's own verdict code (e.g. `E_VALIDATE_NO_PROOF` is rc=6), not a transport
    code, so a refusal still prints a complete JSON report. Gating on `rc != 0` before parsing
    discarded that report unread, which is exactly the class of surface this reader exists to
    keep readable (mirrors `deploy.py`'s own `read_status`, which already ignores rc for this
    reason)."""

    def setUp(self):
        self._real_run_cli = _cli.run_cli

    def tearDown(self):
        _cli.run_cli = self._real_run_cli

    def test_a_refusal_with_a_valid_payload_is_still_parsed(self):
        _cli.run_cli = lambda args, timeout=60: (6, json.dumps({"code": "E_VALIDATE_NO_PROOF"}), "")
        self.assertEqual(_cli.cli_json(["openclaw", "senpi", "deploy", "status", "--json"]),
                          {"code": "E_VALIDATE_NO_PROOF"})

    def test_a_transport_failure_with_no_output_is_still_none(self):
        # rc != 0 is not itself proof of a payload — an empty capture (spawn failure, timeout) must
        # still degrade to None. Only the earlier "rc != 0 alone" gate is retired, not fail-closed
        # behaviour on a genuinely empty read.
        _cli.run_cli = lambda args, timeout=60: (-1, "", "command not found: openclaw")
        self.assertIsNone(_cli.cli_json(["openclaw", "senpi", "status", "--json"]))

    def test_unparseable_output_is_still_none(self):
        _cli.run_cli = lambda args, timeout=60: (0, "not json", "")
        self.assertIsNone(_cli.cli_json(["openclaw", "senpi", "status", "--json"]))


class RunCliSpawnFailures(unittest.TestCase):
    """`run_cli` is the bottom of every lifecycle script — deploy, close, status all read through it.
    An exception escaping HERE takes the caller's whole run with it, mid-money-path, so every way a
    spawn can fail has to come back as the rc=-1 never-ran verdict instead. Catching only
    FileNotFoundError left the fork/exec failures a strained box actually produces (ENOMEM, EAGAIN)
    propagating."""

    def setUp(self):
        self._real = _cli.subprocess.Popen

    def tearDown(self):
        _cli.subprocess.Popen = self._real

    def _raise(self, exc):
        def boom(*_a, **_kw):
            raise exc
        _cli.subprocess.Popen = boom

    def test_a_fork_failure_is_the_never_ran_verdict_not_an_exception(self):
        self._raise(OSError(12, "Cannot allocate memory"))
        rc, out, err = _cli.run_cli(["openclaw", "senpi", "validate", "/pkg"])
        self.assertEqual((rc, out), (-1, ""))
        self.assertTrue(err.startswith(_cli.SPAWN_FAILED_PREFIX))   # never ran — the money-path branch
        self.assertIn("Cannot allocate memory", err)                # …and the cause survives to the log

    def test_a_missing_binary_still_reads_exactly_as_before(self):
        self._raise(FileNotFoundError())
        self.assertEqual(_cli.run_cli(["openclaw", "senpi", "validate", "/pkg"]),
                         (-1, "", f"{_cli.SPAWN_FAILED_PREFIX}openclaw"))

    def test_a_timeout_is_still_the_stopped_waiting_verdict(self):
        # The distinction the prefix exists for: a timeout RAN, so whatever it dispatched may still be
        # in flight. It must not pick up the never-ran prefix now that the OSError arm is broader.
        # A real child that outlives the budget: the timeout path kills its group and reaps it.
        t0 = time.time()
        rc, out, err = _cli.run_cli([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        self.assertLess(time.time() - t0, 10)
        self.assertEqual((rc, out), (-1, ""))
        self.assertFalse(err.startswith(_cli.SPAWN_FAILED_PREFIX))
        self.assertIn("timed out after 0.5s", err)


def _gw(kind, payload=None, message=None):
    return {"kind": kind, "payload": payload, "message": message, "code": None, "rc": 0, "stderr": "",
            "argv": [], "elapsed": 0.1, "transport": None}


class RuntimeInventoryEmptyVsUnreadable(unittest.TestCase):
    """`list_runtimes_or_none` -> rows | None, straight from `senpi.listRuntimes`. None is "I could not
    read the inventory", [] is "no runtimes". A box with NO runtimes answers `{"runtimes": []}`, and
    that has to come back as [] : `deploy.py verify` raises ReadFailed ("COULD NOT CHECK") on None,
    which made it structurally unable to answer for a strategy whose runtime was never installed."""

    def _with(self, answer):
        return mock.patch.object(_cli, "gateway_call", lambda *a, **k: answer)

    def test_an_empty_inventory_is_readable_and_empty(self):
        with self._with(_gw("ok", {"runtimes": []})):
            self.assertEqual(_cli.list_runtimes_or_none(), [])

    def test_a_populated_inventory_keeps_every_row_and_names_it_by_id(self):
        payload = {"runtimes": [
            {"id": "badger-main", "wallet": "0x74b4000000000000000000000000000000005a41",
             "source": "(inline)", "status": "running"},
            {"id": "otter-main", "wallet": "0x56fc000000000000000000000000000000000ea2",
             "source": "(inline)", "status": "running — NO ENTRY SCANNERS"}]}
        with self._with(_gw("ok", payload)):
            rows = _cli.list_runtimes_or_none()
        self.assertEqual([r["name"] for r in rows], ["badger-main", "otter-main"])
        self.assertEqual(rows[0]["wallet"], "0x74b4000000000000000000000000000000005a41")   # full address
        self.assertEqual(rows[1]["status"], "running — NO ENTRY SCANNERS")
        self.assertTrue(_cli.runtime_no_entry_scanners(rows[1]))

    def test_an_answer_without_a_runtimes_list_is_unreadable(self):
        # A payload this reader cannot navigate must stay None, never a confident "no runtimes".
        for payload in ({}, {"runtimes": None}, {"runtimes": "none"}, ["badger-main"]):
            with self._with(_gw("ok", payload)):
                self.assertIsNone(_cli.list_runtimes_or_none(), payload)

    def test_a_failed_read_is_unreadable_and_says_why(self):
        for kind in ("timeout", "transport", "unknown_method", "error", "spawn_failed", "unreadable"):
            why = []
            with self._with(_gw(kind, message="boom")):
                self.assertIsNone(_cli.list_runtimes_or_none(why=why), kind)
            self.assertIn(f"senpi.listRuntimes: {kind}", why[0])

    def test_the_plain_list_reader_degrades_to_empty(self):
        # `list_runtimes` is for reads that only ADD information; the money path uses `_or_none`.
        with self._with(_gw("timeout")):
            self.assertEqual(_cli.list_runtimes(), [])

    def test_the_inventory_is_one_list_runtimes_call_with_its_own_budget(self):
        calls = []

        def fake(method, params=None, **k):
            calls.append((method, params, k))
            return _gw("ok", {"runtimes": []})
        with mock.patch.object(_cli, "gateway_call", fake):
            _cli.list_runtimes_or_none()
        self.assertEqual(calls, [("senpi.listRuntimes", {}, {"rpc_timeout_ms": 15000, "timeout_s": 25})])


class HealthAndDescriptorsReadTheGateway(unittest.TestCase):
    """status.py's batched reads: one `senpi.getHealthStatus` for every runtime's health, and the
    descriptors from the `senpi.listRuntimes` rows. No `openclaw senpi` CLI verb on either."""

    def test_health_map_keys_by_runtime_name_and_retries_an_empty_answer_once(self):
        answers = [_gw("ok", {"statuses": []}),
                   _gw("ok", {"statuses": [{"runtimeId": "spider-main", "health": "healthy"}]})]
        calls = []

        def fake(method, params=None, **k):
            calls.append((method, params))
            return answers.pop(0)
        with mock.patch.object(_cli, "gateway_call", fake):
            hmap = _cli.runtime_health_map()
        self.assertEqual(list(hmap), ["spider-main"])
        self.assertEqual(calls, [("senpi.getHealthStatus", {})] * 2)

    def test_health_map_is_empty_when_unreadable(self):
        with mock.patch.object(_cli, "gateway_call", lambda *a, **k: _gw("timeout")):
            self.assertEqual(_cli.runtime_health_map(), {})

    def test_descriptors_come_from_the_list_runtimes_rows(self):
        desc = {"name": "spider-main", "recipeHash": "abc"}
        answer = _gw("ok", {"runtimes": [{"id": "spider-main", "status": "running", "descriptor": desc},
                                         {"id": "bare-main", "status": "stopped"}]})
        with mock.patch.object(_cli, "gateway_call", lambda *a, **k: answer):
            self.assertEqual(_cli.runtime_descriptors(), {"spider-main": desc})


if __name__ == "__main__":
    unittest.main()
