#!/usr/bin/env python3
"""SKILL.md layout for the OpenClaw size limits (senpi-portfolio 1.35.2).

On OpenClaw 10.4 a SKILL.md over the model's cap is refused whole; the repo gate keeps every SKILL.md at
or under 56,000 est chars (tests/test_context_budgets.py), and the long tail lives in `references/`.

On 5.7 every turn after the read sees only what `truncateToolResultText` keeps of it, ported as
`openclaw_measure.replay_57` (tests/context_budget/). This file is over its 16,000 cap and its last 2,000
chars carry no trigger word, so 5.7 keeps the first ~15,9xx chars and nothing of the end. The router,
the follow-up rule, the one-wallet-list rules and the hard rules must be inside that replay, and the file
must stay in that mode: a trigger word near the end switches 5.7 to head ~11.8k + tail ~3.9k.

Rules stay asserted on SKILL.md itself; text that moved is asserted on the reference it moved to.

    python3 -m pytest senpi-portfolio/tests/test_portfolio_skill_layout.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(HERE, "..")
REFS = os.path.join(SKILL_DIR, "references")
sys.path.insert(0, os.path.join(HERE, "..", "..", "tests", "context_budget"))
import openclaw_measure as om  # noqa: E402
from layout import assert_replayed, norm as _norm  # noqa: E402


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


SKILL = _read(os.path.join(SKILL_DIR, "SKILL.md"))


def _ref(name):
    return _norm(_read(os.path.join(REFS, name)))


# ── 5.7 follow-up replay ──────────────────────────────────────────────────────────────────────────────
def test_the_57_replay_stays_head_only():
    trigger = om.important_tail_trigger(SKILL)
    assert trigger is None, (
        f"The last 2,000 chars of SKILL.md contain {trigger!r}. That flips 5.7 to head ~11.8k + tail ~3.9k on "
        f"every follow-up, which drops the hard rules past ~11.8k (the no-no, \"Use this skill FIRST\", "
        f"\"This skill reads\"). Reword the end of the file (Reference index, Skill Attribution, Install) "
        f"without error/exception/failed/fatal/traceback/panic/errno/exit code/total/summary/result/"
        f"complete/finished/done, and do not end it in '}}'."
    )


# What a 5.7 follow-up turn must still see: the router, the follow-up rule, the one-list rules (R1),
# the always-on summary, the hard rules and (1.35.1) the wallet model's balance rules in their own words.
REPLAY_NEEDLES = (
    "## Follow-ups first — the router",
    "**Follow-ups: answer only what is new.**",
    "never re-send the wallet list, the book total, or verdicts and figures already shown",
    "| The ask | Step to run | Read first |",
    "| close / pause / keep / top up ONE wallet of a strategy | — | the no-no below: whole strategy only |",
    "| a yes to CTA 1 / CTA 2, or a digit (after the CTAs: that CTA; after the idle menu: that option), or a "
    "question about a menu option or its numbers | the levers / the ready options | "
    "`references/output-contract.md` (the menu) |",
    "CTA 2: -15% SL is 15% ROE; up to 10x, never a flat 10x; say rotations, never \"pumps\".",
    "| a read failed: `meta.degraded`, `meta.warnings`, `reconciles: false`, a `null` figure | re-run that step | "
    "`references/output-contract.md` (\"Resilience\") |",
    "| `strategies` | `references/dsl.md` (protection), `references/mandate.md` (verdicts), `references/runtime-health.md` "
    "(liveness) |",
    "narrating between | `references/output-contract.md`, `references/runtime-health.md` (liveness) |",
    "say the cost first: every firing is a full model call",
    # 1.35.2: on Box A (5.7) "restart it, it looks stuck" went to raw strategy_list in 3 of 3 runs; the
    # liveness row names that wording so the follow-up runs `strategies` and opens runtime-health.md.
    "| \"is it running / health check / degraded / not running / could not verify / it looks stuck / restart "
    "it\" | `strategies` | `references/runtime-health.md` |",
    "**Still true on every follow-up**",
    "realized PnL that didn't load → *unavailable*, never `$0`",
    "A full read never skips a wallet or the CTAs.",
    "A `timeout` open may have filled: check positions, never suggest a retry.",
    "Confirm before any change; never trade unprompted. Propose; never deploy without confirmation.",
    "**Source of truth for position facts — read before you answer, even mid-trade.**",
    "**\"In profit\" on an open position is unrealized.**",
    "**A Senpi strategy is one row with all its wallets.**",
    "Every wallet the user has is ONE list, `book.wallets`, ordered by value",
    "largest first; ties by label, then address; a wallet that couldn't load last",
    "**Keep the engine's order and never re-section by origin**",
    "Origin is the kind column",
    "**One book total — quote `book.totals.line`.**",
    "never quote the total without it.",
    "Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it.",
    "**Never Senpi money.**",
    "**No write suggestions on these wallets**",
    "never imply Senpi checked who controls them",
    "Never pitch a strategy",
    "**First, when `book.deep_dive.offer` is true**",
    "**Which one do you want me to go deeper on?**",
    "**1. Want me to rebalance or adjust any of these positions?**",
    "**2. Want me to put the idle capital to work in a new strategy?**",
    "**Strategy-first, judged against each strategy's OWN mandate.**",
    "**Use this skill FIRST — before any raw MCP.**",
    "**This includes DSL / \"are my positions protected?\" questions — do NOT hand-roll them.**",
    "**This skill reads. It never changes protection.**",
    "a status question (\"how's it going?\", \"did you finish?\", \"run a health check\") is never a yes.",
    "### HARD rule — the no-no",
    "**Never recommend closing / keeping / topping-up / repurposing a SINGLE wallet or instance of a "
    "multi-wallet strategy.**",
    "act on **all its wallets together** — never a single leg.",
    "There is no restart verb; close → redeploy is a last resort, asked first.",
    "**no strategy trades from the embedded wallet.**",
    "**It is NOT idle cash in the embedded wallet.**",
    "**always say *where***",
    "**These are two views of one wallet, not two separate pools.**",
    "so it is counted **once**, never summed.",
    "**Never add the two views' account values or withdrawables yourself** — that double-counts the shared collateral.",
    "Only the Hyperliquid legs can fund strategies — EVM USDC does not bridge in",
)

# Everything pinned above must end at least this many chars before the 5.7 cut, so a small edit above it
# fails here first instead of silently dropping a rule from the follow-up replay.
REPLAY_MARGIN = 150


def test_follow_up_rules_survive_the_57_replay():
    assert_replayed(SKILL, REPLAY_NEEDLES)


def test_every_replayed_rule_keeps_a_margin_before_the_57_cut():
    mode, kept, _ = om.replay_57_layout(SKILL)
    assert mode == "head-only", mode
    tight = []
    for needle in REPLAY_NEEDLES:
        n = _norm(needle)
        lo, hi = 0, len(SKILL)
        while lo < hi:  # shortest prefix of SKILL.md that contains the needle
            mid = (lo + hi) // 2
            if n in _norm(SKILL[:mid]):
                hi = mid
            else:
                lo = mid + 1
        end = len(om.utf16_units(SKILL[:lo]))
        if kept - end < REPLAY_MARGIN:
            tight.append((needle[:60], kept - end))
    assert not tight, (
        f"These replayed rules end less than {REPLAY_MARGIN} chars before the 5.7 cut at {kept:,}: {tight}. "
        f"Make room above them (move non-rule text out of the head) rather than letting one fall past the cut.")


def test_the_router_names_a_reference_that_exists_for_each_topic():
    router = SKILL.split("## Follow-ups first — the router", 1)[1].split("\n## ", 1)[0]
    named = set(re.findall(r"`references/([a-z-]+\.md)`", router))
    assert {"dsl.md", "mandate.md", "runtime-health.md", "why-not-trading.md", "mirror-strategies.md",
            "analysis-framework.md", "output-contract.md", "output-fields.md"} <= named, named
    for name in named:
        assert os.path.isfile(os.path.join(REFS, name)), name


def test_the_reference_index_lists_every_reference():
    index = SKILL[SKILL.index("## Reference index"):]
    for name in sorted(os.listdir(REFS)):
        if name.endswith(".md"):
            assert f"`{name}`" in index, f"references/{name} is not in the index"


# ── rules that stay in SKILL.md (verbatim from the pre-1.35 text) ─────────────────────────────────────
def test_the_always_on_liveness_rules_stay_in_skill_md():
    sk = _norm(SKILL)
    for needle in ("**say which one, how many, and whether the user needs to do anything**",
                   "**Never call it healthy on the strength of `health=healthy`.**",
                   "Report it as **⛔ NOT RUNNING / UNPROTECTED — funded but no runtime; no scanner, no DSL, no "
                   "guardrails**, and tell the user to redeploy it via `senpi-strategy-ops`.",
                   "say **\"could not verify on this host,\"** never \"running\" and never \"not running.\"",
                   "**only `live` means \"confirmed working.\"**",
                   "**It is your tool, not a step you hand the user**",
                   "**Never send the user to redeploy over it**",
                   "**`close.py` → redeploy** — **last resort, and ask first.** It is a market exit of every "
                   "open position and a fresh wallet. Never reach for it to clear an error you have not "
                   "diagnosed. **There is no restart verb.**",
                   "**Do not offer \"close and relaunch\" for `withdrawable_unavailable`.** Wait, then support. Do not send anyone "
                   "to redeploy over a row marked \"no\". (\"do they act?\" in `references/why-not-trading.md`.)",
                   "`timeout`: **Not a rejection — the order may have filled.** Check live positions before saying "
                   "anything, and never suggest retrying until you have.",
                   "**They disagree.** The last scans errored; the book is live and managed. Say *\"running — some "
                   "scans errored\"*, never *\"your strategy is broken.\"* Confirm with `status.py <id>` before going "
                   "further.",
                   "Say **\"runtime liveness unverified — not confirmed running\"** — never upgrade to "
                   "\"healthy/protected\" or downgrade to \"broken.\"",
                   "**Mind the vocabulary:** `status.py` reports the engine's raw words, and they do not line up with "
                   "this skill's. Its **`degraded`** is this skill's **`recovering`** (one errored tick); its "
                   "**`unhealthy`** is this skill's **`degraded`** (the real fault).",
                   "**NEVER, for a mirror:**",
                   "**Fees are read, never estimated.**", "**Paper is not live.**",
                   "**Lead the per-strategy read with `profile.description`",
                   "**Never recommend a wholesale close+redeploy of a deliberate book to chase what's hot on a 4h screen.**",
                   "**Never read \"no ratchet record\" as \"no DSL.\"**",
                   "**Asked to run this on a schedule? Say the cost first.**"):
        assert needle in sk, needle


# Rules 1.35.0 had moved to a reference; 1.35.1 brought each back to SKILL.md in its b1916b12 words.
# Whole sentences, each as SKILL.md states it (b1916b12 words). One entry per sentence, so deleting any
# clause of a rule fails the test.
RULES_BACK_IN_CORE = (
    # 1.35.1 round 1
    "`running_blind: true` is a third, previously-invisible state: the runtime IS registered and ticking, but its "
    "entry scanners never wired (`running — NO ENTRY SCANNERS`), so it **cannot produce entry signals** — report "
    "**⚠ RUNNING — NO ENTRY SCANNERS**, not a clean \"running.\"",
    "**Do not warn on it.**",
    "Do not put a ⚠ on the strategy, and do not send the user to a diagnostic.",
    "This is the one that always deserved the warning.",
    "Say **\"⚠ runtime degraded — running but not healthy,\"** not a clean all-clear.",
    "Say **\"runtime liveness unverified — not confirmed running\"** — never upgrade to \"healthy/protected\" or "
    "downgrade to \"broken.\"",
    "A runtime *process* that exists (`status: running`) is not a health verdict and never reaches `live` on its own.",
    "**They disagree.** The last scans errored; the book is live and managed.",
    "Say *\"running — some scans errored\"*, never *\"your strategy is broken.\"*",
    "Confirm with `status.py <id>` before going further.",
    "**They disagree.** Do **not** give a clean all-clear — a scanner can read `healthy` and still be blind.",
    "Say it is running but has not found a trade, and use \"No signals at all\" in `why-not-trading.md`.",
    "**When the two disagree, say which one you are trusting and why.**",
    "If the money is moving, that is the headline and the field is the footnote — do not report the field and bury "
    "the evidence.",
    "**Say \"has not CLOSED a trade in Xh\", never \"has not traded\"**: a position carries no open time, so an entry "
    "taken since that close is invisible here.",
    "Quiet while **holding** means fully allocated, not stuck — check slots and entry gates first, and note every "
    "guard rail halts ENTRIES only, so a gate counter is moot while every slot is full.",
    "**`risk_pause`** (beside `runtime_health`, never instead of it) — the runtime's OWN entry gate says `CLOSED` or "
    "`COOLDOWN`: the strategy is **healthy and paused by its own rule** (daily entry cap, daily loss halt, drawdown "
    "halt, a cooldown).",
    "Name the gate and quote its `reason` verbatim (\"Max Entries/Day — Max entries: 4/4 entries today\"), say when "
    "it lets go (`reset`: daily gates at 00:00 UTC, cooldowns on their own; a latched drawdown halt with "
    "`drawdown_reset_on_day_rollover: false` never lets go on its own — it waits for the user's decision, and you "
    "never clear it or resume entries without their explicit approval), and that nothing is broken.",
    "**Never send the user to redeploy over it** — a fresh wallet market-exits the book and starts the same gate "
    "again from zero.",
    "A thorough health check does not stop at the verdict: for a strategy that is `not_running`, `degraded` or "
    "`unknown` — **never for `recovering`** — **`python3 senpi-strategy-ops/scripts/status.py <id>`** re-asks the "
    "runtime directly and returns its verdict **beside a position count**, which is the corroboration above.",
    "Run it yourself and give the answer.",
    "**It is your tool, not a step you hand the user** — never write \"worth running …\" into a portfolio answer; "
    "either run it, or say what you know without it.",
    "**Mind the vocabulary:** `status.py` reports the engine's raw words, and they do not line up with this skill's.",
    "Its **`degraded`** is this skill's **`recovering`** (one errored tick); its **`unhealthy`** is this skill's "
    "**`degraded`** (the real fault).",
    "Reference the right tool to *confirm* — never re-derive its analysis here.",
    "**Before the ladder: if the row carries `runtime_reason`, say it.**",
    "Quote it in those words: it is why the scanner has not traded.",
    "Call it a crash loop only when it says crashes.",
    "No `runtime_reason` means the runtime gave none — never invent one; the ladder finds it.",
    "**When it IS genuinely broken, act — don't hand the user a to-do.**",
    "For `degraded` corroborated by no activity, or `not_running`, work the ladder yourself and report what you "
    "found (the ladder: `references/runtime-health.md`).",
    "Only the last step touches their money, and only that one needs their consent: **`close.py` → redeploy** — "
    "**last resort, and ask first.**",
    "**Nothing qualified.** The entry bar has not been met — name it, never \"being selective\": the binding gate, its "
    "threshold and the universe count from its `runtime.yaml` (`senpi-strategy-ops` "
    "`references/liveness-verification.md`, \"Healthy, live — and it has never traded\").",
    "Its `name` is **\"copy of `mirror_of`\"** — never call it \"unnamed.\"",
    "High leverage or a lopsided book is worth *surfacing* (\"this copies `mirror_of` at 20x — a sharp adverse move "
    "liquidates fast; it has [no] strategy-level stop\"), but the fix is **never** a DSL or a per-position stop.",
    "To add/tighten a downside cap, take profit, or size down → set `stopLossPercentage` / `takeProfitPercentage` "
    "or lower `mirrorMultiplier` **via `senpi-trade`** (it wraps `strategy_update`).",
    "To stop copying → unsubscribe / close the mirror **via `senpi-trade`**.",
    "**These are two views of one wallet, not two separate pools.**",
    "The `withdrawable` (idle cash) is **shared** and reported *identically* in both views — so it is counted "
    "**once**, never summed.",
    "**Per-strategy verdict (the real value).** For **each `strategy_groups[]` entry** (one per real strategy — "
    "never one per wallet), in this order:",
    "Flag any position fighting the tape *for a directional-momentum mandate* / near `liq_px` / oversized.",
    "**Anti-patterns — these exact misreads happened live; never repeat them:**",
    "The deployed `runtime.yaml` is authoritative; a strategy's open book is *evidence about* whether it's "
    "on-mandate, never the definition of the mandate.",
    "**Lead and reason at the `strategy_groups[]` level, not `strategies[]`.**",
    # 1.35.1 round 2 (review of 16907c2f)
    "**Two rules before you answer.** Count the signals rather than describing one, and name the assets that were "
    "skipped, because \"3 eligible entries were skipped\" is the part the user actually feels.",
    "And lead with whether they need to act: for most of these the honest answer is *nothing*, and saying so is "
    "the useful reply.",
    "Say **\"could not verify on this host\"** — never \"protected,\" \"not protected,\" \"running,\" or \"not "
    "running.\" **`null` is not `false`.**",
    "**That whole-fleet pattern means the runtime on this box is too old for this skill's registry read, not that "
    "the strategies are broken.**",
    "Say so in those words, quote the warning, and do not diagnose the strategies from it: no strategy may be "
    "called running, not-running, protected or unprotected off that run.",
    "The fix is a runtime upgrade on the box, not a redeploy of the strategies.",
    "`live`, trading: **They agree.** Clean. Say so.",
    "**Re-run the deploy** — a deploy interrupted by a gateway restart does not resume on its own, and re-running "
    "reconciles rather than duplicating.",
    "This is the fix for `not_running` after a broken deploy.",
    "**Do not offer \"close and relaunch\" for `withdrawable_unavailable`.** Wait, then support.",
    "Give the exchange's own message, which rides the outcome.",
    "Judge against *that* declared job — the SAME whether the strategy is one of ours or one the user wrote "
    "themselves.",
    "1. **Label + mandate.** 2. **Is it doing its job — against its OWN mandate.** 3. **Positions as evidence — "
    "across ALL the strategy's instances.**",
    "4. **PnL — realized + unrealized, summed across the strategy.** 5. **DSL protection — ladder + live tiers.**",
    "State the group's `protected` posture (⟹ **all** instances ship a DSL exit), then **how its DSL works** from "
    "`group.dsl` / `profile.dsl` (hard stop at `hard_stop_roe_pct`, ratchet arms at `arm_at_roe_pct`, the tier "
    "ladder), then **each open position's live tier** from `positions[].dsl`. 6. **Any lever is WHOLE-STRATEGY.**",
    "3. **Portfolio-level read.** Net exposure (net long/short and by sector), concentration (largest position), "
    "idle drag (capital sitting in cash), and the overall posture — is this book hedged, directional, mostly in "
    "cash?",
    "Compare the net tilt to where the broader market is.",
)


def test_rules_brought_back_to_core_stay_in_skill_md():
    sk = _norm(SKILL)
    missing = [n for n in RULES_BACK_IN_CORE if _norm(n) not in sk]
    assert not missing, f"These rules belong in SKILL.md (the maintainer's rule: imperatives stay in core): {missing}"


def test_the_wallet_model_resilience_and_closing_rules_stay_in_skill_md():
    # Summarised in the replayed head ("Still true on every follow-up"); the full rules live here.
    sk = _norm(SKILL)
    for needle in ("**Never add the two views' account values or withdrawables yourself**",
                   "**It is NOT idle cash in the embedded wallet.**", "**always say *where***",
                   "Only the Hyperliquid legs can fund strategies",
                   "don't report an empty portfolio as \"$0.\"",
                   "Report realized PnL as **unavailable** for that strategy — never as `$0`.",
                   "STOP and re-run first (see above); if it persists, surface it and trust the per-wallet (live) figures.",
                   "**Never** report `total_withdrawable` as embedded idle, never skip a wallet, never skip the CTAs",
                   "Confirm before any change; never trade unprompted.",
                   "**Up to 10x**, never a flat 10x", "Say **rotations**, never \"pumps\".",
                   "Propose; never deploy without confirmation.",
                   "**Present each group as one strategy**; never present its wallets/instances as separate strategies."):
        assert needle in sk, needle


# Rules that bind in SKILL.md and are repeated in a reference so a re-read finds them. Edit both copies
# together: this fails as soon as one copy changes.
DUPLICATED = {
    "runtime-health.md": (
        "`status: ACTIVE` only means the strategy *record* exists and is funded — it does **not** mean a runtime is actually running it.",
        "Report it as **⛔ NOT RUNNING / UNPROTECTED — funded but no runtime; no scanner, no DSL, no guardrails**, and tell the user to redeploy it via `senpi-strategy-ops`.",
        "**Never** call a `not_running` strategy \"alive and waiting,\" \"scanner is live,\" or \"DSL-protected\" — that is a false all-clear",
        "**Never send the user to redeploy over it** — a fresh wallet market-exits the book and starts the same gate again from zero.",
        "**Fees are read, never estimated.**",
        "If the fee read fails, say the number is gross and why.",
        "**Paper is not live.**",
        "Never narrate a shadow run as \"live\", \"running\" or \"a trade\"",
        "**`close.py` → redeploy** — **last resort, and ask first.** It",
        "is a market exit of every open position and a fresh wallet. Never reach for it to clear an error you have not diagnosed.",
        "**There is no restart verb.**",
        "This is the honest bar: **only `live` means \"confirmed working.\"**",
        "**It is your tool, not a step you hand the user** — never write \"worth running …\" into a portfolio answer; either run it, or say what you know without it.",
        "*\"running — some scans errored\"*, never *\"your strategy is broken.\"*",
        "\"runtime liveness unverified — not confirmed running\"",
    ),
    "why-not-trading.md": (
        "so **say which one, how many, and whether the user needs to do anything** — never \"I'm not sure why.\"",
        "**Never call it healthy on the strength of `health=healthy`.**",
        "**Do not offer \"close and relaunch\" for `withdrawable_unavailable`.**",
        "Do not send anyone to redeploy over a row marked \"no\".",
        "**Not a rejection — the order may have filled.**",
        "Check live positions before saying anything, and never suggest retrying until you have.",
    ),
    "mirror-strategies.md": (
        "**NEVER, for a mirror:** add a DSL / ratchet;",
        "A mirror is an intentional copy of a trader, judged on the trader + the multiplier + its",
    ),
    "mandate.md": (
        "**Present each group as one strategy**; never present its wallets/instances as separate strategies.",
        "Judge it on drawdown control and steadiness, not on whether it caught this week's move.",
        "Only a hedge that fails to pay off in an actual crisis is broken.",
        "Say \"flat, waiting for its setup,\" never \"idle dead weight.\"",
    ),
}


def _section_body(text, heading, end_marker):
    start = text.index(heading) + len(heading)
    end = text.index(end_marker, start)
    return text[start:end]


def test_the_resilience_copy_in_output_contract_is_the_whole_section():
    # Resilience no longer replays on a 5.7 follow-up (it sits past the cut); the router row "a read failed"
    # sends the agent to this copy, so every bullet must read the same as SKILL.md.
    skill = _norm(_section_body(SKILL, "## Resilience (engine handles; narrate honestly)\n", "\n## "))
    contract_text = _read(os.path.join(REFS, "output-contract.md"))
    contract = _norm(_section_body(contract_text, "that only has the router still reaches them.\n",
                                   "SKILL.md \"The three buckets\", word for word:"))
    assert skill == contract, ("The Resilience copy in references/output-contract.md must read the same as SKILL.md "
                               "\"Resilience\": edit both copies together.")
    assert "Report realized PnL as **unavailable** for that strategy — never as `$0`." in skill


def test_the_three_buckets_copy_reads_the_same():
    # The bucket table's last row, the grand_total formula and the managed-money paragraph sit just past the
    # 5.7 cut; output-contract.md (router rows "analyze my portfolio" and "a yes to CTA 1 / CTA 2") carries
    # the whole subsection word for word.
    sk = _norm(_section_body(SKILL, "### The three buckets\n", "\n## A strategy is ALL its wallets"))
    contract = _norm(_section_body(_read(os.path.join(REFS, "output-contract.md")),
                                   "SKILL.md \"The three buckets\", word for word:\n",
                                   "\n## A strategy is ALL its wallets — a copy"))
    assert sk == contract, "edit SKILL.md \"The three buckets\" and its output-contract.md copy together"
    assert "**`grand_total = idle_in_embedded + idle_in_strategies + deployed_in_positions`.**" in sk


def test_the_strategy_grouping_copies_read_the_same():
    # "A strategy is ALL its wallets" sits past the 5.7 cut; the strategies row (mandate.md) and the full-read
    # row (output-contract.md) open whole-paragraph copies of it.
    sk = _norm(_section_body(SKILL, "## A strategy is ALL its wallets (present + reason at `strategy_groups[]`)\n",
                             "\n### A flat/empty instance"))
    assert sk.startswith("**This is the most important rule in this skill.**")
    assert "at least one strategy spans multiple wallets — be especially careful." in sk
    for name in ("mandate.md", "output-contract.md"):
        assert sk in _ref(name), f"edit SKILL.md \"A strategy is ALL its wallets\" and its {name} copy together"


def _cta_block(text, end_marker):
    start = text.index("Then the two CTAs. CTA 1 and CTA 2 are about managed wallets only")
    end = text.index(end_marker, start) if end_marker else len(text)
    return _norm(text[start:end])


def test_the_cta_block_copy_in_output_contract_is_the_whole_block():
    # The whole "Mandatory closing" CTA block, from "Then the two CTAs." to the end of the section,
    # compared as one text: every sentence (CTA 1's whole-strategy rule, CTA 2's menu and numbers,
    # the top-up line) must read the same in both copies.
    skill = _cta_block(SKILL, "\n## ")
    contract_text = _read(os.path.join(REFS, "output-contract.md"))
    contract = _cta_block(contract_text, None)
    assert skill == contract, (
        "The CTA block in references/output-contract.md must read the same as SKILL.md \"Mandatory closing\" "
        "from \"Then the two CTAs.\" to the end of the section: edit both copies together.")
    assert "**whole strategy (all its wallets)** — never to a single sleeve" in skill
    assert "Topping up an existing *whole* strategy via `strategy_top_up`" in skill


def test_rules_repeated_in_a_reference_read_the_same_as_skill_md():
    sk = _norm(SKILL)
    for name, needles in DUPLICATED.items():
        ref = _ref(name)
        for needle in needles:
            n = _norm(needle)
            assert n in sk and n in ref, (
                f"{needle!r} must read the same in SKILL.md and references/{name}: edit both copies together "
                f"(SKILL.md is the rule; the reference repeats it for a re-read)."
            )


# ── long tail that moved, asserted on the reference it moved to ──────────────────────────────────────
def test_reason_codes_and_no_signal_reads_are_in_why_not_trading():
    ref = _ref("why-not-trading.md")
    for code in ("withdrawable_unavailable", "insufficient_margin", "no_margin_configured", "no_slots",
                 "below_min_notional", "risk_gate_COOLDOWN", "risk_gate_CLOSED", "position_already_exists",
                 "strategy_backend_paused", "position_open_failed", "timeout", "exception", "invalid_direction",
                 "clearinghouse_unknown"):
        assert f"`{code}`" in ref, code
    for needle in ('**Do not offer "close and relaunch" for `withdrawable_unavailable`.**',
                   'Do not send anyone to redeploy over a row marked "no".',
                   "**Not a rejection — the order may have filled.**",
                   "**Never call it healthy on the strength of `health=healthy`.**"):
        assert needle in ref, needle


def test_runtime_health_states_and_ladder_are_in_runtime_health():
    ref = _ref("runtime-health.md")
    for state in ("live", "recovering", "degraded", "not_running", "unknown", "unverified", "mirror"):
        assert f"- **`{state}`**" in ref, state
    for needle in ("**Mind the vocabulary:**", "**There is no restart verb.**",
                   "**`close.py` → redeploy** — **last resort, and ask first.**",
                   "the runtime on this box is too old for this skill's registry read",
                   "### Corroborate the verdict before you assert it"):
        assert needle in ref, needle


def test_explanations_moved_in_1351_are_in_their_references():
    # Not rules: the incidents, engine notes and examples 1.35.1 moved out to make room.
    rationale = _ref("rationale.md")
    for needle in ("can be reconciled as foreign and DSL-flattened within minutes",
                   "a bare number read by eye is how an older close gets called today's.",
                   "un-bucketed dumps that mislead",
                   "it's the dry powder the other half of the design needs to do its job.",
                   "it graded an all-weather core, a crisis hedge, and a waiting strategy",
                   "it opens and closes positions every tick per its DSL and signal logic.",
                   "Recommending action on a strategy that turns out to be already closed",
                   "(a \"strategy-grinder cascade,\" a \"close at 14:58,\" \"funds returned to embedded\")",
                   "(this is a real failure that broke live strategies)"):
        assert needle in rationale, needle
    dsl = _ref("dsl.md")
    for needle in ("`ratchet_stop_list` shows **only** the live ratchet", "It is protected **two ways**",
                   "`SERR031` auth", "runs a built-in DSL exit (trailing stop) + risk guard-rails"):
        assert needle in dsl, needle
    mandate = _ref("mandate.md")
    for needle in ("A strategy is doing its job when its behavior matches its *design*",
                   "a contrarian book is *supposed* to fade the consensus.",
                   "\"behind the current 4h rotation\"", "This is the SAME read whether the strategy is one of ours"):
        assert needle in mandate, needle
    fields = _ref("output-fields.md")
    for needle in ("A strategy row's wallets are grouped by the same key in every step",
                   "`book.totals.managed_usd` is `grand_total_usd`", "enumerates the embedded wallet",
                   "The engine forces a fresh fetch (no 12h cache)", "can't linger as a ghost.",
                   "`strategyName` is optional on `strategy_create_custom_strategy`",
                   "`\"tradingStrategyName\"` is the **package id**", "`all` is the default when no step is given"):
        assert needle in fields, needle
    contract = _ref("output-contract.md")
    for needle in ("You are a sharp portfolio analyst.", "The user wants a read.",
                   "# 1. the FAST money map: embedded idle + each wallet's value → the three buckets (narrate FIRST)"):
        assert needle in contract, needle
    for needle in ("The steps share a state file", "Their raw reads are `external_wallets: {status, wallets}`"):
        assert needle in fields, needle
    assert "Attribution happens downstream when the execution tools / strategy skills act on a CTA." in rationale
    assert "Those are **custom-strategy** remedies" in _ref("mirror-strategies.md")
    framework = _ref("analysis-framework.md")
    for needle in ("The engine computes these as two separate fields precisely so you don't mix them.",
                   "has **$3K now** regardless of what it was funded.", "a 1% price move at 10x is a 10% return on margin."):
        assert needle in framework, needle


def test_mirror_dsl_mandate_contract_and_fields_moved_whole():
    assert "via `senpi-trade`" in _ref("mirror-strategies.md")
    assert "**How a mirror is protected — two ways, neither a DSL:**" in _ref("mirror-strategies.md")
    dsl = _ref("dsl.md")
    for needle in ("`hard_stop_roe_pct`", "`arm_at_roe_pct`", "**`armed: false`**", "Report both:"):
        assert needle in dsl, needle
    mandate = _ref("mandate.md")
    assert "`profile.source`" in mandate and "A tail-risk / crisis hedge is NOT" in mandate
    contract = _ref("output-contract.md")
    for needle in ("**Is it doing its job — against its OWN mandate.**", "**Any lever is WHOLE-STRATEGY.**",
                   "**Portfolio-level read.**"):
        assert needle in contract, needle
    fields = _ref("output-fields.md")
    for needle in ("**`strategy_groups[]` — ONE entry per real strategy", "`registry_source`",
                   "**never crashes on a missing/corrupt state file"):
        assert needle in fields, needle
