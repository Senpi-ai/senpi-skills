#!/usr/bin/env python3
"""A live strategy that has never traded must get an answer, not "being selective".

Proving liveness (runtime running, scanner heartbeating, ticks landing `ok`) is where the
existing decision tree stops. It is not what a funded user asked. Seen live 2026-09-24: two
funded strategies on one account scanned clean for ~10 h and opened nothing, while the
scanners themselves logged the reason on every tick — the failing gate, its threshold, and
how many instruments they had scanned:

    [<pkg>.scan] WAITING — no forced-flow / liquidation-unwind signature (min score 5); scanned=4
    [<pkg>.scan] SOL HOLD (gate): 4h=BULLISH 80% (need>=75% & directional)

A 4-instrument universe crossed with a rare signature is a strategy working exactly as
written that will realistically never fire. None of the surfaces the liveness doc enumerates
carry that: `senpi scanner` flags `(no signals yet)`, which is *that*, never *why*. So the
answer ended at "it's live and being selective" — repeated across four digest windows for the
same account, which reads as a brush-off from a system holding their money.

Guards that the answer path is taught: the binding gate + threshold, the universe as a count,
the emit history, the two levers, and the selective-vs-unreachable split (an unreachable gate
is broken, and waiting never fixes it — see test_entry_gate_reachability.py).

Run:
    python3 senpi-strategy-ops/tests/test_never_traded_answer.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parents[1]
SKILL = OPS / "SKILL.md"
LIVENESS = OPS / "references" / "liveness-verification.md"


class TheNeverTradedAnswerIsTaught(unittest.TestCase):
    def test_the_resident_line_refuses_being_selective_as_an_answer(self):
        """It has to fire without opening a reference — this is the whole user-facing miss."""
        text = SKILL.read_text()
        for needle in ("has never traded", "binding gate", "universe count",
                       'never "being selective"'):
            self.assertIn(needle, text, needle)

    def test_the_answer_names_gate_threshold_universe_and_emit_history(self):
        liveness = LIVENESS.read_text()
        for needle in ("Healthy, live — and it has never traded",
                       "Name the binding gate and its threshold",
                       "State the universe it actually scans, as a count",
                       "Say how often it has fired",
                       "`signalsProduced`",
                       "senpi events -r"):
            self.assertIn(needle, liveness, needle)

    def test_being_selective_is_named_as_not_an_answer(self):
        """"Selective" is the phrasing the agent actually reached for; name and refuse it."""
        # Whitespace-normalised: a reflow of the paragraph must not fail this guard.
        liveness = " ".join(LIVENESS.read_text().split())
        self.assertIn("is the end of the liveness check, **not** an answer", liveness)
        self.assertIn("(no signals yet)", liveness)

    def test_the_two_levers_are_given_and_recreate_is_still_refused(self):
        """Widening the universe / loosening the gate — applied in place, never close-and-recreate
        (a new strategy wallet costs a real creation fee)."""
        liveness = LIVENESS.read_text()
        self.assertIn("widen the universe", liveness.lower())
        self.assertIn("loosen the binding gate", liveness.lower())
        self.assertIn("never close and recreate", liveness)

    def test_selective_is_distinguished_from_unreachable(self):
        """The dangerous half: an unreachable gate is broken, and patience never fixes it."""
        liveness = LIVENESS.read_text()
        for needle in ("Selective vs. unreachable", "it is **broken**",
                       "test_entry_gate_reachability.py", "UNPROVEN"):
            self.assertIn(needle, liveness, needle)

    def test_no_user_identifying_detail_leaked_into_the_public_repo(self):
        """senpi-skills is public: no MIDs, no wallet addresses in the teaching or this guard."""
        import re
        for path in (SKILL, LIVENESS, Path(__file__)):
            text = path.read_text()
            self.assertNotRegex(text, r"\bM\d{5,}\b", f"MID-shaped token in {path.name}")
            self.assertNotRegex(text, r"\b0x[0-9a-fA-F]{40}\b", f"wallet address in {path.name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
