# Classification table — `senpi-strategy-ops` / `senpi-strategy-author` SKILL.md

One row per paragraph. This table, not the diff, is the review artifact for the context
reduction: at ~1060 lines across the two files nobody can review a prose deletion by
reading a patch. Each row says which bucket the paragraph is in, what evidence puts it
there, and where it goes.

> ## ⚠️ Read this before citing any row: the protected set is ABSENT
>
> This table was designed around a *protected set* — rows backed by production transcripts showing
> that a paragraph's teaching steered a real agent decision. **That pass was abandoned on
> 2026-08-12 after repeated infrastructure failures, and the join never happened.**
>
> **Every row in this table is `blame-only` evidence**, whatever an individual row's evidence column
> says: git provenance plus a source citation, and nothing more. No row here demonstrates that a
> paragraph ever changed what an agent did.
>
> **What that costs.** A deletion justified by a `blame-only` row rests on judgment plus a citation.
> That is the exact reasoning that produced four consecutive defects in this same workstream (Task 2,
> fix rounds 2–5): in each case a fact was verified true *somewhere* and then applied in a context
> where its precondition did not hold. The transcript tier existed because it is the one form of
> evidence that does not share that failure mode. It is not available here.
>
> **Coverage.** Three transcripts were pulled before the failures and remain local, un-joined, at
> `.superpowers/sdd/2026-08-12-skills-context-reduction/transcripts/` (git-ignored — they carry real
> user data and must never be committed). Five of the eight refusal codes seen in the last three days
> of production have **no** transcript at all: `E_FUNDS_BELOW_FLOOR`, `E_BUDGET_UNRESOLVED`,
> `E_RUNTIME_REGISTER_FAILED`, `E_STATE_NO_WALLETS`, `E_STATE_AMBIGUOUS_WALLETS`.
>
> Proceeding on this basis was an explicit decision by the repository owner after the risk was
> raised. Reviewers of any cut citing this table should weigh it accordingly, and the final
> whole-branch review should treat "the protected set is absent" as its primary context.

**Tasks 4–7 cite rows from this table.** No paragraph is marked `protected`, because nothing could
earn that mark — see the banner above.

## How to read a row

| Bucket | Meaning | Default disposition |
|---|---|---|
| **1 producer** | A machine surface the agent already reads states this — a runtime refusal/warn string, a CLI verdict, a script's own output. The skill is re-teaching a message the agent gets anyway. | delete → a reference |
| **2 conversation** | Teaching about what to say to the user, when to ask consent, what not to claim. **No machine surface talks to the user**, so nothing else can carry it. | keep resident |
| **3 routing** | How the agent branches — which command runs next, what an exit code means, which skill owns the ask. | keep resident |
| **4 orientation** | What this skill is, its command surface, where files live. The frontmatter is also the trigger surface. | keep resident |

**Evidence strength.** `transcript` = a production session where the code fired and the
agent's next action is observable. `blame-only` = no transcript behind it; the row rests
on git provenance and a source citation, which is weaker and a reviewer should know it.

**`protected`** means a transcript shows this paragraph's teaching steering a real
decision. A protected paragraph may **move** to a reference; it may not be **deleted** on
an R1 citation alone ("the runtime says it too"). If a later task wants it gone it needs a
new argument, not this one.

Transcripts are cited **by local filename only**. They live in the git-ignored
`.superpowers/sdd/2026-08-12-skills-context-reduction/transcripts/` directory and carry
real user data — no MID, wallet address, verbatim user text, or identifying strategy name
appears in this file.

## Baseline

- **Window:** 2026-08-05 → 2026-08-12 (7 days), `telemetry.otel_traces`, spans whose
  `gen_ai.tool.call.result` carries a bracketed code.
- **Population:** 96 distinct sessions across 8 codes. Production runs the **pre-convergence
  skills from `main`**, so the shipped spellings are `E_BUDGET_*`, not the converged
  `W_BUDGET_*`, and `main`'s fat `deploy.py` renders the state codes itself. That does not
  invalidate the baseline: what is being measured is whether a paragraph of skill teaching
  steered a decision, not which spelling fired.

| Code | hits | users | transcript pulled |
|---|---|---|---|
| `E_FUNDS_BELOW_FLOOR` | 64 (d4–7) + 18 (d0–3) | 39 | yes |
| `E_BUDGET_BELOW_STRATEGY_MIN` | 60 (d4–7) + 9 (d0–3) | 2 / 3 | yes |
| `E_FUNDS_SHORT` | 38 (d4–7) + 46 (d0–3) | 15 / 13 | yes |
| `E_RUNTIME_REGISTER_FAILED` | 28 (d4–7) + 14 (d0–3) | 4 / 3 | yes |
| `E_STATE_AMBIGUOUS` | 27 (d4–7) + 25 (d0–3) | 3 / 4 | yes |
| `E_STATE_AMBIGUOUS_WALLETS` | 24 (d4–7) + 6 (d0–3) | 7 / 3 | yes |
| `E_BUDGET_UNRESOLVED` | 22 (d4–7) + 17 (d0–3) | 4 / 6 | yes |
| `E_STATE_NO_WALLETS` | 12 (d4–7) + 8 (d0–3) | 5 / 3 | yes |

