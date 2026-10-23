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
from layout import assert_replayed, norm as _norm  # noqa: E402


def _skill():
    with open(SKILL, encoding="utf-8") as f:
        return f.read()


def _assert_replayed(needles):
    assert_replayed(_skill(), needles)


def test_the_57_replay_stays_head_only():
    trigger = om.important_tail_trigger(_skill())
    assert trigger is None, (
        f"The last 2,000 chars of SKILL.md contain {trigger!r}. That flips 5.7 to head ~11.8k + tail ~3.9k on "
        f"every follow-up, which drops the HARD RULES past ~11.8k (fees, TOTAL PnL, the counterfactual, "
        f"undetermined, verbatim numbers). Reword the end of the file (the output contract's last lines, the "
        f"Reference index) without error/exception/failed/fatal/traceback/panic/errno/exit code/total/"
        f"summary/result/complete/finished/done, and do not end it in '}}'."
    )


ROUTER_NEEDLES = (
    "**On a follow-up, answer only what is new** — never re-send the `book` table",
    "## Router — first turn and every follow-up",
    "Run only the step(s) the ask needs; route every fix through the depth choice at the end — never auto-act.",
    "| sell too early / late", "| nothing to review, an empty result |", "| a saved wallet |",
    "| the wallet picked from `book.deep_dive.question` |",
    "| what did I miss, compare to the market / the whales |", "whales: compose `senpi-smart-money`",
    "`no_strategies` → `senpi-market-pulse`, then `senpi-strategy-discover`",
    "guardrail 5: judge it against its own mandate, all its wallets together, flat-by-design is not a loss",
    "| `output-contract.md` |", "| the user picks a fix depth | *(none)* | `output-contract.md` (both menus) |",
    "| where am I leaking, fees, maker vs taker | `telemetry` | `guardrails.md` hard rule 0;",
    "**Still true on every follow-up**",
    "mechanism not recorded on this build\", never a bug.",
    "\"closed by hand\" or \"manual\". Never the raw status.",
    "The maker ratio is a fee-tier signal, never a fee dollar total.",
    "the maker ratio: \"Quick actions\")",
    "**Fix depth.** End every improvement with \"How deep do you want me to go?\" and stop: Explain only",
    "(on a saved wallet: Explain only / Turn it into a routine / Draft the exact orders).",
)


def test_the_follow_up_rule_and_the_router_survive_the_57_replay():
    _assert_replayed(ROUTER_NEEDLES)


MOVED_INTO_HEAD_NEEDLES = (
    "Two sources, two jobs. Never mix them, and never reconstruct one from the other.",
    "**Telemetry is read ONLY from the current book.**",
    "**A ratchet-sourced exit never names who closed the trade.**",
    "Say **\"not closed by its stop\"**; never \"you closed this\", \"closed by hand\" or \"manual\".",
    "Discuss individual reversals only after the aggregate, and only as evidence.",
    "**Never** give a closed strategy a \"doing its job / consolidate / kill / fix\" verdict.",
    "Never pick for the user, and never act unprompted.",
)


def test_the_rules_moved_into_the_head_survive_the_57_replay():
    # Original SKILL.md sentences, copied word for word into "Still true on every follow-up" (their home
    # sections further down keep them too). Ranked by harm if forgotten on a follow-up: who closed a trade,
    # acting without a pick, a closed strategy turned into a "consolidate" recommendation.
    _assert_replayed(MOVED_INTO_HEAD_NEEDLES)


def _menu_options(block):
    # The bold option names of a "> - **Name** — what it does" menu, in order.
    return re.findall(r"(?m)^>\s*-\s*\*\*(.+?)\*\*\s+—", block)


