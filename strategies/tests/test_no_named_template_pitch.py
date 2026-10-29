"""The satellite skills hand a strategy ask to senpi-strategy-author; they never pitch a template by name.

Strategy conversations start in senpi-strategy-author, which forks the closest template when one
fits the user's idea (found through senpi-strategy-discover's ranking) and builds from scratch when
none does. So the surfaces below that close with an offer (a market read, a smart-money read, a
portfolio, a signals sweep, a trade review, a mirror) hand off to author and name no template
themselves. Hand-written copy rots silently: one edit to a closing section and a named pitch is back
with nothing failing.

author and discover are not on the list: offering a fitting template by name is their job.
quant-desk is not either: its examples name strategies the reader already runs ("run the desk on
Phalanx?"), which is not a pitch.

Run:
  python3 -m pytest strategies/tests/test_no_named_template_pitch.py -q
"""
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

SURFACES = ["senpi-market-pulse", "senpi-smart-money", "senpi-portfolio", "senpi-signals",
            "senpi-trader-research", "senpi-improve-trades"]
# senpi-trade carried the named copy lists, so it is held to naming none too; it never carried the
# pitch copy the outcome and leverage checks guard ("a guaranteed fill" there is an order term).
NAME_SURFACES = SURFACES + ["senpi-trade"]
# The templates these surfaces used to pitch by hand: the Hyperfeed and signals menus, and the copy lists.
PITCHED = ["Penguin", "Pelican", "Puffin", "Signals Hunter", "Athena", "Whalehunter", "Squid", "Remora",
           "Raptor", "Oxpecker", "Cuckoo", "Jackal", "Pilotfish", "Starling", "Phalanx"]


def _names(body, name):
    """Whole-word, any case: "**whalehunter**" and "Whalehunter" are the same pitch."""
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", body, re.I)


def _body(skill):
    with open(os.path.join(REPO, skill, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()


class NoNamedTemplatePitch(unittest.TestCase):
    def test_no_surface_pitches_a_template_by_name(self):
        for skill in NAME_SURFACES:
            body = _body(skill)
            for name in PITCHED:
                self.assertIsNone(_names(body, name), f"{skill}/SKILL.md names {name!r} — a satellite "
                                                      f"skill is pitching a template; hand off to author")
            self.assertIn("senpi-strategy-author", body,
                          f"{skill}/SKILL.md has no hand-off to senpi-strategy-author")

    def test_no_outcome_promise(self):
        """The pitch is what the machinery does, never what it will return.

        Each pattern is allowed only under a negation — "no guaranteed-gain language" is a RULE
        against the promise, and senpi-improve-trades has carried it far longer than this copy has.
        """
        banned = [r"you'?ll see (?:a )?runners?", r"guarantee\w*", r"quick(?:ly)? profits?",
                  r"addict\w*", r"most users (?:win|profit)", r"can'?t lose"]
        for skill in SURFACES:
            body = _body(skill)
            for pat in banned:
                for m in re.finditer(pat, body, re.I):
                    lead = body[max(0, m.start() - 40):m.start()].lower()
                    self.assertTrue(any(w in lead for w in ("never", "no ", "not ", "without")),
                                    f"{skill}/SKILL.md promises an outcome: "
                                    f"...{body[max(0, m.start() - 60):m.end() + 30].strip()}...")

    def test_leverage_is_up_to_ten_never_a_flat_ten(self):
        """"up to 10x", never "at 10x": the per-name venue cap clamps plenty of instruments below 10.

        The clamp changes the PRICE move behind a number, not the wallet cost — ROE is return on
        margin, so a 15% ROE stop is ~13.5% of the wallet at any leverage, while the move it takes
        doubles at 5x. So this guards only the leverage clause of the PITCH; the 13.5% figure is
        leverage-independent and pinned separately above.

        Matched as exact pitch constructions, not a pattern around "position": senpi-portfolio
        carries an unrelated and correct ROE explainer ("a 1% price move at 10x is a 10% return on
        margin"), and a looser regex flags that instead of a real regression.
        """
        for skill in SURFACES:
            body = _body(skill)
            for bad in ("one position at 10x", "at 10x with a DSL", "position at 10x on the"):
                self.assertNotIn(bad, body,
                                 f"{skill}/SKILL.md pitches a flat 10x ({bad!r}); the per-name venue "
                                 f"cap clamps many instruments below it — say 'up to 10x'")

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
