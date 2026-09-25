"""What the agent reads in senpi-signals/SKILL.md for 2.0: one reading, on demand, nothing to schedule or fund.

2.0 is a one-time job with no compare. The skill runs it when asked and never on a schedule (a cron is a
model call per firing), never deploys or funds anything for it, and never describes a detector that
needs an earlier reading as part of the feed."""
import json
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"


def _flat(path):
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def _frontmatter(path):
    return re.sub(r"\s+", " ", re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S).group(1))


def test_the_run_is_one_on_demand_reading():
    skill = _flat(SKILL)
    assert "python3 scripts/sweep.py --print-feed" in skill
    assert "One reading, no compare." in skill
    assert "two runs a minute apart give the same feed" in skill


def test_a_request_for_a_schedule_gets_a_no():
    skill = _flat(SKILL)
    assert "On demand only: say no to a cron." in skill
    assert "any schedule" in skill and "say no" in skill
    assert "Never create an `openclaw cron` or any other scheduled agent turn for it" in skill
    assert "if asked to put it on a cron or any schedule, say no" in _frontmatter(SKILL)


def test_nothing_to_deploy_fund_or_keep():
    skill = _flat(SKILL)
    for gone in ("strategies/signals", "deploy.py create signals", "--budget 10", "--consumer social",
                 "--snapshot-only", "state.json", "state ring"):
        assert gone not in skill, gone
    assert "never deploy a strategy or fund a wallet to run it" in skill
    assert not (SKILL_DIR / "references" / "scheduling.md").exists()


def test_compare_detectors_are_v2_and_never_claimed():
    skill = _flat(SKILL)
    assert "## v2 — compare over periods (not in 2.0)" in skill
    assert "Now, never \"since\"." in skill
    assert "Never describe the feed as if it carried them." in skill


def test_every_run_ends_with_one_question_to_trade_or_build():
    """The feed is observation; acting is a separate, consented step. One question closes every run,
    it routes to senpi-trade or to a strategy, and nothing is placed or funded without a yes."""
    skill = _flat(SKILL)
    assert "## How every run ends — one question" in skill
    # Three routes, not one. Athena alone sent every user who wanted a strategy to the same place
    # regardless of what they had just read — and never named the template that trades THIS feed.
    for fragment in ("Want to act on any of these?",
                     "I can set up a **single trade** on one read",
                     "**(1) Signals Hunter**, which trades this same feed on",
                     "**(2) Puffin**, that same engine",
                     "**(3) Athena**, a hedge fund built on these same smart-money signals",
                     "**(4) a different type of signal**",
                     "**(5) roll your own**, built around these reads with you"):
        assert fragment in skill, fragment
    assert "Place nothing until the user says yes to that exact order." in skill
    assert "The stop must sit before liquidation." in skill
    assert "It is a new strategy with no track record, and you say so." in skill
    assert "Deploy only on the user's yes." in skill
    assert "then the closing question (next section) — nothing after it" in skill
    assert "Follow-up: \"how could I play it?\"" not in skill


def _closing(skill):
    return skill[skill.index("## How every run ends — one question"):skill.index("## Where it lives")]


def test_the_offer_carries_all_five_routes_and_every_template_it_names_is_real():
    """Five strategy routes are offered every time — Signals Hunter, Puffin, fork Athena, a different
    type of signal, roll your own — and each read type names the template built on that kind of read. A
    name that isn't in the catalog would send the user to a template ops can't deploy, so every id named
    here must exist in strategies/catalog.json."""
    closing = _closing(_flat(SKILL))
    for route in ("**(1) Signals Hunter → senpi-strategy-ops.**",
                  "**(2) Puffin → senpi-strategy-ops.**",
                  "**(3) Fork Athena → senpi-strategy-ops.**",
                  "**(4) A different type of signal → senpi-strategy-ops, or senpi-strategy-discover.**",
                  "**(5) Roll your own → senpi-strategy-author.**"):
        assert route in closing, route
    assert "Offer **all five** strategy routes, every time." in closing
    named = re.findall(r"`([a-z0-9-]+)`", closing)
    ids = {s["id"] for s in json.loads(
        (SKILL_DIR.parent / "strategies" / "catalog.json").read_text(encoding="utf-8"))["skills"]}
    template_ids = {n for n in named if n in ids}
    assert {"signals-hunter", "puffin", "athena", "athena-x", "phalanx", "penguin"} <= template_ids, template_ids
    # any id-shaped token that looks like a template but isn't in the catalog is a dead end
    assert not {n for n in named if n.endswith(("-hunter", "-x")) } - ids
    assert "the minimum budget from the catalog, never from memory" in closing


def test_signals_hunter_is_offered_as_this_feeds_own_engine():
    """The claim that earns route (2) its place: signals-hunter's scanners ARE this skill's scripts.
    If that stops being true the pitch becomes marketing, so the parity is asserted here too."""
    import hashlib
    closing = _closing(_flat(SKILL))
    assert "byte-identical to the scripts behind" in closing
    # Both packages the closing offers as "this feed's own engine" — signals-hunter and its
    # concentrated variant puffin — must actually carry these bytes, or the pitch becomes marketing.
    for pkg in ("signals-hunter", "puffin"):
        for f in ("sweep.py", "score.py", "smartmoney.py"):
            a = (SKILL_DIR / "scripts" / f).read_bytes()
            b = (SKILL_DIR.parent / "strategies" / pkg / "main" / "scanners" / f).read_bytes()
            assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), (pkg, f)
    # and the expectation it sets: a correct run acts on fewer reads than the feed prints
    assert "acts on fewer reads than the feed shows" in closing


