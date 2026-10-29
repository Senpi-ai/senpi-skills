#!/usr/bin/env python3
"""The AI-credits rules are resident in senpi-account-status/SKILL.md — the meter is explained, never
read, while the plan catalog IS published and the end of the free-credit milestones is stated — tiers
carry no benefit and are never brought up, the router can find the credits and tier questions from
the frontmatter, the shape the skill documents is the shape the engine returns, and the version the
README advertises is the version the skill carries."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "senpi-account-status" / "SKILL.md"
NOTES = REPO / "senpi-account-status" / "references" / "presentation-notes.md"
FIXTURE = REPO / "senpi-account-status" / "tests" / "fixtures" / "status_fixture.json"
README = REPO / "README.md"
SECTION = "## AI credits — the usage meter"
PLANS = "## Plans & free credits"
RULES = "## Golden rules"
FREE_CREDITS = "**Free credits"


def _split(path):
    """(frontmatter, body) — the body is everything after the closing `---`."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def _flat(text):
    """Prose needles must survive a re-wrap: collapse every run of whitespace to one space."""
    return re.sub(r"\s+", " ", text)


def _section(body, heading=SECTION):
    start = body.find(heading)
    assert start >= 0, f"SKILL.md lost its {heading!r} section"
    rest = body[start + len(heading):]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def _free_credits(body):
    """The free-credits paragraph: from its bold lead-in to the end of the Plans section."""
    plans = _section(body, PLANS)
    start = plans.find(FREE_CREDITS)
    assert start >= 0, "the Plans section lost its free-credits paragraph"
    return plans[start:]


class CreditsAreTheUsageMeter(unittest.TestCase):
    def test_the_section_and_its_rules_are_in_the_body(self):
        body = _flat(_split(SKILL)[1])
        self.assertIn(SECTION, body)
        for needle in (
            "I can't read it from here",
            "the whole conversation is re-sent each turn",
            "cannot be traded, withdrawn, deposited into a strategy, or moved anywhere",
            '"chatting is free"',
            "depends on your plan",
        ):
            self.assertIn(needle, body, needle)

    def test_the_never_say_column_carries_the_four_failure_modes(self):
        section = _flat(_section(_split(SKILL)[1]))
        self.assertIn("| Say | Never say |", section)
        for needle in (
            '"chatting is free"',
            "plan size you did not read",
            "tradable or withdrawable",
            "Blaming a shrinking meter on trading losses",
        ):
            self.assertIn(needle, section, needle)

    def test_a_strategy_question_and_any_language_land_on_the_meter(self):
        # "credits" asked about a strategy, or in another language, is still the AI-credit meter: the
        # runtime ticks without a model call, the agent's own crons are what spend, and an unclear
        # "credits" is asked about rather than read as funding or leverage
        section = _flat(_section(_split(SKILL)[1]))
        for needle in (
            "ticks without a model call",
            "decision_mode: llm",
            "each firing is a full model call",
            "does my strategy use credits?",
            "in any language",
            "unless the user says leverage, a loan or funding",
            "a wallet or strategy balance offered as proof about credits",
        ):
            self.assertIn(needle, section, needle)
        self.assertIn("does my strategy use credits?", _flat(_split(SKILL)[0]))

    def test_no_plan_size_or_dollar_amount_in_the_section(self):
        # plan sizes change; the text says "depends on your plan" and never carries a figure
        section = _flat(_section(_split(SKILL)[1]))
        self.assertNotIn("$", section)
        self.assertIsNone(re.search(r"\b\d[\d,]*\s*credits\b", section), "a plan size crept in")

    def test_the_router_can_find_the_credits_questions(self):
        frontmatter = _split(SKILL)[0]
        desc = re.search(r"description: >-\n((?:  .*(?:\n|$))+)", frontmatter).group(1)
        for needle in ("credits", "meter", "bubble"):
            self.assertIn(needle, desc, needle)

    def test_readme_row_matches_the_skill_version(self):
        if not README.is_file():
            self.skipTest("README not present (installed skill layout)")
        version = re.search(r'version: "([0-9.]+)"', SKILL.read_text(encoding="utf-8")).group(1)
        self.assertIn(f"| [`senpi-account-status`](senpi-account-status/) | {version} |",
                      README.read_text(encoding="utf-8"))


