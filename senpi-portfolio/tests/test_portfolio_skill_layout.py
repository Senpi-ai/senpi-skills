#!/usr/bin/env python3
"""SKILL.md layout for the OpenClaw size limits (senpi-portfolio 1.35.0).

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
# the always-on summary and the hard rules.
REPLAY_NEEDLES = (
    "## Follow-ups first — the router",
    "**Follow-ups: answer only what is new.**",
    "never re-send the wallet list, the book total, or verdicts and figures already shown",
    "| The ask | Step to run | Read first |",
    "| close / pause / keep / top up ONE wallet of a strategy | — | the no-no below: whole strategy only |",
    "| a yes to CTA 1 / CTA 2, or a digit (after the CTAs: that CTA; after the idle menu: that option) | the levers / "
    "the ready options | `references/output-contract.md` (the menu) |",
    "| `strategies` | `references/dsl.md` (protection), `references/mandate.md` (verdicts), `references/runtime-health.md` "
    "(liveness) |",
    "narrating between | `references/output-contract.md`, `references/runtime-health.md` (liveness) |",
    "say the cost first: every firing is a full model call",
    "**Still true on every follow-up**",
    "`total_withdrawable` is idle in strategy wallets, NOT embedded idle cash.",
    "Never add a wallet's `main` + `xyz` views (shared collateral).",
    "realized PnL that didn't load → *unavailable*, never `$0`",
    "A full read never skips a wallet or the CTAs.",
    "A `timeout` open may have filled: check positions, never suggest a retry.",
    "confirm before any change, never trade unprompted; propose, never deploy without confirmation.",
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
)


def test_follow_up_rules_survive_the_57_replay():
    assert_replayed(SKILL, REPLAY_NEEDLES)


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
                   "**Do not offer \"close and relaunch\" for `withdrawable_unavailable`.** Do not send anyone "
                   "to redeploy over a row marked \"no\". (\"do they act?\" in `references/why-not-trading.md`.)",
                   "`timeout`: **Not a rejection — the order may have filled.** Check live positions before saying "
                   "anything, and never suggest retrying until you have.",
                   "`degraded`, book live: *\"running — some scans errored\"*, never *\"your strategy is broken.\"* "
                   "`unknown`: \"runtime liveness unverified — not confirmed running\".",
                   "`status.py`'s `degraded` is our `recovering`, its `unhealthy` our `degraded`.",
                   "**NEVER, for a mirror:**",
                   "**Fees are read, never estimated.**", "**Paper is not live.**",
                   "**Lead the per-strategy read with `profile.description`",
                   "**Never recommend a wholesale close+redeploy of a deliberate book to chase what's hot on a 4h screen.**",
                   "**Never read \"no ratchet record\" as \"no DSL.\"**",
                   "**Asked to run this on a schedule? Say the cost first.**"):
        assert needle in sk, needle


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
        "Never narrate a shadow run as \"live\", \"running\" or \"a trade\" — the user then manages a position that does not exist.",
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
        "Those are **custom-strategy** remedies",
        "A mirror is an intentional copy of a trader, judged on the trader + the multiplier + its",
    ),
    "mandate.md": (
        "Judge it on drawdown control and steadiness, not on whether it caught this week's move.",
        "Only a hedge that fails to pay off in an actual crisis is broken.",
        "Say \"flat, waiting for its setup,\" never \"idle dead weight.\"",
    ),
}


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