def test_the_replayed_fix_depth_line_copies_the_full_menus():
    # Copy must match: the "Still true" line is the menus further down with the descriptions left out.
    # Rename an option in either place and this fails until both read the same.
    raw = _skill()
    contract = raw.split("## The output contract", 1)[1]
    question = re.search(r"(?m)^> \*\*(How deep do you want me to go\?)\*\*$", contract)
    assert question, "the output contract's fix-depth menu lost its question line"
    senpi = _menu_options(contract[question.end():].split("\n\n", 1)[0])
    saved_section = raw.split("### Fix depth on a saved wallet", 1)[1].split("\n## ", 1)[0]
    saved = _menu_options(saved_section)
    assert len(senpi) == 3 and len(saved) == 3, (senpi, saved)
    line = (f"**Fix depth.** End every improvement with \"{question.group(1)}\" and stop: "
            f"{' / '.join(senpi)} (on a saved wallet: {' / '.join(saved)}).")
    card = raw.split("**Still true on every follow-up**", 1)[1].split("\n\n", 1)[0]
    assert _norm(line) in _norm(card), (
        f"The 'Still true on every follow-up' Fix depth line must name the menus further down word for word: "
        f"{line!r}")


R1_NEEDLES = (
    "Origin is the `kind` column, never a rank and never a section break.",
    "**A Senpi strategy is one row with all its wallets.**",
    "Never reorder it, never re-section by origin",
    "**One book total: quote `book.total.line` verbatim.**",
    "`value_read: \"partial\"` → a strategy whose value covers only some of its wallets: say \"(1 of 2 "
    "wallets)\" from `wallets_loaded` / `wallet_count`.",
    "never compare wallets yourself",
    "ask `book.deep_dive.question` verbatim",
    "never imply Senpi checked who controls them",
    "Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it.",
    "**Every exit is a `MANUAL_TRADE`**",
    "**No write suggestions** on these wallets",
    "An address pasted in chat is never described as saved",
    "belong to `quant-desk`",
)


def test_the_r1_rules_survive_the_57_replay():
    _assert_replayed(R1_NEEDLES)


HARD_RULE_NEEDLES = (
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
    "Those return un-attributed dumps that invite exactly the failure modes below (skipping the current-price "
    "comparison, guessing the exit mechanism).",
    "**never use `audit_*`** for closed trades",
    "**clips silently**: `default_leverage` is a request, not what opened.",
)


def test_the_hard_rules_survive_the_57_replay():
    _assert_replayed(HARD_RULE_NEEDLES)


# 1.20.2 (Box A, OpenClaw 5.7, 2026-10-22): two rules past the cut, each copied short into the replay
# next to the line it belongs with. A "Turn it into a routine" follow-up on a wallet with a few closes gave
# stop prescriptions (the sample gate was past the cut), and follow-ups wrote "a guaranteed +$237" (the
# no-guaranteed-gain rule was past the cut). The full rules stay where they were; the test below keeps
# each short copy in step with them.
SAMPLE_GATE_COPY = "With fewer than ~8 closes, offer \"explain only\" and say the sample is too small."
NO_GUARANTEE_COPY = "No fabricated forward numbers (no $/week). No guaranteed-gain language."
SHORT_COPY_NEEDLES = (SAMPLE_GATE_COPY, NO_GUARANTEE_COPY)


def test_the_short_copies_survive_the_57_replay():
    _assert_replayed(SHORT_COPY_NEEDLES)