class PlansAndFreeCreditsArePublished(unittest.TestCase):
    """The catalog is the half of this that IS quotable — a user asking what a plan costs gets the
    table, not a shrug at the meter. The figures come from the subscription card, so the rules that
    survive a price change (no rollover, instant upgrade, the user's own page wins) matter as much
    as the numbers."""

    def test_the_four_plans_and_the_cycle_rules_are_in_the_table(self):
        section = _flat(_section(_split(SKILL)[1], PLANS))
        for plan in ("Starter", "Pro (default)", "Advanced", "Quant"):
            self.assertIn(plan, section, plan)
        for needle in (
            "Every plan includes every model",
            "no rollover, no top-ups, no overage",
            "Upgrading is instant",
            "takes effect at the next renewal",
            "Payment is web only",
        ):
            self.assertIn(needle, section, needle)

    def test_the_plan_table_carries_the_published_prices(self):
        # the figures are the point of the section: an agent is told to quote this table, so a price
        # that drifts is quoted at the user. Names and prose rules are locked above; these are the
        # numbers (credits from 2026-10-26: Pro $55, Advanced $115, Quant $240).
        section = _section(_split(SKILL)[1], PLANS)
        for row in (
            "| Starter | $25 | $25 | 1 |",
            "| Pro (default) | $50 | $55 (+10%) | 1.5 |",
            "| Advanced | $100 | $115 (+15%) | 2 |",
            "| Quant | $200 | $240 (+20%) | 3 |",
        ):
            self.assertIn(row, section, row)

    def test_the_advertised_bonus_matches_the_credits_it_grants(self):
        # "$100 -> $130 (+30%)" has to be arithmetic, not decoration
        section = _section(_split(SKILL)[1], PLANS)
        priced = re.findall(r"\|\s*\$(\d+)\s*\|\s*\$(\d+)\s*\(\+(\d+)%\)", section)
        self.assertTrue(priced, "no bonus-rate plan row found")
        for price, credits, pct in priced:
            self.assertEqual(int(credits), round(int(price) * (1 + int(pct) / 100)),
                             f"${price} at +{pct}% is ${round(int(price) * (1 + int(pct) / 100))}, "
                             f"not ${credits}")

    def test_the_trial_figure_stays_unpublished(self):
        # plan prices are fixed and quotable; the trial grant has moved more than once, so the
        # catalog may carry every plan number and still no trial number
        section = _flat(_section(_split(SKILL)[1], PLANS))
        self.assertIn("Never quote a trial figure", section)
        self.assertIn("open positions are NOT auto-closed", section)
        self.assertIn("expires **14 days", section)   # the one trial number that IS published

    def test_a_lapsed_plan_pauses_the_strategies_too(self):
        # "a running strategy uses no credits" was read as "a strategy needs no plan": agents told
        # users no subscription was needed, and the strategies stopped when the trial ended
        section = _flat(_section(_split(SKILL)[1], PLANS))
        for needle in (
            "A lapsed plan pauses everything, not just chat",
            "a renewal payment fails",
            "every strategy on it stops",
            "a stop-loss already resting on the exchange still triggers",
            "never tell them a subscription is not needed",
        ):
            self.assertIn(needle, section, needle)
        self.assertIn('"no subscription is needed"', _flat(_section(_split(SKILL)[1])))  # Never say
        self.assertIn("what happens when my trial ends?", _flat(_split(SKILL)[0]))  # the router finds it

    def test_the_meter_section_still_defers_the_balance_to_the_page(self):
        # the catalog being quotable must not leak into the balance: "what is left" is still theirs
        section = _flat(_section(_split(SKILL)[1]))
        self.assertIn("how much is **left** depends on your plan and your usage", section)