**Codes with a SKILL.md paragraph but NO transcript in the window** — every row citing them
is `blame-only` and downstream evidence is correspondingly weaker:
`E_INSTANCE_BINDING_UNKNOWN`, `E_WALLET_OWNED_BY_OTHER_PACKAGE`, `E_ROLLBACK_INCOMPLETE`,
`E_DEPLOY_IN_PROGRESS`, `E_UNIVERSE_NOT_LIVE`, `E_VALIDATE_NO_PROOF`,
`E_VALIDATE_CONTENT_CHANGED`, `E_VALIDATE_RUNTIME_VERSION_CHANGED`,
`E_VALIDATE_UNRESOLVABLE_SCANNER_PATH`, `E_VALIDATE_NO_RECIPE`, `W_BUDGET_PARTIAL_FUND`,
`W_BUDGET_FUNDED_UNREADABLE`, `INVALID_REQUEST`, the PAUSED branch.
Several of these **cannot** appear in this window by construction: they are rendered only
by the converged verb, and production runs `main`. Absence is not evidence of irrelevance
for those — it is evidence that nothing has shipped yet.

**Code in the population with NO SKILL.md paragraph:** `E_RUNTIME_REGISTER_FAILED`
(42 hits / 4 users). Neither file teaches it. Its message carries a noise-filtered CLI
tail and no hint by design (`main:deploy.py:842`, taxonomy row), so the agent is steered
by the cause alone. Noted here so a later task does not read the silence as a deletion.

---

## `senpi-strategy-ops/SKILL.md` (645 lines, 51 blocks)

