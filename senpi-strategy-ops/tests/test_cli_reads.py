#!/usr/bin/env python3
"""Hermetic tests for the `_cli` READ layer — the helpers every lifecycle script quotes from.

Two contracts live here, both of them about not answering a question the surface never answered:

  * **unreadable != empty.** `find_list`/`list_strategies` degrade to `[]` on a payload they cannot
    navigate, which reads as "nothing is deployed" at every call site that trusts them. The strict
    pair (`find_list_or_none` / `list_strategies_strict`) keeps the two apart.
  * **a requested amount is never a funded one.** `strategy_funded` reports the backend's own
    figure or nothing at all.

Run:  python3 senpi-strategy-ops/tests/test_cli_reads.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import unittest
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


class ListStrategiesStrict(unittest.TestCase):
    def test_a_transport_failure_raises_instead_of_reading_as_empty(self):
        mcp = FakeMCP(raises=RuntimeError("no SENPI_AUTH_TOKEN"))
        with self.assertRaises(_cli.ReadFailed) as ctx:
            _cli.list_strategies_strict(mcp)
        self.assertIn("strategy_list", str(ctx.exception))
        self.assertIn("no SENPI_AUTH_TOKEN", str(ctx.exception))

    def test_an_unnavigable_payload_raises(self):
        with self.assertRaises(_cli.ReadFailed) as ctx:
            _cli.list_strategies_strict(FakeMCP(payload={"ok": True, "records": {"count": 0}}))
        self.assertIn("strategy_list", str(ctx.exception))

    def test_a_genuinely_empty_list_is_returned(self):
        self.assertEqual(_cli.list_strategies_strict(FakeMCP(payload={"strategies": []})), [])

    def test_the_status_filter_is_forwarded_server_side(self):
        mcp = FakeMCP(payload={"strategies": []})
        _cli.list_strategies_strict(mcp, statuses=_cli.LIVE_STATUSES)
        self.assertEqual(mcp.calls[0][1]["status"], _cli.LIVE_STATUSES)

    def test_the_lenient_lister_still_degrades(self):
        # `list_strategies` is unchanged: status.py / close.py keep their current behaviour.
        self.assertEqual(_cli.list_strategies(FakeMCP(raises=RuntimeError("boom"))), [])


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


if __name__ == "__main__":
    unittest.main()