class FreeCreditMilestonesHaveEnded(unittest.TestCase):
    """2026-10-26: every free-credit milestone ended, the $15 subscribe bonus included, and the
    "Unlock Free AI Credits" card shows nothing. A table left in the skill is a promise of credit the
    user never gets."""

    def test_every_milestone_is_named_as_ended(self):
        free = _flat(_free_credits(_split(SKILL)[1]))
        for needle in (
            "milestones ended on 2026-10-26",
            "your first strategy, funding a wallet, the trading-volume ladder and the "
            "first-subscription bonus",
            "None of them pays any more",
            'the "Unlock Free AI Credits" card shows nothing',
            'Its "Deploy your agent" row went with it',
            "was never a milestone",
        ):
            self.assertIn(needle, free, needle)

    def test_earned_credit_stays_and_the_trial_credit_still_activates_on_deploy(self):
        free = _flat(_free_credits(_split(SKILL)[1]))
        for needle in (
            "Milestone credit already earned stays until the next renewal",
            "The trial credit still activates when the user deploys their agent",
        ):
            self.assertIn(needle, free, needle)

    def test_the_paragraph_publishes_no_ladder_and_no_dollar_figure(self):
        free = _free_credits(_split(SKILL)[1])
        self.assertNotIn("|", free, "a milestone table is back")
        self.assertNotIn("$", free, "a milestone amount is back")

    def test_no_milestone_is_offered_anywhere_in_the_skill(self):
        body = _flat(_split(SKILL)[1])
        for stale in ("Subscribe to a plan", "Launch your first strategy", "Not retroactive",
                      "across 2 milestones", "Milestone | Free credits",
                      "never lands in Balances as USDC"):
            self.assertNotIn(stale, body, stale)

    def test_the_router_still_finds_the_milestone_and_free_credit_questions(self):
        desc = _flat(_split(SKILL)[0])
        for needle in ("where are my milestone credits?", "how do I get free credits?"):
            self.assertIn(needle, desc, needle)


class TheFeeFollowsThePlan(unittest.TestCase):
    """From 2026-10-26 a paid plan pays no Senpi trading fee. The skill states no nonzero Senpi fee
    anywhere: for any other account the agent quotes the fee the engine returns, never a rate from
    memory."""

    def test_the_catalog_states_the_paid_plan_fee(self):
        section = _flat(_section(_split(SKILL)[1], PLANS))
        for needle in (
            "No Senpi trading fee on any paid plan",
            "only Hyperliquid's own exchange fees",
            "quote its own `fee.pct` from the engine",
        ):
            self.assertIn(needle, section, needle)
        self.assertNotIn("start at 0.05% on every plan", section)

    def test_no_nonzero_senpi_fee_is_quoted(self):
        for path in (SKILL, NOTES):
            text = path.read_text(encoding="utf-8")
            for rate in ("0.025%", "0.03%", "0.05%"):
                self.assertNotIn(rate, text, f"{path.name} quotes {rate}")

    def test_the_standing_answer_quotes_the_fee_the_data_returns(self):
        body = _flat(_split(SKILL)[1])
        self.assertIn("Quote `fee.pct` as returned: `0%` on a paid plan", body)
        self.assertIn("the Senpi trading fee (none on a paid plan)", _flat(_split(SKILL)[0]))

    def test_the_presentation_notes_quote_the_same_field(self):
        notes = _flat(NOTES.read_text(encoding="utf-8"))
        self.assertIn("Quote `fee.pct` as returned", notes)


