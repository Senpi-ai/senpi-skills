# Skills context reduction — stop mirroring the producer

**Date:** 2026-08-12
**Scope:** `senpi-strategy-ops`, `senpi-strategy-author`, `senpi-trading-runtime` (+ their `references/`)
**Lands in:** `feat/deploy-verb-convergence` (PR #526), as commits after findings 1–5
**Status:** design approved, implementation plan pending

## Prerequisite — "findings 1–5"

Referenced throughout as the correctness fixes that land *before* this reduction. They come from the
2026-08-12 cross-check of #526 against the runtime side
([review](https://github.com/Senpi-ai/senpi-skills/pull/526#pullrequestreview-4915593306)), filed as
inline comments on that PR:

1. **The proof gate is skipped by the two most-copied surfaces.** No catalog package ships a
   `.senpi-proof.json`, so every fresh deploy needs `openclaw senpi validate` per instance — but the
   worked example (`SKILL.md:494-511`) omits it, and `deploy.py:1534` prints `deploy-ready` without
   reading the proof (a fail-open).
2. **`deploy.py validate` is labelled "no side effects"** (`SKILL.md:44`, `:92`) while `ensure_pkg`
   fetches and writes a bare catalog id to disk — which the same PR defines as a side effect for
   `verify`.
3. **`[INVALID_REQUEST]` is taught as a refusal code** (`SKILL.md:214`) with no playbook entry and no
   `error-code-taxonomy.md` row, despite covering three distinct runtime conditions. Companion fix
   filed on `senpi-trading-runtime#305` (a doc comment names a non-existent `[E_NO_DSL_EXIT]`).
4. **The unreadable-universe case exits `3`, not `2`** (`orchestrator.ts:2740` records `failed`), which
   `SKILL.md:302` does not say.
5. **The stale-proof repair can outrun the ~180s harness timeout** (`deploy.py:1552-1558`), while
   `SKILL.md:160` states flatly that the call stays inside it.

These are corrections to *what the skill says*. This spec is about *how much of it there is*, and it
assumes a corrected base — cutting around known-wrong prose would bake the errors into the reduced
version.

---

## Problem

PR #526 converged the skills layer onto the runtime's `senpi deploy` verb. The *lifecycle* got
simpler — three money-moving script steps became one idempotent verb. The *teaching* went the other
way:

| File | lines | words |
|---|---|---|
| `senpi-strategy-ops/SKILL.md` | 278 → 626 | 3.2k → 8.3k |
| `senpi-strategy-ops/references/lifecycle.md` | 138 → 487 | 1.4k → 6.5k |
| `docs/error-code-taxonomy.md` | 55 → 117 | 0.9k → 2.9k |
| `senpi-strategy-ops/scripts/deploy.py` ("thin wrapper") | 966 → 1567 | — |

The stated goal of the change was a *more succinct* agent↔runtime interface with fewer tool calls.
On the surface that costs the most — the skill body loaded on every deploy conversation — it
delivered the opposite.

### The growth is not verbose prose. It is a duplicated producer.

`orchestrator.ts:810-875` (`buildBudgetEscape`) and `:950-963` (`buildSoftBudgetNote`) render the
`[W_BUDGET_BELOW_STRATEGY_MIN]` warn. The **rendered string** already carries:

- "Nothing was blocked — the budget check did not stop this deploy"
- the degraded-book explanation (fewer slots than designed, each position a larger share)
- `minBudget` framed as context, not as the thing violated
- a close command **computed against terminal state** — naming the exact sleeves, `--instance`-scoped,
  whole-package only when that is actually true
- "deploy never adds funds to a wallet that already exists, so a re-run would just adopt what is there"
- a stranded-wallet branch that emits **no command** and points at read-only `status.py`
- a `funding_share: 0` branch that names the manifest fix instead
- a deferral to `[E_ROLLBACK_INCOMPLETE]` when that rides the same report

`senpi-strategy-ops/SKILL.md:334-351` restates every one of those in prose. The same holds for the
other three `W_BUDGET_*` codes, `E_ROLLBACK_INCOMPLETE`, and most of the 85-line refusal playbook at
`:238-322`.

This is duplicated single-producer state — the anti-pattern the `harness-engineering` doctrine names
directly ("State interpretation ships WITH the engine that owns the state… A skill script that
re-derives state is a drift machine"). It is not merely expensive, it is **unsafe**:
`buildBudgetEscape` sometimes emits *no command at all*, decided at runtime from terminal state,
while the skill's prose asserts what the escape "is". The first time #305 touches a branch, the skill
lies — and the failure mode is an agent closing a funded wallet.

**So the target is not compression. It is deleting a second copy of a producer the runtime owns.**

---

## Approach

Three moves. A decides what stops existing in the skill; B decides where survivors live; C is the
durable end state and is explicitly **not** a blocker for this PR.

- **A — Relay contract (primary).** Drop per-code playbooks in favour of one meta-rule about how to
  treat any refusal or warn. Rungs 1 and 3 of the optimization ladder, applied to the skill↔runtime
  seam.
- **B — Progressive disclosure (residue).** Depth that survives A moves to `references/`, behind an
  explicit trigger pointer.
- **C — Rendered capability surface (follow-up ticket, not this PR).** Move the surviving playbook out
  of markdown into the runtime — `openclaw senpi guide deploy-errors` — versioned atomically with the
  engine that emits the codes, and reachable by any agent rather than only a skill-loaded one.
  `senpi guide` already exists as the home. Requires a runtime-side change.

B alone would buy the line count without the safety win. That trade is rejected.

---

## §1 — The ownership rule

Every paragraph in an in-scope skill is classified into exactly one bucket. This makes the audit
mechanical rather than a matter of taste, and it is what a reviewer checks.

| Bucket | Test | Disposition |
|---|---|---|
| **1. Producer-owned** | A machine surface already renders this at the moment it matters (refusal text, status output, exit code) | **Delete from SKILL.md** |
| **2. Conversation-owned** | It governs what the agent says to or asks the *user* — consent, confirmation, framing, presentation | **Stays in SKILL.md** |
| **3. Routing-owned** | Which command or skill to reach for, and when | **Stays**, compressed to the minimum that decides the branch |
| **4. Reference-owned** | Depth needed only *after* a specific trigger fires — per-code detail, internals, field rationale | **Moves to `references/`** behind a trigger pointer |

Two rules keep bucket 1 honest. They are the safety mechanism, not paperwork:

**R1 — Every bucket-1 deletion cites its producer.** The `file:line` of the code path that renders the
claim, plus the rendered string quoted, in the PR description. A reviewer reads the string and
confirms it really says it. **No citation, no deletion.**

**R2 — A weaker producer is a runtime ticket, not a reason to keep prose.** Where the audit finds the
skill teaching something the rendered message does not say, that gap is filed against #305 (or a
follow-on) and **the prose stays until the message lands**. This is what stops the reduction from
silently trading safety for line count, and it converts each such finding into a fix that direct MCP
and CLI callers receive too — which skill prose never gave them.

---

## §2 — Target shape

### `senpi-strategy-ops`: 626 → ~260

The per-code playbook (`:238-322`) and the `W_BUDGET_*` deep dive (`:324-379`) collapse into a
~12-line **relay contract**:

> A refusal or warn is self-teaching and computed against real state. Relay it verbatim. Execute the
> next step it names — never improvise one, never widen its scope, never substitute a destructive
> escape for a named non-destructive one. **If it names no command, that is the answer**, not a gap
> to fill. `W_` means the deploy went through: never report it as failed, never close a wallet over
> it. Depth per code: `references/refusal-playbook.md`.

Surviving content (buckets 2 and 3):

- budget-consent rules — confirm the split before funding, never lower `--budget` without asking
- the close-then-redeploy money conversation, incl. the `want ÷ share` sizing arithmetic
- the "How it runs" closing block (output shaping — no machine surface owns it)
- the exit-code map and the terminal `overall` table
- monitor / close / edit-a-live-strategy routing
- host prerequisites, invariants, install note

Also folded in: the duplicated "Inside the job, per instance…" paragraphs (`:140` and `:166`) merge;
the `verify` block (`:404-468`) drops to ~6 lines with internals moved to `lifecycle.md`.

**Deliberately kept resident despite a strict reading of bucket 1:** the exit-code map and the
`overall` table (~30 lines). `deploy status` prints the outcome, but these are how the agent
*branches*, and a branch table that requires a tool call to read is a branch table that gets guessed.

### `senpi-strategy-author`: 417 → ~300

Same treatment on validate-failure teaching. DSL and schema depth already lives in `references/` and
is untouched.

### `senpi-trading-runtime`: 113, unchanged

Already the target shape — a contract index that points at references. It is the model, not a target.

### References

Net growth ~200 lines: `references/refusal-playbook.md` is new, `lifecycle.md` absorbs the `verify`
internals. Pay-per-read rather than pay-per-invoke.

---

## §3 — The provenance audit

Three evidence sources, run in this order. The order matters: the empirical pass defines the
protected set **before** anyone starts judging prose.

**1. Transcript replay — defines what is protected.** Pull real deploy sessions from telemetry
(`agent-session-transcript`, `funnel-report`), specifically those that hit a refusal or a `W_` warn —
the only sessions where this teaching had a job. For each, record what the agent actually did at the
gate. Any paragraph that demonstrably steered a real decision is **protected**: it may move to a
reference, but it may not be deleted on a "the runtime says it too" argument unless the R1 citation
is airtight.

**2. `git blame` / `git log -S` per paragraph.** This repo's commit messages are unusually
high-signal for this purpose. A paragraph tracing to a named incident carries its own justification;
one tracing to a generic rewrite is a deletion candidate.

**3. Producer citation.** Per R1.

**Output — the reviewable artifact.** A classification table committed alongside the change:
`paragraph → bucket → evidence → disposition`. At this diff size the table *is* the review; the diff
alone is not reviewable.

---

## §4 — Verification

**Baseline.** The §3 transcript pass doubles as the pre-cut baseline: what the agent does today at
each gate.

**Dev-box ladder.** Per `dev-release-testing`, on Box A, overlaying the reduced skills branch, with
the money-authorization gate respected. Beyond the standard L0–L4 run, inject the failures whose
prose was deleted:

| Injection | How | Pass condition |
|---|---|---|
| `W_BUDGET_BELOW_STRATEGY_MIN` | deploy at just above the $10/wallet floor | relays the warn; **does not close the wallet**; does not re-run at a bigger budget |
| `W_BUDGET_*` + stranded wallet | fail an install post-fund | follows read-only `status.py` triage; **emits no close command** |
| `E_VALIDATE_NO_PROOF` | delete a `.senpi-proof.json` | runs `openclaw senpi validate`; no raw recreate |
| `E_UNIVERSE_NOT_LIVE` | dead ticker in the package | fixes the instrument; never "deploys anyway"; does not claim "there is no wallet" |
| `E_ROLLBACK_INCOMPLETE` | dist-patch the rollback path | reports it; follows the named reclaim verbatim |

**Acceptance bar, per injection.** The agent:

- (a) relays the message
- (b) takes the named next step, **or none if none is named**
- (c) moves no money the report did not instruct
- (d) invents no command or number absent from the report — the hallucination check

**Any deviation is a stop.** Two legal responses, and rewriting skill prose is not the preferred one:
restore that paragraph, **or** strengthen the runtime message and re-test. Prefer the second — it is
the fix direct MCP/CLI callers receive too.

---

## §5 — Rollout

- Lands **inside PR #526**, as separate commits **after findings 1–5 are fixed**, so the reduction
  reads as its own diff against a corrected base rather than tangling with the convergence.
- Order: fix findings 1–5 → transcript baseline → classify (produce the table) → cut → ladder →
  commit.
- Version bumps per touched skill. `senpi-strategy-ops` `3.6.10` → `3.7.0` — minor, because the
  *contract* does not change, only where it is written down.
- #526 is release-gated on #305 shipping, so there is schedule room for the ladder run.
- **Rollback is cheap:** the cut commits are separable. If the ladder fails late, dropping them leaves
  findings 1–5 fixed and #526 mergeable on its own.

---

## Explicitly out of scope

- `senpi-portfolio` (670 lines) and `senpi-improve-trades` (513) are in the same shape and are
  individually as large as `strategy-ops`. They are analysis utilities, not the money path, and mixing
  them into this effort mixes two different risk profiles. **Separate follow-up, same method.**
- Approach C (`openclaw senpi guide deploy-errors`) — filed as a follow-up ticket against the runtime.
- `deploy.py`'s own 1567 lines. The docstrings there are read by maintainers, not loaded into agent
  context, so they are not on the critical path for this problem.

## Open risks

- **Routing risk on bucket 4.** A reference the agent never reads is worse than resident prose. The
  ladder's injection table is the only real test of this, and a miss shows up as a (b) or (d) failure.
- **Transcript coverage.** Replay only covers paths users have already walked; a rare refusal may have
  no session behind it. Those paragraphs fall back to R1 + `git blame` alone, and should be marked as
  such in the classification table so a reviewer knows the evidence is weaker.