def test_the_concentrated_route_is_offered_with_its_cost_attached():
    """Puffin is the aggressive answer to "can I just trade these?" — the same vendored engine on one
    position. Concentration is the whole product, so the number that bounds it has to travel with the
    offer: an agent that pitches 10x on one name without the per-stop and fee cost is selling, not
    advising. signals-hunter must also stay the DEFAULT — the concentrated one is opt-in."""
    closing = _closing(_flat(SKILL))
    assert "**(2) Puffin**, that same engine concentrated into one position at a time" in closing
    assert "**(2) Puffin → senpi-strategy-ops.**" in closing
    for cost in ("19-23% of the account",            # one stop-out, at 75-90% margin x a 25% ROE stop
                 "0.8-0.9% of the",                   # one round trip at 10x
                 "no second position to average against the first"):
        assert cost in closing, cost
    assert "never as the default" in closing
    assert "`signals-hunter` stays the default answer for this feed." in closing


def test_an_off_feed_route_announces_itself_as_a_different_signal():
    """Route (4) exists to offer a template that does NOT trade these reads, and the honest form of
    that offer is structural, not a caveat bolted on afterwards: the route is worded as a different
    *type of signal* before any template is named.

    The trap it closes is real. Penguin is the template users ask for here by name — it is the other
    one-position-at-a-time book — but it trades Orca's 4h-leaderboard rank-jump detector, not these
    detectors. "Want to act on any of these?" answered with a template that cannot act on them is a
    promise the deploy will not keep, so the route label carries the disclosure and the bullet names
    the actual signal in the same sentence as the template.

    Asserted as the rule rather than as a Penguin special case, so the next off-feed template offered
    here has to be introduced the same way."""
    closing = _closing(_flat(SKILL))
    assert "Routes (1)-(3) trade the reads above. Route (4) deliberately does **not**" in closing
    assert "worded as a different *type of signal* before any template is named" in closing
    assert "offered AS a different signal, never as another way to trade what they just read" in closing
    # the example, and the signal it actually trades, in the route itself
    assert "`penguin`" in closing and "Orca's detector rather than this feed's" in closing
    assert "4h leaderboard" in closing
    # and the open-ended case routes to the picker, not to an improvised shortlist
    assert "senpi-strategy-discover" in closing
    assert "never improvise a shortlist from memory" in closing


def test_the_closing_question_is_treated_as_a_major_entry_point():
    """For most users this question is where a read first becomes a position. An agent that treats it
    as a footer — abbreviating it, dropping routes, asking it only sometimes — is where the funnel
    leaks, so the skill says so in as many words."""
    closing = _closing(_flat(SKILL))
    assert "**This is a major entry point.**" in closing
    assert "Ask it every time, in full, with all five routes." in closing


def test_a_template_is_a_starting_point_never_a_promise():
    closing = _closing(_flat(SKILL))
    assert "Every template is a starting point the user makes their own" in closing
    assert "Never promise or imply results, and never call a template proven." in closing
    for claim in ("proven template", "proven strategy", "guaranteed", "best-performing", "profitable"):
        assert claim not in closing.lower(), claim


def test_the_sweep_stays_read_only_and_the_question_stays_private():
    skill = _flat(SKILL)
    assert "3. **The sweep is read-only.**" in skill
    assert "Acting on a read happens only in the closing step (below), on the user's explicit yes" in skill
    assert "**Never in public copy.**" in skill


def test_the_brief_is_documented_and_the_chip_wording_loads_the_skill():
    skill = _flat(SKILL)
    front = _frontmatter(SKILL)
    assert "python3 scripts/sweep.py --brief 3" in skill
    assert "## The short version (`--brief N`)" in skill
    for trigger in ("scan Senpi Signals", "scan for market anomalies"):
        assert trigger in front, trigger


def test_the_mispricing_vocabulary_routes_here_and_stays_an_observation():
    """"Find what's mispriced" is how a user asks for this feed in their own words, so it has to
    reach the skill at all — and then be pinned to the reads, because "mispriced" is a valuation
    word and this sweep carries no model of fair value. It compares two things it read; saying a
    thing is worth more or less than its price would be advice the feed cannot support."""
    front = _frontmatter(SKILL)
    for trigger in ("find what's mispriced", "where is the market wrong", "find dislocations",
                    "what's out of line"):
        assert trigger in front, trigger
    body = SKILL.read_text(encoding="utf-8")
    skill = _flat(SKILL)
    assert "never a fair-value judgment" in skill
    assert 'never "HYPE is overpriced"' in skill
    # ONCE, and in golden rule 2 — a rebase across a section deletion re-attached a copy of this
    # paragraph to the previous section while a second copy lived in the rule, and every assertion
    # above passed against both. "Present somewhere" is not the claim; "stated once, where the rule
    # that forbids the crossing lives" is.
    assert body.count("gap between two things the sweep actually read") == 1, "the fence is duplicated"
    rule2 = body[body.index("2. **Observation, not advice.**"):body.index("3. **")]
    assert "gap between two things the sweep actually read" in rule2, "the fence drifted out of rule 2"
    assert "Every user has it." in skill


def test_a_whale_open_ships_but_an_add_or_a_flip_is_still_v2():
    """Rule 5 banned every whale read because they all needed history. An OPEN does not — the
    position dates itself — so the rule has to separate them, or the agent keeps suppressing a
    detector the feed now carries."""
    skill = _flat(SKILL)
    assert "The one exception, because it dates itself: a whale OPEN" in skill
    assert "An **add** needs the old size and a **flip** needs the old side" in skill
    assert "Say *opened*, never *added* or *flipped*" in skill
    assert "an undated whale position is dropped rather than called fresh" in skill
    # the v2 list must no longer claim whale reads wholesale
    assert "whale **adds and flips**, OI surges" in skill