def test_the_short_copies_sit_by_their_lines_and_match_the_full_rules():
    raw = _skill()
    card = _norm(raw.split("**Still true on every follow-up**", 1)[1].split("\n\n", 1)[0])
    fix_depth = card.split("**Fix depth.**", 1)[1]
    assert _norm(SAMPLE_GATE_COPY) in fix_depth, "the sample-gate copy left the replayed Fix depth line"
    # The full gates: guardrail 7 (Senpi strategy) and "Fix depth on a saved wallet". Same threshold, and
    # the copy's words are the part the two share.
    g7 = _norm(_section(raw, "### 7. The user chooses the fix depth — never auto-act, and never offer a "
                             "tune off one trade"))
    saved = _norm(_section(raw, "### Fix depth on a saved wallet — advice they apply on Hyperliquid"))
    full = (re.search(r"With \*\*fewer than (~\d+) attributed closes on that strategy\*\*, offer \"explain only\" "
                      r"and say the sample is too small to tune on", g7),
            re.search(r"Same sample gate: with fewer than (~\d+) closes on that wallet, offer \"explain only\" "
                      r"and say the sample is too small to change a routine on\.", saved))
    assert all(full), "a full sample gate (guardrail 7 or the saved-wallet one) changed its wording"
    copy = re.search(r"fewer than (~\d+) closes", SAMPLE_GATE_COPY).group(1)
    assert {m.group(1) for m in full} == {copy}, (
        f"the replayed sample gate says {copy}; the full gates say {[m.group(1) for m in full]}. Edit all "
        f"three together.")
    # Hard rule 5 carries the no-guaranteed-gain copy; the full rules are the output contract's step 4 and
    # guardrail 3, both past the cut.
    hard = _norm(_section(raw, "## HARD RULES (never violate — obey these even if you skim the rest)"))
    rule5 = hard.split("5. **It's the strategy, not the user", 1)[1].split("The detailed guardrails", 1)[0]
    assert _norm(NO_GUARANTEE_COPY) in rule5, "the no-guaranteed-gain copy left hard rule 5"
    assert "**no guaranteed-gain language**" in _norm(_section(raw, "## The output contract — what you produce"))
    assert "no guaranteed-gain figure of any kind." in _norm(
        _section(raw, "### 3. No fabricated numbers — realized PnL + engine counterfactuals ONLY"))


REPLAY_NEEDLES = (ROUTER_NEEDLES + MOVED_INTO_HEAD_NEEDLES + R1_NEEDLES + HARD_RULE_NEEDLES
                  + SHORT_COPY_NEEDLES)
REPLAY_MARGIN = 150


def test_every_replayed_rule_keeps_a_margin_before_the_57_cut():
    # Same check as senpi-portfolio's layout test: a rule that ends right at the cut is one edit away from
    # falling past it, and the phrase tests above would only notice once it is gone.
    raw = _skill()
    mode, kept, _ = om.replay_57_layout(raw)
    assert mode == "head-only", mode
    tight = []
    for needle in REPLAY_NEEDLES:
        n = _norm(needle)
        lo, hi = 0, len(raw)
        while lo < hi:  # shortest prefix of SKILL.md that contains the needle
            mid = (lo + hi) // 2
            if n in _norm(raw[:mid]):
                hi = mid
            else:
                lo = mid + 1
        end = len(om.utf16_units(raw[:lo]))
        if kept - end < REPLAY_MARGIN:
            tight.append((needle[:60], kept - end))
    assert not tight, (
        f"These replayed rules end less than {REPLAY_MARGIN} chars before the 5.7 cut at {kept:,}: {tight}. "
        f"Make room above them (move non-rule text out of the head) rather than letting one fall past the cut.")


# Each sentence copied word for word into "Still true on every follow-up" sits there once and once in its
# home section further down, and nowhere else: a third copy, or the home copy edited away, fails here.
STILL_TRUE_COPIES = (
    ("Two sources, two jobs. Never mix them, and never reconstruct one from the other.",
     "## Sources — onchain vs runtime (the split that keeps you honest)"),
    ("**Telemetry is read ONLY from the current book.**",
     "## Sources — onchain vs runtime (the split that keeps you honest)"),
    ("**A ratchet-sourced exit never names who closed the trade.**",
     "## Sources — onchain vs runtime (the split that keeps you honest)"),
    ("Say **\"not closed by its stop\"**; never \"you closed this\", \"closed by hand\" or \"manual\".",
     "## Sources — onchain vs runtime (the split that keeps you honest)"),
    ("Discuss individual reversals only after the aggregate, and only as evidence.",
     "## The output contract — what you produce"),
    ("**Never** give a closed strategy a \"doing its job / consolidate / kill / fix\" verdict.",
     "### 8. Verdicts are for the CURRENT book only — closed strategies are HISTORY, not a live problem"),
    ("Never pick for the user, and never act unprompted.", "## The output contract — what you produce"),
)


