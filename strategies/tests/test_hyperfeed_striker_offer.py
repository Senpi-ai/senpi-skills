"""The Hyperfeed strikers must stay offered on every surface that recommends a strategy.

Penguin and Pelican are the templates users fork most, and the reason is that a live rotation
shows up fast enough for someone to see what the machinery does before they have a thesis of
their own. So the fleet's recommendation surfaces name them by hand, in each skill's own
`Mandatory closing` — the same idiom that already names `whalehunter` in senpi-smart-money.
Hand-written copy in nine files rots silently: one refactor of a closing section and a surface
stops offering them with nothing failing. These needles are the guard.

Two claims in the copy are load-bearing and easy to get wrong, so they are pinned too:

  ROTATIONS, NOT PUMPS. The detector fires on a fresh 15+ rank jump out of the deep field of the
  top-50 leaderboard rows — a jump in what winning traders HOLD, confirmed by trend, freshness
  and a volume spike. It is not a price-pump detector: a name can rank-jump without pumping, and
  a pumping name no smart money rotated into does not fire at all. Copy that says "pump" recruits
  price-chasers who are then confused by their first entry.

  THE WALLET NUMBER, AND "EACH". The stop is 15% ROE, which at 10x is a 1.5% price move, and on
  90% margin costs ~13.5% of the wallet. Writing "losses limited to -15%" lands near the right
  figure by coincidence, from the wrong quantity, and reads as a cap on TOTAL downside — which it
  is not: Penguin's risk guard rails are off, so stops compound (three ~= 40% of the wallet).

Needles are SHORT and never span a line break: the closings are blockquoted, so a fragment that
crosses a newline picks up a "> " marker and silently stops matching.

Run:
  python3 -m pytest strategies/tests/test_hyperfeed_striker_offer.py -q
"""
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# skill -> fragments that must appear somewhere in its SKILL.md
OFFER = {
    "senpi-market-pulse":        ["Penguin", "Pelican", "senpi Hyperfeed", "13.5% of the wallet"],
    "senpi-smart-money":         ["Penguin", "Pelican", "senpi Hyperfeed", "13.5% of the wallet"],
    "senpi-portfolio":           ["Penguin", "Pelican", "Puffin", "Signals Hunter", "Athena",
                                  "13.5% of that wallet"],
    "senpi-signals":             ["Penguin", "Pelican", "Hyperfeed"],
    "senpi-trader-research":     ["senpi-signals", "hands-off"],
    "senpi-improve-trades":      ["Penguin", "Pelican", "Hyperfeed"],
    "senpi-strategy-discover":   ["Penguin", "Pelican", "Start here", "13.5% of that wallet"],
}

# Every surface that pitches the strikers must also carry the honest cost, and must not sell them
# as a price-pump detector. trader-research only routes onward, so it carries neither.
PITCHES = [s for s in OFFER if s not in ("senpi-trader-research", "senpi-signals")]


def _body(skill):
    with open(os.path.join(REPO, skill, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()


class HyperfeedStrikerOffer(unittest.TestCase):
    def test_every_surface_still_offers_them(self):
        for skill, needles in OFFER.items():
            body = _body(skill)
            for n in needles:
                self.assertIn(n, body, f"{skill}/SKILL.md no longer carries {n!r} — a recommendation "
                                       f"surface stopped offering the Hyperfeed strikers")

    def test_rotations_not_pumps(self):
        """The copy must carry the rule, and must never PITCH the detector as firing on a pump.

        Only affirmative pitch phrasings are banned. Prose that explains why a pump does NOT fire
        ("a pumping name no smart money rotated into does not fire") is the point of the rule, not a
        breach of it, so the check is on phrases, not on the bare word.
        """
        pitch = [r"pump signals?", r"market pumps?", r"pump detector", r"fires on (?:a )?pump",
                 r"pump[- ]chas\w* (?:signal|read)"]
        for skill in PITCHES:
            body = _body(skill)
            self.assertRegex(body, r'\*\*rotations\*\*, never "pumps"',
                             f"{skill}/SKILL.md lost the rotations-not-pumps rule")
            for pat in pitch:
                for m in re.finditer(pat, body, re.I):
                    lead = body[max(0, m.start() - 60):m.start()].lower()
                    self.assertTrue(any(w in lead for w in ("never", "not ", "no ", "rather than")),
                                    f"{skill}/SKILL.md pitches the detector as a pump signal: "
                                    f"...{body[max(0, m.start() - 80):m.end() + 40].strip()}...")

    def test_cost_is_stated_per_stop_out(self):
        """13.5% of the wallet, and 'each' — never '-15%' as a cap on total downside."""
        for skill in PITCHES:
            body = _body(skill)
            self.assertIn("13.5%", body, f"{skill}/SKILL.md dropped the per-stop wallet cost")
            self.assertRegex(body, r"\*\*each\*\*|each stop-out",
                             f"{skill}/SKILL.md dropped the per-trade qualifier on the stop cost")
            self.assertNotRegex(body, r"limiting losses to -?15%",
                                f"{skill}/SKILL.md states the stop as a cap on total downside; it is "
                                f"15% ROE per trade (~13.5% of the wallet), and stops compound")

    def test_no_outcome_promise(self):
        """The pitch is what the machinery does, never what it will return.

        Each pattern is allowed only under a negation — "no guaranteed-gain language" is a RULE
        against the promise, and senpi-improve-trades has carried it far longer than this copy has.
        """
        banned = [r"you'?ll see (?:a )?runners?", r"guarantee\w*", r"quick(?:ly)? profits?",
                  r"addict\w*", r"most users (?:win|profit)", r"can'?t lose"]
        for skill in OFFER:
            body = _body(skill)
            for pat in banned:
                for m in re.finditer(pat, body, re.I):
                    lead = body[max(0, m.start() - 40):m.start()].lower()
                    self.assertTrue(any(w in lead for w in ("never", "no ", "not ", "without")),
                                    f"{skill}/SKILL.md promises an outcome: "
                                    f"...{body[max(0, m.start() - 60):m.end() + 30].strip()}...")

    def test_tags_reach_the_ranker(self):
        """discover ranks by keyword overlap on tags (weight 3) — no boosts. The tags are the lever."""
        for pkg in ("penguin", "pelican"):
            with open(os.path.join(REPO, "strategies", pkg, "strategy.yaml"), encoding="utf-8") as fh:
                card = fh.read()
            for tag in ("hyperfeed", "high-risk-high-reward", "aggressive", "start-here"):
                self.assertIn(tag, card, f"{pkg} lost the {tag!r} tag — discover can no longer surface "
                                         f"it for that ask, and the ranker has no boost to fall back on")


if __name__ == "__main__":
    unittest.main()
