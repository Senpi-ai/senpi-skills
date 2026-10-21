#!/usr/bin/env python3
"""Where the rules sit in SKILL.md, not just whether they exist.

OpenClaw 10.4 refuses a SKILL.md read over the model's cap whole; the repo gate keeps every SKILL.md at or
under 56,000 est chars (tests/test_context_budgets.py). On 5.7 every turn after the read sees only what
`truncateToolResultText` keeps of it, ported as `openclaw_measure.replay_57` (tests/context_budget/). This
file is over its 16,000 cap and its last 2,000 chars carry no trigger word, so 5.7 keeps the first ~15,9xx
chars and nothing of the end. The follow-up rule, the router, the R1 one-book / saved-wallet rules and the
hard rules must be inside that replay, and the file must stay in that mode: a trigger word near the end
switches 5.7 to head ~11.8k + tail ~3.9k, and the hard rules past ~11.8k are gone on every follow-up. A
rule that drifts out of the replay passes every phrase test and is still gone on the next turn — this file
is what catches that.

    python3 -m pytest senpi-improve-trades/tests/test_improve_trades_skill_layout.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(HERE, "..")
SKILL = os.path.join(SKILL_DIR, "SKILL.md")
sys.path.insert(0, os.path.join(HERE, "..", "..", "tests", "context_budget"))
import openclaw_measure as om  # noqa: E402


def _skill():
    with open(SKILL, encoding="utf-8") as f:
        return f.read()


def _norm(text):
    # Whitespace runs and blockquote markers at line starts do not change what the model reads.
    return " ".join(re.sub(r"(?m)^[ \t]*>[ \t]?", "", text).split())


def _replayed():
    return _norm(om.replay_57(_skill()))


def _assert_replayed(needles):
    replayed = _replayed()
    lost = [n for n in needles if _norm(n) not in replayed]
    mode, kept, _ = om.replay_57_layout(_skill())
    assert not lost, (
        f"5.7 keeps only the first {kept:,} chars of SKILL.md on a follow-up ({mode}); these rules are past "
        f"it: {lost}. Make room above them: move long-tail text that sits above them further down SKILL.md "
        f"(it stays in core) or into references/ if it holds no rule. Do not drop a rule to make room."
    )


def test_the_57_replay_stays_head_only():
    trigger = om.important_tail_trigger(_skill())
    assert trigger is None, (
        f"The last 2,000 chars of SKILL.md contain {trigger!r}. That flips 5.7 to head ~11.8k + tail ~3.9k on "
        f"every follow-up, which drops the HARD RULES past ~11.8k (fees, TOTAL PnL, the counterfactual, "
        f"undetermined, verbatim numbers). Reword the end of the file (the output contract's last lines, the "
        f"Reference index) without error/exception/failed/fatal/traceback/panic/errno/exit code/total/"
        f"summary/result/complete/finished/done, and do not end it in '}}'."
    )


def test_the_follow_up_rule_and_the_router_survive_the_57_replay():
    _assert_replayed((
        "**On a follow-up, answer only what is new** — never re-send the `book` table",
        "## Router — first turn and every follow-up",
        "Run only the step(s) the ask needs; route every fix through the depth choice at the end — never auto-act.",
        "| sell too early / late", "| nothing to review, an empty result |", "| a saved wallet |",
        "| the wallet picked from `book.deep_dive.question` |",
        "`no_strategies` → `senpi-market-pulse`, then `senpi-strategy-discover`",
        "guardrail 5: judge it against its own mandate, all its wallets together, flat-by-design is not a loss",
        "**Still true on every follow-up**",
        "never mix them or rebuild one from the other",
        "mechanism not recorded on this build\", never a bug.",
        "\"not closed by its stop\", never the raw status",
        "Never pick the fix depth for the user.",
        "The maker ratio is a fee-tier signal, never a fee dollar total.",
    ))


def test_the_r1_rules_survive_the_57_replay():
    _assert_replayed((
        "Origin is the `kind` column, never a rank and never a section break.",
        "**A Senpi strategy is one row with all its wallets.**",
        "Never reorder it, never re-section by origin",
        "**One book total: quote `book.total.line` verbatim.**",
        "never compare wallets yourself",
        "ask `book.deep_dive.question` verbatim",
        "never imply Senpi checked who controls them",
        "Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it.",
        "**Every exit is a `MANUAL_TRADE`**",
        "**No write suggestions** on these wallets",
        "An address pasted in chat is never described as saved",
        "belong to `quant-desk`",
    ))


def test_the_hard_rules_survive_the_57_replay():
    _assert_replayed((
        "## HARD RULES (never violate — obey these even if you skim the rest)",
        "read on demand, never an agent-turn cron.",
        "**Never quote a $ PnL without saying which side of fees it is on.**",
        "**The fee rate is NOT a constant — never quote one from memory.**",
        "**Never take leverage from the config — read what the position ACTUALLY opened at.**",
        "**Lead with TOTAL PnL**",
        "**The hold-to-now counterfactual is CONTEXT, never a verdict.**",
        "**Undetermined ≠ all-clear.**",
        "**Quote the engine's numbers verbatim.**",
        "**It's the strategy, not the user — on Senpi strategy trades.**",
        "never a strategy pitch. No fabricated forward numbers (no $/week).",
        "these five are the floor.",
        "**Use this skill FIRST — before any raw MCP.**",
        "**never use `audit_*`** for closed trades",
    ))


def test_the_reference_index_lists_every_reference_and_every_reference_exists():
    raw = _skill()
    index = raw.split("## Reference index", 1)[1]
    named = set(re.findall(r"`references/([a-z0-9-]+\.md)`", index))
    on_disk = {n for n in os.listdir(os.path.join(SKILL_DIR, "references")) if n.endswith(".md")}
    assert named == on_disk, f"index names {sorted(named)}, references/ holds {sorted(on_disk)}"


def test_every_reference_the_skill_points_to_exists():
    for name in set(re.findall(r"`(?:references/)?([a-z0-9-]+\.md)`", _skill())) - {"SKILL.md"}:
        assert os.path.exists(os.path.join(SKILL_DIR, "references", name)), name


def test_the_rules_past_the_replay_stay_in_skill_md():
    # Summarised in the replayed head ("Still true on every follow-up"); the full rules live here.
    sk = _norm(_skill())
    for needle in ("Two sources, two jobs. Never mix them, and never reconstruct one from the other.",
                   "**A ratchet-sourced exit never names who closed the trade.**",
                   "And never print the raw status",
                   "**never** report it as a bug.",
                   "Discuss individual reversals only after the aggregate, and only as evidence.",
                   "do **not** give them a verdict, do **not** flag their missing mandate",
                   "Never pick for the user, and never act unprompted.",
                   "quote the maker ratio as a fee-tier signal, never a fee dollar total.",
                   "**Unknown is never empty.**", "never a bare \"no positions\""):
        assert needle in sk, needle


# Rules that bind in SKILL.md and are repeated in a reference so a re-read finds them. Edit both copies
# together: this fails as soon as one copy changes.
DUPLICATED = {
    "exit-records.md": (
        "the ROE it closed at — already at the REAL leverage, so quote it",
        "the full `DslState`, which already holds every number users ask for. Quote these rather than "
        "computing anything:",
    ),
}


def test_rules_repeated_in_a_reference_read_the_same_as_skill_md():
    sk = _norm(_skill())
    for name, needles in DUPLICATED.items():
        with open(os.path.join(SKILL_DIR, "references", name), encoding="utf-8") as f:
            ref = _norm(f.read())
        for needle in needles:
            n = _norm(needle)
            assert n in sk and n in ref, (
                f"{needle!r} must read the same in SKILL.md and references/{name}: edit both copies together "
                f"(SKILL.md is the rule; the reference repeats it for a re-read)."
            )


def test_moved_examples_live_in_their_reference():
    """The worked examples left SKILL.md; each must still be in the file the skill points to for it."""
    def ref(name):
        with open(os.path.join(SKILL_DIR, "references", name), encoding="utf-8") as f:
            return " ".join(f.read().split())
    g = ref("guardrails.md")
    for needle in ("**+$410.14**", "-5.094% x 3 = **-15.3%**", "kept 37%", "0.55 ROE points",
                   "\"HYPE ran +18% and you weren't in it\"", "**The mistake to avoid:**",
                   "you have 13 wallets, kill/merge 11 of them"):
        assert needle in g, needle
    s = ref("sources.md")
    for needle in ("starved the CPU", "`meta.onchain_recovered_wallets`", "`totalHyperliquidFees`",
                   "it's a GLOBAL bound"):
        assert needle in s, needle
    o = ref("one-book.md")
    assert "`min_closes` (8)" in o and "an empty $0 wallet stays in the table, never in the question" in o


def test_new_references_never_call_a_saved_wallet_connected_or_verified():
    """senpi-portfolio's wording test reads SKILL.md and output-shape.md only; the new files get the same
    two bans here."""
    for name in ("guardrails.md", "exit-records.md", "one-book.md", "sources.md"):
        with open(os.path.join(SKILL_DIR, "references", name), encoding="utf-8") as f:
            text = f.read()
        assert not re.search(r"(?i)connect(?!ion)", text), name
        assert not re.search(r"(?i)(?<![a-z])verified", text), name