def test_the_still_true_copies_sit_in_the_block_and_in_their_home_section():
    raw = _skill()
    card = _norm(raw.split("**Still true on every follow-up**", 1)[1].split("\n\n", 1)[0])
    whole = _norm(raw)
    for sentence, home in STILL_TRUE_COPIES:
        n = _norm(sentence)
        assert whole.count(n) == 2, (sentence, whole.count(n))
        assert n in card, f"{sentence!r} left the 'Still true on every follow-up' block"
        assert n in _norm(_section(raw, home)), f"{sentence!r} is no longer in its home section {home!r}"


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
                   "**Unknown is never empty.**", "never a bare \"no positions\"",
                   # guardrail 9 keeps its reason clause (CLAUDE.md: one justification clause per rule).
                   "It's out of scope for a *trade review*, it second-guesses a strategy that was just set up, "
                   "and nudging toward MARKET / higher-slippage fills **raises fees** — the biggest killer of "
                   "returns.",
                   # 1.20.1 review round: base wording back in place of the paraphrase.
                   "**If the gateway is down**, `senpi dsl closes` exits non-zero and prints *\"start it with: "
                   "openclaw gateway run\"*. **Say that.** An unreadable record is an unreadable record — do not "
                   "fall back to guessing a mechanism from fill shapes and present it as fact.",
                   "Keep **floor vs fill** separate: on AVAX the floor was +8.23% and it filled +7.67%, which is "
                   "0.55 ROE points of execution slippage, not calibration.",
                   "— and it routes to **`senpi-strategy-author` / `senpi-strategy-ops`** to apply.",
                   "Saying it \"detected the position rolling over\" or \"cut early before the hard stop\" invents "
                   "a mechanism — a real review did exactly that",
                   "**Lead with `pnl_summary.total`** (realized closed + unrealized open) — NOT "
                   "`realized_pnl_total` alone.",
                   "**`external_no_trades`** — saved wallets, no Senpi strategy, nothing closed in the window",
                   # Back in core word for word (they had left for references or been paraphrased).
                   "\"HYPE ran +18% and you weren't in it\" is only interesting if HYPE is *in the strategy's "
                   "mandate* and the miss reveals a gate that's too tight — otherwise it's just an asset the "
                   "strategy was never designed to trade.",
                   "The engine sources closed trades from on-chain fills, so you should not need "
                   "`strategy_get_pnl_and_account_value_history` for a trade review at all.",
                   "Translate `closeReason`, never paste the enum:",
                   "**Scope the ask.** When the user names a trade count",
                   "Get it from the runtime registry (`installed_runtimes.json`, the same source the engine reads "
                   "for mandates — each entry's `id`) or from `openclaw senpi runtime list`."):
        assert needle in sk, needle