| Anchor | Bucket | Evidence | Disposition |
|---|---|---|---|
| ops `:1-30` frontmatter (`description` = the trigger surface) | 4 orientation | Nothing else routes an ask into this skill; the `description` is matched, not read. blame `6e23ee20` (2026-06-29 restore) with per-line accretion through `b9ab670e` (2026-08-11) | keep resident |
| ops `:32` H1 | 4 orientation | — | keep resident |
| ops `:34-40` "a strategy is a package" / no scanner daemon | 4 orientation | blame `6e23ee20`, `aef63866` (2026-08-04). No machine surface states the package model; `deploy status` assumes it | keep resident, trim |
| ops `:42-75` the command block | 3 routing | The seven commands are the whole action surface. blame spans `6e23ee20` → `ed78284b` (2026-08-12) | keep resident |
| ops `:77` H2 Deploy | 4 orientation | — | keep resident |
| ops `:79-83` "`status` is the gate" / never report live off a started job | 3 routing | R1 partial: the verb renders `overall` (`orchestrator.ts` `decideOverall`), but **no message forbids claiming live from a running phase** — that confabulation is exactly what the paragraph stops. blame `aef63866` | keep resident |
| ops `:85-89` Step 0 — resolve the id, catalog curl | 3 routing | The catalog URL exists nowhere else in the agent's context. blame `6e23ee20` + `fa1bd982` | keep resident |
| ops `:91-103` Step 0.5 — `deploy.py validate`, universe report, network cost | 3 routing + 1 producer | Routing half (run validate before funding) has no producer. Universe-report half is R1: `deploy.py validate` prints its own findings; taxonomy `E_UNIVERSE_NOT_LIVE`. blame `c9f35c31`, `dbf64756`, `0b31eb29` | keep routing; delete the universe/network prose → `references/lifecycle.md` |
| ops `:105-120` preflight is two questions; `senpi validate` records the proof | 3 routing + 1 producer | Two-command routing has no producer. Proof mechanics are R1: `validate/codes.ts:161`, `deploy/proof-gate.ts`, taxonomy `E_VALIDATE_NO_PROOF`. blame `714f4351`, `4465d3bc`, `b2fc820d` | keep the two-command routing; delete the proof mechanics → `references/lifecycle.md` |
| ops `:123-139` budget tiers, **confirm the amount with the user first**, flags | 2 conversation + 3 routing | **No machine surface asks the user to confirm a split.** The funds refusals say "confirm a lower amount with the user" only *after* a refusal (`main:deploy.py:341-344`; converged `deploy/funding.ts:270-278`) — nothing prompts the confirmation before the money moves. Flags half is 3 routing. blame `98d9f183`, `c1c97f82`, `b0f955d4` (2026-08-06) — TRANSCRIPT_123 | TRANSCRIPT_DISP_123 |
| ops `:141-149` wallet naming, one wallet funds at a time, the race | 1 producer | The report renders `strategyName` per wallet; the sequencing is the verb's own behaviour and observable in `deploy status` phases. No agent decision branches on it. blame `b9ab670e` (2026-08-11) | delete → `references/lifecycle.md` |
| ops `:151-159` re-running resumes; `--budget` is a hard target; **never lower `--budget` to dodge a funding error** | 1 producer (adopt/hard-target) + 2 conversation (never lower without asking) | Adopt-not-double-fund and the hard target are R1 — the refusal names the exact shortfall and `main:deploy.py:341-344` renders "confirm a lower amount with the user". The **prohibition** ("never lower `--budget` to dodge") and "confirm the split before funding" are the conversation half with no producer. blame `b9ab670e` — TRANSCRIPT_151 | TRANSCRIPT_DISP_151 |
| ops `:161-166` ~150s poll budget, exit `6` pending is not a failure | 3 routing | Exit `6` is the branch; nothing in the pending output says "not a reason to re-run `create`". blame `7cc93aef`, `c9f35c31` | keep resident |
| ops `:168-179` inside the job: reconcile → preflight → create → install → observe | 1 producer | `deploy status` prints the phase names while running and the per-step report when terminal. blame `aef63866`, `63a50283` | delete → `references/lifecycle.md` |
| ops `:181-188` `[E_DEPLOY_IN_PROGRESS]`, no cancel, self-freeing slot | 1 producer | Taxonomy `E_DEPLOY_IN_PROGRESS`: the message names the running job and "Watch the running job: `senpi deploy status`". blame `a053ec2a`, `0285f91b` — **blame-only** (no transcript in window) | delete → `references/refusal-playbook.md` |
| ops `:190-192` Step 2 — poll until terminal | 3 routing | — | keep resident |
| ops `:194-207` the exit-code map | 3 routing | This is how the agent branches. The codes are printed; their *meaning* is not. blame `a053ec2a`, `773a5124`, `25c4b605`, `2a5c7da6` | keep resident |
| ops `:209` lead-in | 4 orientation | — | keep resident |
| ops `:211-217` the terminal `overall` table | 3 routing | Five values, five different next actions. `installed-unobserved` in particular has no self-describing next step | keep resident |
| ops `:219-221` gateway restart → `interrupted` | 1 producer | `status` renders `interrupted` with the journal, a fresh read and the resume command. blame `aef63866` — blame-only | delete → `references/lifecycle.md` |
| ops `:223-227` re-running is always safe; no local deploy-state file | 3 routing | No message states the global idempotence — each refusal only names its own next step. The agent's decision *whether to re-run at all* rests here. blame `aef63866`, `319cacac` | keep resident |
| ops `:229-234` behaviour change: deploy never adds funds to an existing wallet | 2 conversation | The report says the amount was NOT added; the instruction to **say it out loud to the user** and to `close.py` first for a fresh wallet has no producer. blame `0ac25d26` (2026-08-04) | keep resident |
| ops `:236-238` no exit block → refused pre-money | 1 producer | `INVALID_REQUEST` at `orchestrator.ts:1972`; the message names the exact edit. blame `6e23ee20`/`e1cdc8de` — blame-only | delete → `references/refusal-playbook.md` |
| ops `:240-245` "Do NOT improvise" — never substitute raw `strategy_create_custom_strategy`, never `runtime create` | 2 conversation + 3 routing | **The load-bearing no-producer case.** A raw MCP create *succeeds*; there is no refusal to relay. Nothing downstream tells the agent this made an empty custom-position strategy with no attribution. blame `aef63866`, `e1cdc8de` | keep resident |
| ops `:246-248` `[E_FUNDS_SHORT]` | 1 producer | `main:deploy.py:341-344` renders "Either add USDC, or confirm a lower amount with the user and re-run `create` with --budget ≤ b*"; converged `deploy/funding.ts:268-278` renders the same two ways forward — TRANSCRIPT_FS | TRANSCRIPT_DISP_FS |
| ops `:249-250` `[E_FUNDS_BELOW_FLOOR]` | 1 producer | `main:deploy.py:332-336` renders "No budget can fund N wallet(s) below the $10/wallet floor … Do NOT retry with a lower --budget: no lower budget is valid", plus the deposit skill by name; converged `funding.ts:259-267` identical — TRANSCRIPT_FBF | TRANSCRIPT_DISP_FBF |
| ops `:251-254` `[E_STATE_AMBIGUOUS_WALLETS]` | 1 producer | `main:deploy.py:772-776` renders "Do NOT hand-register a runtime, and do NOT tear anything down to 'start clean'", the read-only `status.py` next step, and "resolve WITH THE USER which wallet is live". The SKILL paragraph is a near-verbatim restatement — TRANSCRIPT_SAW | TRANSCRIPT_DISP_SAW |
| ops `:255-264` `[E_INSTANCE_BINDING_UNKNOWN]` | 1 producer | `orchestrator.ts:1858`; taxonomy row carries the wallet list, the "not a funding problem", the "re-running changes nothing" and the user's-call rule. blame `03a4e275`, `0272785b` (2026-08-09) — **blame-only**, and the code cannot fire on `main` | delete → `references/refusal-playbook.md` |
| ops `:265-281` `[E_WALLET_OWNED_BY_OTHER_PACKAGE]` | 1 producer | `orchestrator.ts:1940-1960`, which states explicitly that setting `id:` back to the stamp does not reach the wallet. blame `7ae79953`, `c9f35c31` — **blame-only**, cannot fire on `main` | delete → `references/refusal-playbook.md` |
| ops `:282-283` `[E_DEPLOY_IN_PROGRESS]` bullet | 1 producer | Duplicate of `:181-188`. blame `a053ec2a` — blame-only | delete → `references/refusal-playbook.md` |
| ops `:284-290` `[E_ROLLBACK_INCOMPLETE]` | 1 producer | The refusal names the wallet, the amount and the exact reclaim command, and computes the package-wide caveat. Taxonomy row. blame `b0f955d4` (2026-08-06) — blame-only | delete → `references/refusal-playbook.md`, **keep one resident line**: "never leave this one unreported" is a conversation rule with no producer |
| ops `:291-294` PAUSED / mid-teardown | 1 producer | `INVALID_REQUEST` `orchestrator.ts:635` branch names both the resume and `close.py '<id>'`. blame `319cacac` — blame-only | delete → `references/refusal-playbook.md` |
| ops `:295-298` `[E_VALIDATE_UNRESOLVABLE_SCANNER_PATH]` | 1 producer | Taxonomy row; the install gate names `-p <runtime.yaml>` / `--runtime-yaml-dir`. blame `8b1276c9` — blame-only | delete → `references/refusal-playbook.md` |
| ops `:299-315` `[E_UNIVERSE_NOT_LIVE]` incl. the scoped-relay rule and the unreadable-list branch | 1 producer | `universe/package-universe.ts:297`; the refusal names every dead instrument, both forms and the file+key path. blame `dbf64756`, `5bc1d4ea`, `c9f35c31` — **blame-only; cannot fire on `main`** (the gate is post-convergence) | delete → `references/refusal-playbook.md`, **keep the scoped-relay sentence resident**: "never relay it as 'there is no wallet'" is a claim about what the agent must *not say*, and no message says it |
| ops `:316-338` the three proof refusals + the `RUNTIME_VERSION_CHANGED` auto-repair | 1 producer | `validate/codes.ts:161`, `deploy/proof-gate.ts`; taxonomy rows. The repair note is printed to stderr before validation starts. blame `714f4351`, `4465d3bc`, `c9f35c31` — blame-only | delete → `references/refusal-playbook.md`, **keep resident**: "if the call is killed mid-repair, nothing was created — do NOT re-run `create`" (the killed call prints nothing the agent can read) |
| ops `:340-349` "the `W_` prefix means WARNING — the deploy went through" | 3 routing | The prefix→verdict rule is the single branch that stops a warn being reported as a failure. Taxonomy rule 1. blame `c1c97f82` (2026-08-06 rename), `b945ea24`, `123714cc` | keep resident |
| ops `:350-367` `[W_BUDGET_BELOW_STRATEGY_MIN]` — degraded not failed, the scoped close escape, `minBudget` is context | 1 producer **against the converged verb only** | Converged: `orchestrator.ts:810-875` (`buildBudgetEscape`) renders the scoped close command, the stranded-wallet no-command branch, the zero-share manifest sentence and the `E_ROLLBACK_INCOMPLETE` deferral; `:950-963` renders "Nothing was blocked" and the `minBudget`-as-context clause. **`main` renders none of it**: `main:deploy.py:512-517` says only "It will DEPLOY but run DEGRADED … Fund $X+ for the authored design" — no escape, no scoping, no "never close a wallet over it". blame `98d9f183`, `a2135ec3`, `b0f955d4`, `aea8cbef` — TRANSCRIPT_BSM | TRANSCRIPT_DISP_BSM |
| ops `:368-373` `[W_BUDGET_UNRESOLVED]` — lower bound, size conservatively, read `belowMin` | 1 producer (first half) + **R2 risk** (second half) | First half R1 both sides: converged `orchestrator.ts:999`; `main:deploy.py:507-509` renders "may be understated. Size conservatively." **Second half has no producer on `main` and is false there**: `main:deploy.py:505-512` is an `if`/`elif`, so an unresolved sleeve suppresses the below-min check *and* the `belowMin` flag together — "the two are not mutually exclusive, so read `belowMin`" is a claim about the converged verb only. blame `c1c97f82`, `a2135ec3` — TRANSCRIPT_BU | TRANSCRIPT_DISP_BU |
| ops `:374-389` `[W_BUDGET_PARTIAL_FUND]` | 1 producer | Taxonomy row; the warn names each wallet once with both numbers, the percentage and the shortfall, and deliberately carries no close command. blame `bb0171f1` (2026-08-08) — **blame-only**, cannot fire on `main` | delete → `references/refusal-playbook.md`, **keep resident**: "quote the `funded` figure, never the requested one" |
| ops `:390-395` `[W_BUDGET_FUNDED_UNREADABLE]` | 1 producer | Taxonomy row; the warn states UNKNOWN and emits no top-up. blame `bb0171f1` — blame-only, cannot fire on `main` | delete → `references/refusal-playbook.md` |
| ops `:397-405` report from the structured output; **never re-derive a number in prose** | 2 conversation | No machine surface tells the agent not to re-derive. This is the rule that stops "$500 deployed" over a $60 book, and it is the same failure `W_BUDGET_PARTIAL_FUND` exists for | keep resident |
| ops `:407` H3 the funded path | 4 orientation | — | keep resident |
| ops `:409-418` `deploy.py` starts the verb; **`== 2` no longer catches a failure** | 3 routing | The exit-code shift (`2` refused / `3` failed) is a branch the agent silently gets wrong; no output announces the change. blame `e1cdc8de`, `f4499a66` (2026-08-07) | keep resident |
| ops `:420-451` `verify` is READ-ONLY; its `0/3/1` exit map; the ignored flags; `--json` keys | 3 routing (map) + 1 producer (mechanics) | The `0/3/1` map is the branch and has no producer. The rest — which reads it does, what `could-not-check` means, the `next` key, the stderr warning naming each ignored flag — is printed by `verify` itself. blame `a075272c`, `d2af29c1`, `f297e647`, `870e94cf`, `ca323fe2` — blame-only | keep the `0/3/1` map + "the resume is `create`/`runtime`, never `verify`"; delete the mechanics → `references/lifecycle.md` |
| ops `:453-479` the step `verify` names; ACTIVE-only resume; stamp triage | 1 producer | `verify` prints the per-instance step it chose and names the collision; the stamp rules are its own output. blame `a075272c`, `536093fb`, `7ae79953`, `b945ea24` — blame-only | delete → `references/lifecycle.md`, **keep resident**: "the stamp is quoted, never read as proof of who created the wallet" (an inference rule, not a message) |
| ops `:481-484` `deploy.py status [<id>]` — one job record per agent | 1 producer | The mismatch refuses in its own words at exit `1`. blame `e1cdc8de` — blame-only | delete → `references/lifecycle.md` |
| ops `:486-497` host prerequisites — plugin skew, `SENPI_AUTH_TOKEN`, Python 3 | 1 producer (skew) + 4 orientation (env) | The exit-`1` skew message says which side is behind and that nothing was dispatched (`917e4943`, `029e5dae`). The env contract has no producer — a missing token fails downstream and opaquely | keep the env contract resident; delete the skew prose → `references/lifecycle.md` |
| ops `:499` H3 final step | 4 orientation | — | keep resident |
| ops `:501` "How it runs" is REQUIRED on every deploy | 2 conversation | Nothing produces this block. It exists because a `live` report says nothing about how the funded thing behaves. blame `fa1bd982` (2026-07-07) | keep resident |
| ops `:503-505` the three bullets — cadence / scoring / protection | 2 conversation | Each bullet names which YAML field to read and how to say it in plain language. No producer; `deploy status` prints none of it. blame `fa1bd982` | keep resident |
| ops `:507` keep it to ~3 lines; per-instance blocks | 2 conversation | — | keep resident |
| ops `:509-530` worked example "install spider" | 3 routing (illustrative) | Restates `:42-75` + `:123-139` + `:79-83` + `:501-507` end to end. No independent content; blame `fa1bd982`, `aef63866`, `ed78284b` | delete → `references/lifecycle.md` |
| ops `:532` H2 monitor | 4 orientation | — | keep resident |
| ops `:534-547` `status.py` is the single source of truth; copy/manual/no-runtime/runtime-unknown | 3 routing + 1 producer | The routing sentence ("don't hand-compose `strategy_list`") has no producer. The management-mode taxonomy is `status.py`'s own labelled output. blame `6e23ee20`, `f76e19da` (2026-07-05) | keep the routing sentence + "tell the user the management mode — do not call them idle" (2 conversation); delete the mode taxonomy → `references/lifecycle.md` |
| ops `:549-552` DSL coverage verdict + the absence trap | 3 routing | Points at the procedure reference. The trap — an unprotected position shows up as an *absence* — is an inference no surface states. blame `ee91b3a2` (2026-07-01) | keep resident |
| ops `:554-566` don't trust "runtime: running"; the read-only surface list | 3 routing | Six read-only surfaces vs the one money path. This is the list that keeps a monitoring ask off `create`. blame `fe7f9246`, `e1cdc8de` | keep resident |
| ops `:568-570` `runtime_id` = the runtime.yaml `name`; `group == <id>` | 4 orientation | Ledger-free rediscovery; no surface explains the naming convention. blame `6e23ee20` | keep resident |
| ops `:572` H2 close | 4 orientation | — | keep resident |
| ops `:574-585` `close.py`; `strategy_close` is async so re-run to poll; `--all`; orphan cleanup | 3 routing + 1 producer | The re-run-to-poll loop is the branch and `close.py` prints `closing`/`closed` without saying re-run. Orphan cleanup and `--instance` mechanics are the script's own behaviour. blame `6e23ee20`, `b9ab670e` | keep the poll loop + `--all` routing; delete the mechanics → `references/lifecycle.md` |
| ops `:587` H2 applying an edit | 4 orientation | — | keep resident |
| ops `:589-592` the edit itself is authored in `senpi-strategy-author` | 3 routing | Cross-skill ownership; nothing else routes an edit ask. blame `b9ab670e`, `90842e88` | keep resident |
| ops `:594-597` **re-running `create` will NOT apply the edit** | 2 conversation + 3 routing | **No producer, and the machine surface actively misleads**: after an adopt, the deploy reports `overall: live` — which is exactly the confabulation this paragraph stops. blame `07608113` (2026-08-06), `b9ab670e` | keep resident |
| ops `:599-609` steps 1–2: package on disk in the durable root; **prove the edit runs before you close anything** | 3 routing + 1 producer | "Validate before you flatten a live book" has no producer — nothing sequences the two. The `E_VALIDATE_NO_RECIPE` clause is R1 (`validate/codes.ts:37`). blame `b9ab670e`, `b2fc820d`, `1532fade` | keep the sequencing; delete the `NO_RECIPE` clause → `references/refusal-playbook.md` |
| ops `:610-612` **get explicit consent, in these words** | 2 conversation | Market exit, funds return, new wallet, ratchet does not carry over. Nothing on any surface asks for consent. blame `b9ab670e` | keep resident |
| ops `:613-626` `close.py` then `create`; per-sleeve; `--budget` is the WHOLE package's; `want ÷ share`; a warn's re-run figure is a FLOOR | 2 conversation + 3 routing | The arithmetic (`300 ÷ 0.3 → --budget 1000`) and "say the resulting wallet figure to the user, not the `--budget` number" have no producer, and the floor rule is a correction to a number a warn *does* print. blame `2493ad53`, `714f4351` (2026-08-11) | keep resident |
| ops `:628-633` NEVER when applying an edit (hand-render, raw MCP, claim live early) | 2 conversation | Three prohibitions on paths that all *succeed* silently. No producer by construction. blame `b9ab670e`, `07608113` | keep resident |
| ops `:635` H2 invariants | 4 orientation | — | keep resident |
| ops `:637-639` attribution is the package's `id`/`version`; the package is `@senpi-ai/runtime` | 4 orientation | Repo-level invariants (root `CLAUDE.md`); `deploy.py` does the attribution automatically, so this is a claim about *other* paths. blame `6e23ee20`, `bec21642` | keep resident (2 lines) |
| ops `:641` H2 install | 4 orientation | — | keep resident |
| ops `:643-645` install the whole `scripts/` dir | 4 orientation | Packaging contract; failure mode is an import error with no guidance. blame `0844d49e` (2026-06-26) | keep resident |