class ReferralsPayAShareOfSpend(unittest.TestCase):
    """Referrals pay 15% of a referred friend's monthly Senpi spend for their first 12 months (the
    senpi.ai/referrals program), not a share of the builder fee, and they do earn: the interim
    "referrals earn nothing new" line is gone."""

    def test_the_catalog_states_the_referral_share(self):
        section = _flat(_section(_split(SKILL)[1], PLANS))
        for needle in (
            "Referrals pay 15% of a referred friend's monthly Senpi spend, for their first 12 months",
            "not from their trading",
            "$15 a month × 12 months = $180",
            # Payout is TBD: without this the agent falls back on the old daily-USDC payout.
            "How and when the share is paid out is not announced yet: if asked, say so, and never "
            "describe a payout schedule, token or threshold for it.",
        ):
            self.assertIn(needle, section, needle)

    def test_no_file_says_referrals_earn_nothing(self):
        for path in (SKILL, NOTES):
            text = _flat(path.read_text(encoding="utf-8"))
            self.assertNotIn("earn nothing", text, path.name)
            self.assertNotIn("builder fee on referred", text, path.name)

    def test_the_router_finds_the_referral_question(self):
        self.assertIn("how do referrals work?", _flat(_split(SKILL)[0]))


class TiersCarryNoBenefit(unittest.TestCase):
    """Loyalty tiers carry no benefit for now. The agent does not bring them up, never suggests
    reaching one, and answers a question about one with a single sentence."""

    TIER_WORDS = ("loyalty", "next_tier", "points_to_next", "fee_discount_pct", "get_loyalty_tiers",
                  "maintenance", "demoted", "previous_tier", "Mandatory closing",
                  "break down what it takes", "Bronze", "Legend")

    def test_the_rule_is_a_golden_rule(self):
        rules = _flat(_section(_split(SKILL)[1], RULES))
        for needle in (
            "Tiers carry no benefit right now: no fee change, nothing unlocked.",
            "Do not bring tiers up.",
            "Never suggest reaching, keeping or regaining a tier",
            "never close with a next-tier offer",
            "say tiers carry no benefit right now",
            "Points still accrue at the plan multiplier and still set leaderboard rank.",
        ):
            self.assertIn(needle, rules, needle)

    def test_a_standing_answer_is_points_rank_the_fee_and_referral(self):
        notes = _flat(NOTES.read_text(encoding="utf-8"))
        self.assertIn("A standing answer is points, rank, the fee and referral", notes)
        self.assertIn("never a closing offer about one", notes)

    def test_neither_file_still_carries_the_tier_story(self):
        for path in (SKILL, NOTES):
            text = _flat(path.read_text(encoding="utf-8")).lower()
            for word in self.TIER_WORDS:
                self.assertNotIn(word.lower(), text, f"{path.name}: {word}")
            self.assertNotIn("next tier", text, f"{path.name}: next tier")

    def test_only_the_golden_rule_mentions_a_tier(self):
        body = _split(SKILL)[1]
        rest = body.replace(_section(body, RULES), "")
        self.assertEqual([line for line in rest.splitlines() if "tier" in line.lower()], [])

    def test_the_router_still_finds_the_tier_questions(self):
        desc = _flat(_split(SKILL)[0])
        for needle in ("what's my rank/tier?", "where did my tier go?"):
            self.assertIn(needle, desc, needle)


class TheSkillDocumentsTheEnginesOutput(unittest.TestCase):
    """The agent reads the shape from SKILL.md and the values from status.py. When the two drift, it
    narrates fields that are not there."""

    def _engine_output(self):
        scripts = str(REPO / "senpi-account-status" / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import status
        return status.run(status._FixtureClient(json.loads(FIXTURE.read_text(encoding="utf-8"))))

    def test_the_documented_sections_are_the_returned_sections_in_order(self):
        documented = re.search(r"Returns `\{([^}]*)\}`", _split(SKILL)[1]).group(1)
        self.assertEqual(documented.split(", "), list(self._engine_output()))

    def test_the_documented_fee_and_points_fields_are_the_returned_ones(self):
        body = _split(SKILL)[1]
        out = self._engine_output()
        fee = re.search(r"- `fee` — `(\w+)` \+ `(\w+)`", body)
        self.assertEqual(list(fee.groups()), list(out["fee"]))
        points = re.search(r"- `points` — ([^\n]*(?:\n  [^\n]*)*)", body).group(1)
        documented = re.findall(r"`(\w+)`", points)
        self.assertEqual([f for f in out["points"] if f != "found"], documented)


if __name__ == "__main__":
    unittest.main()
