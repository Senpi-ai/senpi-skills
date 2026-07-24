#!/usr/bin/env python3
"""`meta.book_state` — the routing signal that decides the answer when there's nothing to review.

Pure: no MCP, no subprocess.

The two empty cases need OPPOSITE responses. Nothing deployed → pivot to market-pulse +
strategy-discover. Deployed-but-idle → diagnose the strategy they already have. Telling someone whose
funded strategy is silently blocked to go find *another* strategy is the worst available answer, so the
split is computed in the engine rather than left to narration — and asserted here.

    python3 -m pytest senpi-improve-trades/tests/test_book_state.py
    python3 senpi-improve-trades/tests/test_book_state.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import review  # noqa: E402


class BookState(unittest.TestCase):
    def test_no_strategies_pivots_to_the_market(self):
        state, nxt = review._book_state(0, 0, False)
        self.assertEqual(state, "no_strategies")
        self.assertIn("market-pulse", nxt)
        self.assertIn("strategy-discover", nxt)

    def test_deployed_but_idle_diagnoses_instead_of_pitching(self):
        state, nxt = review._book_state(2, 0, False)
        self.assertEqual(state, "strategies_no_trades")
        self.assertIn("Do NOT pitch another strategy", nxt)
        self.assertNotIn("discover", nxt)      # never sell into an unanswered "why is mine idle?"

    def test_has_trades_is_a_normal_review(self):
        self.assertEqual(review._book_state(2, 11, False)[0], "has_trades")

    def test_unreadable_list_is_never_an_empty_book(self):
        """A token/scope failure must NOT read as 'you have no strategies'."""
        state, nxt = review._book_state(0, 0, True)
        self.assertEqual(state, "unknown")
        self.assertIn("TOKEN", nxt)
        self.assertIn("never", nxt.lower())

    def test_stamped_on_every_output_path(self):
        for meta, want in (({"strategy_count": 0, "trade_count": 0, "warnings": []}, "no_strategies"),
                           ({"strategy_count": 2, "trade_count": 0, "warnings": []}, "strategies_no_trades"),
                           ({"strategy_count": 2, "trade_count": 9, "warnings": []}, "has_trades"),
                           ({"strategy_count": 0, "trade_count": 0,
                             "warnings": ["strategy_list failed: 401"]}, "unknown")):
            out = review._slim_for_context({"trades": [], "meta": dict(meta)})
            self.assertEqual(out["meta"]["book_state"], want)
            self.assertIn("next_action", out["meta"])

    def test_full_output_also_gets_book_state(self):
        """--full bypasses the trimming, not the stamping."""
        out = review._slim_for_context({"trades": [], "meta": {"strategy_count": 0, "trade_count": 0}},
                                       full=True)
        self.assertEqual(out["meta"]["book_state"], "no_strategies")

    def test_absent_counts_leave_book_state_unset(self):
        """Never guess a state from a meta that didn't report counts."""
        out = review._slim_for_context({"trades": [], "meta": {"warnings": []}})
        self.assertNotIn("book_state", out["meta"])

    def test_empty_book_is_not_reported_as_an_auth_fault(self):
        """A brand-new user's normal state must not read as a token problem (it used to)."""
        _state, nxt = review._book_state(0, 0, False)
        self.assertNotIn("USER-scoped", nxt)
        self.assertNotIn("token", nxt.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