---

## `senpi-strategy-author/SKILL.md` (417 lines, 51 blocks)

| Anchor | Bucket | Evidence | Disposition |
|---|---|---|---|
| author `:1-21` frontmatter (`description` = the trigger surface, and it carries the DSL boundary) | 4 orientation | The DSL⟹author routing lives in the description, which is matched before the body is read. blame `bec21642`, `6c8969e7`, `41a2b545` | keep resident |
| author `:23` H1 | 4 orientation | — | keep resident |
| author `:25-28` interview, not lecture; the user decides thesis + guardrails | 2 conversation | The whole skill's method. No producer. blame `8cbd1976` (2026-06-25) | keep resident |
| author `:30-39` **DSL ⟹ author here. This is the boundary.** + the Decoupling incident | 2 conversation + 3 routing | **No producer by construction**: a raw `strategy_create_custom_strategy` / `create_position` *succeeds*. Nothing refuses, nothing warns; the strategy is simply unnamed, unsupervised and invisible. blame `41a2b545` (2026-07-03) | keep resident |
| author `:41-52` opening a position is a FORK — ask (A) vs (B); never open into a scanner-managed wallet | 2 conversation | The failure it prevents is silent and *delayed*: the order succeeds, then the DSL reconciler flattens the position as foreign. No surface warns at order time. blame `eadc3ca7` (2026-07-13) | keep resident |
| author `:54` H2 start here | 4 orientation | — | keep resident |
| author `:56-59` scratch is the slow path; offer three ways | 2 conversation | Funnel teaching; no producer. blame `27db3383` (2026-07-04) | keep resident |
| author `:61-69` the three options; hand the thesis hint to `senpi-strategy-discover` | 2 conversation + 3 routing | Cross-skill routing ("don't rebuild the catalog here") plus the offer script. blame `27db3383` | keep resident |
| author `:71-74` example opening | 2 conversation | An illustrative script for `:61-69`. blame `27db3383` | delete → `references/creating-a-strategy.md` |
| author `:76-80` tone — encourage without discouraging; the user's choice is final | 2 conversation | Prevents nagging/re-pitching after a scratch choice. No producer. blame `0e3b2bd7` (2026-07-17) | keep resident |
| author `:82-83` transition to the interview | 4 orientation | — | keep resident |
| author `:85` H2 never guess syntax | 4 orientation | — | keep resident |
| author `:87-91` every identifier from memory is a silent failure; the two live incidents | 2 conversation + 3 routing | **The canonical no-producer class**: a wrong ticker or field name "compiles fine, ticks clean, trades nothing, with no error to tell you". Nothing can produce this because nothing fails. blame `8cbd1976` | keep resident |
| author `:93-99` the source-of-truth table | 3 routing | Five lookups, five destinations. This is where the agent goes instead of recalling. blame `8cbd1976` | keep resident |
| author `:101-103` source beats memory; STOP and ask when you can't find the source | 2 conversation | — | keep resident |
| author `:105-109` when you can't check a source, run the code (`--stage import`); `<recipe-dir>` is the dir holding `runtime.yaml` | 3 routing | The `<recipe-dir>` rule is R1-adjacent (`E_VALIDATE_NO_RECIPE` lists the instances) but is stated here *before* any refusal, which is the point. blame `1532fade`, `8020c9d3`, `eb9feeb9` | keep resident |
| author `:111-115` **`--stage import` is NOT the gate** + the "Validation passed" incident | 1 producer — **`keep — R2`** | `senpi validate --stage import` prints its own `does not prove: that a tick executes`. **But the cited surface demonstrably did not steer**: the recorded incident is an agent reading that exact output and reporting "Validation passed" to the user. An R1 citation here would delete the only thing that caught it. blame `fdd1b496` (2026-08-07 — "The gate was documented correctly and skipped anyway") | **keep — R2**: file an issue on `Senpi-ai/senpi-trading-runtime` — `senpi validate --stage import` should render the non-gate verdict as a **refusal-shaped line the agent cannot paraphrase as a pass**, not as a `does not prove:` footnote |
| author `:117` H2 default behaviour | 4 orientation | — | keep resident |
| author `:119` H3 funding heads-up | 4 orientation | — | keep resident |
| author `:121-125` read the balance ONCE before Decision 1; ~$11.50/wallet incl. the creation fee | 2 conversation + 3 routing | **No machine surface volunteers a balance before a deploy** — `[E_FUNDS_BELOW_FLOOR]` fires *after* the whole build. The `$11.50` figure is also the one number that reconciles "the floor is $10" with a wallet funded to exactly $10 still refusing. blame `ff5a975b`, `9b0a166c`, `b9b2241a` (2026-07-30 → 2026-08-05) — TRANSCRIPT_AFH | TRANSCRIPT_DISP_AFH |
| author `:127-136` the two branches + the one-line heads-up script; never gate the interview | 2 conversation | Explicitly forbids the blocking behaviour the refusal would otherwise induce. No producer. blame `ff5a975b` | keep resident |
| author `:138-140` why this exists (users hit the wall at the last step and left) | 2 conversation (rationale) | Rationale for `:121-136`, not itself a steer. blame `ff5a975b` | delete → `references/creating-a-strategy.md` |
| author `:142-156` the five conversation rules (one question at a time, mine the opening ask, reflect, replay the spec, build in visible stages) | 2 conversation | The skill's entire method; rule 2's "losing a constraint from the first sentence is the #1 mistake" is a named field failure. No producer. blame `8cbd1976`, `8b60f434` | keep resident |
| author `:158-160` pointer to `references/creating-a-strategy.md`; drive from the script, don't read the guide to the user | 3 routing + 2 conversation | — | keep resident |
| author `:162` H2 the 7 decisions / `:164` lead-in | 4 orientation | — | keep resident |
| author `:166-195` the 7-decision question script | 2 conversation | Seven questions with their options and what each maps to. Nothing produces a question. blame `8cbd1976`, `53de603b` | keep resident |
| author `:169-171` (within the above) verify every named ticker against `market_list_instruments`; `xyz:XYZ100` not `xyz:NASDAQ` | 3 routing + 1 producer | R1 exists downstream (`validate_universe.py`, `E_UNIVERSE_NOT_LIVE`) but fires *after* the package is written; here it stops the ticker entering at all. blame `8cbd1976` | keep resident |
| author `:189-195` (within the above) offer the DSL presets; **never hand-roll stops** | 2 conversation + 3 routing | The preset list is the offer script; the path to `references/dsl-presets.yaml` is the routing. blame `8cbd1976`, `53de603b` | keep resident |
| author `:197` H2 after the 7 / `:199-205` build in narrated stages | 2 conversation | Anti-silent-block discipline. No producer. blame `8b60f434` (2026-07-04) | keep resident |
| author `:207-209` lay out the plan in one beat | 2 conversation | — | keep resident |
| author `:211-213` stage 1 — confirm the spec, nothing is written before the yes | 2 conversation | — | keep resident |
| author `:214-223` stage 2 — scaffold under the **durable strategies root**, never inside a managed skill dir; FLAT vs multi-instance layout | 3 routing | **No producer, and the failure is destructive**: a package authored inside a managed skill dir is deleted by the next skill update (the skills-manager wipe class). Nothing warns. The FLAT rule is what keeps `E_VALIDATE_NO_RECIPE` from ever firing. blame `3b0f67d6` (2026-07-31), `53de603b` | keep resident |
| author `:224-234` stages 3–6 — `scoring.py`, `scanners/scan.py`, `runtime.yaml`, `strategy.yaml` | 3 routing | The file plan and what each file is for. blame `8b60f434`, `6c8969e7`, `53de603b` | keep resident |
| author `:235` stage 7 — unit-test `scoring.py` | 3 routing | — | keep resident |
| author `:236-250` stage 8 — the three lint commands; "fast feedback, not a verdict"; the universe-enforcement note | 3 routing + 1 producer | Three commands with absolute paths = routing. The `E_UNIVERSE_NOT_LIVE` scoping prose is R1 (`universe/package-universe.ts:297`) and duplicates ops `:299-315`. blame `eb9feeb9`, `3b0f67d6`, `b2fc820d`, `2a1da729` | keep the three commands + "a clean lint does not mean the strategy works"; delete the universe prose → `references/refusal-playbook.md` |
| author `:251-265` stage 9 — **THE GATE**; which dir to point at; do not narrow it | 3 routing | The `--stage`/`--scanner`/`--no-attest` rule is what makes the run record a proof at all — and a narrowed run *passes*, so the failure is silent. blame `eb9feeb9`, `b2fc820d` | keep resident |
| author `:267-283` PASS / UNPROVEN / FAIL and the UNPROVEN diagnosis | 3 routing (the three outcomes) + 1 producer (the diagnosis) | Three outcomes = the branch. The UNPROVEN diagnosis — which of two things it is, "the finding names the line it returned from", "no setups right now is not one of the possibilities" — is rendered by `senpi validate` itself. blame `eb9feeb9`, `49de4118` | keep the three outcomes; delete the diagnosis → `references/creating-a-strategy.md` |
| author `:285-288` **quote the three stage lines verbatim**; if `live` is not in what you paste you did not run the gate | 2 conversation | An evidence rule about the agent's own claim. No producer — and `:111-115` is the recorded case of it being violated. blame `fdd1b496` | keep resident |
| author `:290-293` `E_VALIDATE_NO_RECIPE` means your layout is wrong | 1 producer | `validate/codes.ts:37`; the refusal lists the instances to pick from. Duplicates author `:251-265` and ops `:105-120`. blame `fdd1b496` — blame-only | delete → `references/refusal-playbook.md` |
| author `:295-300` fix → re-run is a loop, **and it has a stop** (two attempts, then report and let the user decide) | 2 conversation | Nothing counts the agent's attempts. This is the only brake on an edit-loop against an already-unproven package. blame `49de4118` (2026-08-06) | keep resident |
| author `:302-307` what PASS does **not** mean + the two passing-but-wrong examples | 2 conversation + 1 producer (partial) | `senpi validate` "says as much in its own output" — the paragraph admits its own R1. The two field examples (a Supertrend returning one direction; a cooldown on a nonexistent ctx field) are what make it concrete, and neither is in any message. blame `fdd1b496` | keep the rule resident; delete the two examples → `references/creating-a-strategy.md` |
| author `:309-313` never say ready / never hand to ops without PASS; **you are the last check before real money** | 2 conversation | `verify` reports `live` for a scanner that reads nothing — so the paragraph is a correction to what a downstream surface *will* tell the agent. blame `eb9feeb9`, `b9ab670e` | keep resident |
| author `:315-316` report each stage as it lands | 2 conversation | — | keep resident |
| author `:318` H2 wallets & concurrency / `:320-322` every instance gets its own sub-wallet | 3 routing + 4 orientation | blame `8cbd1976` | keep resident |
| author `:324-335` default to running it alongside; **never tell the user they must stop an existing strategy**; offer deposit or `strategy_withdraw_funds` | 2 conversation | Corrects a wrong claim the agent makes unprompted. No surface says anything about concurrency. blame `8cbd1976`, `9b0a166c` | keep resident |
| author `:337-338` wallet creation happens in the deploy step | 4 orientation | — | keep resident (1 line) |
| author `:340` H2 invariants / `:342-362` the 10 authoring invariants | 3 routing | Every item is a silent-failure class: a guessed tool name, dollars instead of `marginPct`, a gate that ignores `ctx.dry_run`, an unverified ticker. `senpi validate` catches some (wire-schema check) but none of them announce themselves. blame `8cbd1976`, `eb9feeb9` | keep resident |
| author `:364` H2 editing / `:366-368` tune inputs, swap the preset, re-validate | 3 routing | — | keep resident |
| author `:370` H2 handoff / `:372-374` going live is a separate gated loop | 3 routing | — | keep resident |
| author `:376-383` was this an edit to an ALREADY LIVE strategy? confirm in those words | 2 conversation | Same rule as ops `:594-612`, stated on the authoring side where the edit actually happens — the agent that made the edit is the one that must raise it. blame `90842e88`, `b9ab670e` | keep resident (the two copies are the two entry points, not a duplication to collapse) |
| author `:385-410` the 4-step handoff loop: confirm → preflight → deploy → GATE on `overall` | 3 routing + 1 producer | The loop is the routing. The embedded refusal prose (`[E_FUNDS_SHORT]` / `[E_FUNDS_BELOW_FLOOR]`, the `verify` exit map, the adopt-never-topped-up rule) duplicates ops `:246-250`, `:420-451`, `:229-234`. blame `a426fc3f`, `4e83ac27`, `e1cdc8de` | keep the 4 steps + "never tell the user it's live until a report says `overall: live`"; delete the refusal prose → cite ops |
| author `:412-417` **NEVER deploy with `strategy_create_custom_strategy` / `create_position`**; a created strategy with no runtime IS the bug | 2 conversation | Third statement of the same no-producer prohibition (with author `:30-39` and ops `:240-245`). The raw call succeeds; the money is stranded. blame `a426fc3f`, `13d63a4b` | keep resident |

---

## R2 tickets

Bucket-1 rows whose cited message turns out not to say the thing, or to say it in a form
that demonstrably did not steer. These are **not deleted by this plan**.

| Row | Missing sentence | Where to file |
|---|---|---|
| author `:111-115` `--stage import` is not the gate | `senpi validate --stage import` renders its non-gate status as a `does not prove:` footnote. An agent read that exact output and reported "Validation passed" to the user. The verdict line needs to be refusal-shaped — something the agent cannot paraphrase as a pass. | `Senpi-ai/senpi-trading-runtime` |
| ops `:368-373` `[W_BUDGET_UNRESOLVED]` `belowMin` clause | The "the two codes are not mutually exclusive — read `belowMin`" rule is true of the converged verb and **false of `main`**, where `deploy.py:505-512` is an `if`/`elif` and an unresolved sleeve suppresses the below-min check and the flag together. Until the converged verb ships fleet-wide, an agent following this sentence on a `main` box reads an unset flag as "no shortfall". | `Senpi-ai/senpi-trading-runtime` (message), and pin the skill sentence to the verb version |
