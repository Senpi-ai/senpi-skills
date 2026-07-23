#!/usr/bin/env python3
"""Offline tests for the `idle` step — "why hasn't it traded?" (the #1 support question by volume).

Pure: `idle_verdict` takes already-fetched events, so there is NO MCP and NO subprocess here.

This lives in senpi-portfolio, not senpi-improve-trades, because it is a LIVE-STATE question — "is this
strategy doing its job right now" — and this skill owns the mandate that defines the job. improve-trades
is the retrospective counterpart ("how did my CLOSED trades do").

The verdict that matters most is the one that ISN'T alarming: a strategy that is scanning and simply
hasn't found a setup must read `waiting`, never `broken`. Getting that backwards is how users tear down
working strategies (the LION incidents), so it has its own test.

    python3 -m pytest senpi-portfolio/tests/test_idle.py
    python3 senpi-portfolio/tests/test_idle.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import portfolio  # noqa: E402


def _ev(name, ts=1000, **attrs):
    """An event-ring entry; kwargs become dotted senpi.* attrs (`__` → `.`)."""
    return {"name": name, "ts": ts,
            "attrs": {"senpi." + k.replace("__", "."): v for k, v in attrs.items()}}


def _sig(result, code, asset="SOL", score=7.0):
    return _ev("signal.outcome", outcome__result=result, outcome__reason_code=code,
               signal__asset=asset, signal__direction="LONG", signal__score=score)


def _strat(label="s", status="ACTIVE", runtime_id="s-main", mandate=None):
    return {"label": label, "status": status, "runtime_id": runtime_id,
            "wallet": "0x" + "a" * 40, "mandate": mandate}


class Verdicts(unittest.TestCase):
    def test_blocked_every_candidate_rejected(self):
        """THE ibis case: the scanner produced candidates and the runtime rejected all of them.
        Invisible on every other surface — liveness said healthy while it was 100% dead."""
        events = [_sig("rejected", "schema_invalid", a) for a in ("SOL", "BTC", "ETH")]
        r = portfolio.idle_verdict(_strat("ibis"), events, True)
        self.assertEqual(r["verdict"], "blocked")
        self.assertEqual(r["signals"]["rejected"], 3)
        self.assertEqual(r["reason_codes"]["schema_invalid"], 3)
        self.assertIn("schema_invalid", r["verdict_detail"])
        self.assertEqual(len(r["sample_rejections"]), 3)

    def test_waiting_is_not_broken(self):
        """Selective-by-design must NEVER read as broken — the safety-critical branch."""
        r = portfolio.idle_verdict(_strat("tides"), [_ev("scan.tick"), _ev("scan.tick")], True)
        self.assertEqual(r["verdict"], "waiting")
        self.assertIn("selective", r["verdict_detail"])
        self.assertIn("not broken", r["verdict_detail"])

    def test_blocked_reports_the_dominant_reason(self):
        events = [_sig("blocked", "no_slots"), _sig("blocked", "no_slots"),
                  _sig("blocked", "risk_gate_leverage")]
        r = portfolio.idle_verdict(_strat(), events, True)
        self.assertEqual(r["verdict"], "blocked")
        self.assertIn("no_slots", r["verdict_detail"])       # the MOST common, not just the first
        self.assertEqual(r["reason_codes"], {"no_slots": 2, "risk_gate_leverage": 1})

    def test_traded_contradicts_the_premise(self):
        r = portfolio.idle_verdict(_strat(), [_ev("position.opened"), _sig("accepted", "submitted")], True)
        self.assertEqual(r["verdict"], "traded")
        self.assertEqual(r["positions_opened"], 1)

    def test_accepted_but_nothing_opened(self):
        r = portfolio.idle_verdict(_strat(), [_sig("accepted", "submitted"), _ev("order.failed")], True)
        self.assertEqual(r["verdict"], "accepted_no_open")
        self.assertEqual(r["orders_failed"], 1)

    def test_funded_without_a_runtime(self):
        self.assertEqual(portfolio.idle_verdict(_strat(runtime_id=None), [], True)["verdict"], "no_runtime")

    def test_no_events_is_silent_not_broken(self):
        r = portfolio.idle_verdict(_strat(), [], True)
        self.assertEqual(r["verdict"], "silent")
        self.assertIn("cannot confirm", r["verdict_detail"])

    def test_unreadable_telemetry_fails_open(self):
        """An unreadable event log is NOT evidence of a fault — never diagnose from absence."""
        r = portfolio.idle_verdict(_strat(), [], False)
        self.assertEqual(r["verdict"], "unknown")
        self.assertIn("NOT evidence", r["verdict_detail"])

    def test_closed_strategy_is_history(self):
        self.assertEqual(portfolio.idle_verdict(_strat(status="CLOSED"), [], True)["verdict"], "not_current")

    def test_verdict_carries_the_mandate(self):
        """The reason this belongs in portfolio: the mandate defines what 'doing its job' means, so a
        `waiting` verdict can be narrated against the strategy's own design."""
        r = portfolio.idle_verdict(_strat(mandate="selective regime-switcher, ~3 trades/week"),
                                   [_ev("scan.tick")], True)
        self.assertEqual(r["verdict"], "waiting")
        self.assertIn("regime-switcher", r["mandate"])

    def test_last_event_ts_is_the_newest(self):
        events = [_ev("scan.tick", ts=100), _ev("scan.tick", ts=900), _ev("scan.tick", ts=500)]
        self.assertEqual(portfolio.idle_verdict(_strat(), events, True)["last_event_ts"], 900)


class FanOut(unittest.TestCase):
    def test_reads_preserve_order_and_skip_closed(self):
        strats = [_strat("a"), _strat("b", status="CLOSED", runtime_id=None), _strat("c")]
        meta = {"warnings": []}
        os.environ["SENPI_EVENTS_FIXTURE"] = os.path.join(HERE, "fixtures", "idle_events.json")
        try:
            reads = portfolio.idle_reads(strats, meta)
        finally:
            os.environ.pop("SENPI_EVENTS_FIXTURE", None)
        self.assertEqual([r["label"] for r in reads], ["a", "b", "c"])
        self.assertEqual(reads[1]["verdict"], "not_current")   # closed never probed

    def test_empty_strategy_list_is_safe(self):
        self.assertEqual(portfolio.idle_reads([], {"warnings": []}), [])


class StepRegistration(unittest.TestCase):
    def test_idle_is_a_registered_step(self):
        self.assertIn("idle", portfolio._STEPS)
        self.assertIn("idle", portfolio._STEP_FNS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