# Rules that bind in SKILL.md and are repeated in a reference so a re-read finds them. Edit both copies
# together: this fails as soon as one copy changes.
DUPLICATED = {
    "one-book.md": (
        "`value_read: \"partial\"` → a strategy whose value covers only some of its wallets: say \"(1 of 2 "
        "wallets)\" from `wallets_loaded` / `wallet_count`.",
    ),
    "guardrails.md": (
        "Keep **floor vs fill** separate: on AVAX the floor was +8.23% and it filled +7.67%, which is 0.55 ROE "
        "points of execution slippage, not calibration.",
        "— and it routes to **`senpi-strategy-author` / `senpi-strategy-ops`** to apply.",
        "\"HYPE ran +18% and you weren't in it\" is only interesting if HYPE is *in the strategy's mandate* and "
        "the miss reveals a gate that's too tight — otherwise it's just an asset the strategy was never "
        "designed to trade.",
        "**Account value ≠ P&L — never narrate the value curve as trades.** The engine sources closed trades "
        "from on-chain fills, so you should not need `strategy_get_pnl_and_account_value_history` for a trade "
        "review at all.",
    ),
    "sources.md": (
        "**Scope the ask.** When the user names a trade count — \"improve my last 10\", \"review my last 20 "
        "trades\" — **pass `--last N`**: it's a GLOBAL bound (the N most-recent closed trades across the whole "
        "book), so the review stays about what they asked instead of the entire 7-day book.",
    ),
    "exit-records.md": (
        "Translate `closeReason`, never paste the enum:",
        "**If the gateway is down**, `senpi dsl closes` exits non-zero and prints *\"start it with: openclaw "
        "gateway run\"*. **Say that.** An unreadable record is an unreadable record — do not fall back to "
        "guessing a mechanism from fill shapes and present it as fact.",
        "Saying it \"detected the position rolling over\" or \"cut early before the hard stop\" invents a "
        "mechanism — a real review did exactly that on a position whose hold time (5.03h vs a 300m timer) was "
        "the giveaway.",
        "You need the **runtime id**, which is the key the event log is addressed by. Get it from the runtime "
        "registry (`installed_runtimes.json`, the same source the engine reads for mandates — each entry's "
        "`id`) or from `openclaw senpi runtime list`.",
        "the ROE it closed at — already at the REAL leverage, so quote it",
        "the full `DslState`, which already holds every number users ask for. Quote these rather than "
        "computing anything:",
        # guardrail 6b's timer rule; the reference words its subject "The three above".
        "are TIMERS, not reactions to price, and none is phase-scoped.**",
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


def _section(text, heading):
    """The body of a SKILL.md section: from its heading line to the next heading of the same or a higher
    level (a "### " section also ends at "## ")."""
    level = len(heading.split(" ", 1)[0])
    body = text.split(heading + "\n", 1)[1]
    m = re.search(r"\n#{1,%d} " % level, body)
    return (body[:m.start()] if m else body).strip("\n")


def _copy(ref_text, title):
    """A "## Copy: SKILL.md "<title>"…" block of a reference, up to the next "## " heading."""
    body = ref_text.split(f'## Copy: SKILL.md "{title}"', 1)[1].split("\n", 1)[1]
    body = body.split("\n## ", 1)[0].strip("\n")
    return body


# Sections past the 5.7 replay, copied whole into the reference a replayed Router row opens. Byte for byte:
# edit both copies together.
COPIED = {
    "sources.md": (("## Sources — onchain vs runtime (the split that keeps you honest)",
                    "Sources — onchain vs runtime"),),
    "output-contract.md": (
        ("## Composition — this skill orchestrates, it doesn't re-implement",
         "Composition — this skill orchestrates, it doesn't re-implement"),
        ("## The output contract — what you produce", "The output contract — what you produce"),
        ("### Fix depth on a saved wallet — advice they apply on Hyperliquid",
         "Fix depth on a saved wallet — advice they apply on Hyperliquid"),
    ),
}


def test_sections_copied_into_a_reference_are_byte_identical():
    raw = _skill()
    for name, pairs in COPIED.items():
        with open(os.path.join(SKILL_DIR, "references", name), encoding="utf-8") as f:
            ref = f.read()
        for heading, title in pairs:
            skill_body, ref_body = _section(raw, heading), _copy(ref, title)
            assert skill_body == ref_body, (
                f"references/{name} copies SKILL.md {heading!r} word for word; the two differ. Edit both "
                f"copies together.")


def test_the_close_reason_list_in_skill_md_names_the_vocabulary_in_exit_records():
    # 6b names the five reasons inline; exit-records.md defines them. Same names, same order.
    raw = _skill()
    line = raw.split("Translate `closeReason`, never paste the enum:", 1)[1].split("(what each means", 1)[0]
    inline = re.findall(r"`([a-z_]+)`", line)
    with open(os.path.join(SKILL_DIR, "references", "exit-records.md"), encoding="utf-8") as f:
        vocab = f.read().split("Translate `closeReason`, never paste the enum:", 1)[1].split("\n\n", 1)[0]
    defined = re.findall(r"(?m)^- \*\*`([a-z_]+)`\*\*", vocab)
    assert inline == defined and len(inline) == 5, (inline, defined)


def test_the_copies_hold_the_base_replayed_rules_past_the_cut():
    # Rules the 1.19.1 replay kept and 5.7 now drops; on a follow-up they are read from these copies.
    def ref(name):
        with open(os.path.join(SKILL_DIR, "references", name), encoding="utf-8") as f:
            return _norm(f.read())
    oc, so = ref("output-contract.md"), ref("sources.md")
    for needle in ("**`strategies[]` (the CURRENT book only) is the sole verdict surface.**",
                   "**no guaranteed-gain language**", "Process-framed, NEVER \"premature / left on the table.\"",
                   "never as a live strategy to act on",
                   "Don't re-implement these — call them and weave their output into the four-part contract below.",
                   "use `senpi-portfolio` for live state."):
        assert _norm(needle) in oc, needle
    for needle in ("`meta.telemetry_source` (`available` / `partial` / `unavailable`) and "
                   "`meta.exit_reason_source_counts` tell you exactly how much enrichment landed; surface that "
                   "honestly.",
                   "**never** report it as a bug.", "it **never** becomes the trade list and **never** re-derives "
                   "a price or PnL.", "And never print the raw status",
                   "quote the maker ratio as a fee-tier signal, never a fee dollar total."):
        assert _norm(needle) in so, needle


def test_the_fee_paragraph_copy_in_sources_is_byte_identical():
    raw = _skill()
    skill_par = raw[raw.index("**Fee $:**"):].split("\n\n", 1)[0]
    with open(os.path.join(SKILL_DIR, "references", "sources.md"), encoding="utf-8") as f:
        ref = f.read()
    assert skill_par == _copy(ref, "Quick actions"), (
        "references/sources.md copies the SKILL.md \"Fee $:\" paragraph word for word; edit both together.")
    assert "quote the maker ratio as a fee-tier signal, never a fee dollar total." in skill_par


def test_the_router_rows_that_reach_the_copies_name_them():
    # A copy only helps a follow-up if a replayed Router row sends the agent to it.
    replayed = _norm(om.replay_57(_skill()))
    for row in ("| shaken out, how my exits fire, why did it close, what is locked now |",
                "| where am I leaking, fees, maker vs taker |"):
        line = replayed.split(_norm(row), 1)[1].split(" | ", 3)
        assert "`sources.md`" in line[1], (row, line[:2])
    for row in ("| master my week, analyze my strategies and trades, suggest improvements |",
                "| the user picks a fix depth |"):
        line = replayed.split(_norm(row), 1)[1].split(" | ", 3)
        assert "`output-contract.md`" in line[1], (row, line[:2])


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
    assert "a wallet with no package stamp is its own row" in o


def test_the_fee_evidence_in_guardrails_says_the_fee_follows_the_plan():
    """From 2026-10-26 the builder fee is none on a paid plan and a flat 0.03% without one; the per-tier
    story is only how older fills read."""
    with open(os.path.join(SKILL_DIR, "references", "guardrails.md"), encoding="utf-8") as f:
        g = " ".join(f.read().split())
    assert ("The builder fee follows the plan, not a constant: none on a paid plan, a flat 0.03% without one. "
            "Read it from the fills. Older fills carry the old tier rates") in g
    assert "per tier" not in g
    assert "flat 0.05%" not in g


# senpi-portfolio/tests/test_your_wallets_wording.py, SECTION_BANNED + its "own book" ban, read whole here.
OWNERSHIP_BANNED = (r"(?i)\bverified\b|\bproo?f\b|\bprov(e|ed|en)\b|\bsignature\b|\b(you|they) own\b"
                    r"|\bowned by\b|\bowning\b|\b(your|their) own (saved )?wallets?\b|\b(your|their) own book\b")


def test_new_references_never_call_a_saved_wallet_connected_verified_or_owned():
    """senpi-portfolio's wording test reads SKILL.md and output-shape.md only; the new files get the same
    bans here: connect(ed), verified, and the proven / owned / "your own wallet" section bans."""
    for name in ("guardrails.md", "exit-records.md", "one-book.md", "sources.md", "output-contract.md"):
        with open(os.path.join(SKILL_DIR, "references", name), encoding="utf-8") as f:
            text = " ".join(f.read().split())
        assert not re.search(r"(?i)connect(?!ion)", text), name
        assert not re.search(r"(?i)(?<![a-z])verified", text), name
        m = re.search(OWNERSHIP_BANNED, text)
        assert not m, f"{name}: …{text[max(0, m.start() - 50):m.end() + 30]}…"
