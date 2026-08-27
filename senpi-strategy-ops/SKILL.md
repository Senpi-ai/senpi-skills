---
name: senpi-strategy-ops
description: >-
  Deploy / monitor / close a NAMED Senpi trading strategy.
  Use when the user names a strategy to run — "install spider", "deploy polar",
  "set up kodiak", "run the spider strategy", "is my strategy live?", "what am I
  running", "list my strategies" (→ status.py),
  "are my positions protected? / do they have a stop-loss (DSL)?",
  "stop/close/uninstall polar" — and for teardown like "close all strategies",
  "return funds to main", "tear everything down" (→ close.py --all). ALWAYS tear
  down via close.py, never a raw strategy_close (that strands the runtime). ops
  deploys / closes / monitors; it does NOT author or edit strategy files — an edit
  ("make my live strategy more aggressive", change leverage/sizing/DSL) is authored
  in senpi-strategy-author, the only skill that knows the scanner / yaml / DSL
  schema. A strategy is a PACKAGE (strategy.yaml + one runtime.yaml per instance +
  scanners/) the runtime supervises in-process — no scanner daemon. `deploy.py
  create <id> --budget <usd>` takes a package live end to end (it gates the package,
  then runs the runtime's detached deploy job; watch with `senpi deploy status`);
  close.py tears down (stop runtime + strategy_close → flattens positions,
  returns funds). The id (spider, polar, kodiak) is the package folder. NOT for choosing WHICH strategy
  (senpi-strategy-discover) or authoring / editing the strategy files themselves (senpi-strategy-author).
license: Apache-2.0
metadata:
  author: Senpi
  version: "3.6.23"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Strategy Ops — deploy, monitor, close

A strategy is a **package**: `strategy.yaml` (the deploy manifest) + one `runtime.yaml` per instance +
`<instance>/scanners/`. The runtime **spawns and supervises** each `scanners/scan.py`, calling
`scan(inputs, ctx)` every `interval_seconds` and owning everything downstream — signal validation,
sizing/execution, the two-phase DSL exit, risk guard-rails. **There is no separate scanner daemon, no
`push_signal`.** Ops owns the deployed lifecycle. Deploy is **one verb**, run as a **detached job**:
funds preflight → wallet create+fund → runtime install → one observed scanner tick. It returns in ~1s
and you poll until it is terminal.

```
openclaw senpi validate <recipe-dir>                                    # 0a. does it RUN? records the proof create needs
python3 senpi-strategy-ops/scripts/deploy.py validate <id>              # 0b. preflight — structurally deploy-ready? (no money, nothing installed; a bare id is fetched to disk)
python3 senpi-strategy-ops/scripts/deploy.py create <id> --budget <usd> # 1. THE FUNDED PATH: validates, then starts the deploy
openclaw senpi deploy status                                            # 2. poll until terminal; read the verified report
python3 senpi-strategy-ops/scripts/status.py                            # what am I running? (+ health)
python3 senpi-strategy-ops/scripts/close.py <id> | --all                # teardown one strategy | EVERY open strategy
```
**Fund through `deploy.py create|runtime <id>`, not through the bare verb.** Both resolve the package,
run the structural preflight, then start the runtime's `senpi deploy` job, poll it, and relay its
report **verbatim**. The wrapper's value is resolution, that structural pass and the verbatim relay —
**not** a gate the verb lacks: the live-universe gate is the verb's own and it fires **pre-money**. Use
the bare verb for the read-only `openclaw senpi deploy status`, and whenever resolution is not needed.
**Always tear down through `close.py`** (one `<id>` or `--all`) — it deletes the runtime *and* closes
the strategy. A raw `strategy_close` MCP call closes the strategy but **leaves the runtime
registered**, which collides on the next deploy.
Pass the **strategy `id`** for a CATALOG strategy (what `senpi-strategy-discover` hands over, e.g.
`spider`), fetched from the remote if not on disk; for a **locally-authored package, pass its DIRECTORY
path**. Either way the package belongs in the **durable strategies root** (`SENPI_STRATEGIES_DIR` if
set, else the agent workspace `strategies/` dir — normally `/data/workspace/strategies`) and **never
inside a skill directory**: a package written there is destroyed on the next skill update. An on-disk
package is authoritative; an invalid one surfaces its real errors and is never silently replaced by a
remote fetch. Mechanics + state machine: [`references/lifecycle.md`](references/lifecycle.md) · manifest schema: [`references/strategy-yaml-schema.md`](references/strategy-yaml-schema.md).

## Deploy — one lifecycle: start the verb, poll `status` until terminal

> **`status` is the gate, not a suggestion.** A strategy is LIVE only when the deploy report's `overall`
> is **`live`** — every instance created + installed + a **verified scanner tick observed**.
> `installed-unobserved` means the tick was NOT seen inside the wait window — not failure and not
> success; say exactly that. Never report "live" off a started job, a running phase, or an install alone.

**Step 0 — resolve which strategy.** The user's word ("spider") is a strategy **`id`**. Confirm it
exists against the registry (`curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/strategies/catalog.json`);
no match → hand to **senpi-strategy-discover**.

**Step 0.5 — preflight (required).** `deploy.py validate <id>` reports every structural + render
issue in **one pass**, with **no money moved and nothing installed** — a bare catalog id is fetched to
disk, which is also how it resolves that id to a package directory. It also **reports** the live
universe, the gate the deploy verb enforces pre-money, so `validate` is the cheap early read of it,
not the thing holding it. That read is a **network call** (`market_list_instruments`) needing
`SENPI_AUTH_TOKEN`, and it can take a few seconds.

**Preflight is two questions, two commands.** `openclaw senpi validate <recipe-dir>` answers **does it
run** — one real tick against live read-only data, no wallet and no funding — and **a `PASS` is also
what RECORDS the proof `create` refuses without**. So it is **validate → deploy, per instance**: **one
run per instance, pointed at the dir holding that instance's `runtime.yaml`** (the package root for a
flat package, the instance's own dir once `strategy.yaml` lists them), and an edited package needs a
fresh `openclaw senpi validate` before the next `create`. **`UNPROVEN` (exit 2) is not a pass.**
`deploy.py validate <id>` answers the other question, **is the package well formed**. Do not deploy a
package that has not returned `PASS`. The proof: [`references/lifecycle.md`](references/lifecycle.md).

**Step 1 — start the deploy.** Budget splits across instances by `funding_share`, **min $10 each** (the
platform wallet floor) — **confirm the amount with the user first**. Two tiers, and only the first
stops anything: below the $10/wallet floor the deploy **refuses**; a wallet left with less than **its
own** sizing needs still **deploys**, with a `[W_BUDGET_BELOW_STRATEGY_MIN]` warn to relay.
```
python3 senpi-strategy-ops/scripts/deploy.py create spider --budget 300
```
It validates locally, starts the job — which itself refuses pre-money on a dead universe — then polls
`deploy status` and prints the verb's report verbatim. Flags: `--decision-model <model>` (only for a
`decision_mode: llm` action), `--tick-wait <s>` (tick-observation window, default 120; **`0` skips it
and can then never report `live`**), `--max-wait <s>` (ACTIVE-wallet wait, default 150 — an explicit
value also becomes `deploy.py`'s own poll budget), `--json` (clean stdout, notes on stderr).

**Re-running `create` is how you resume**, and it never double-funds: the verb reconciles first and
**adopts a wallet that already exists**, then installs whatever is still missing rather than funding a
second beside it. The **`--budget` is a hard target**: if the live balance can't cover it the deploy
**refuses** with the exact shortfall and **NEVER silently funds less** (the "$1,000 → $10" failure).
Fund/free USDC or confirm a smaller amount, then re-run — **never lower `--budget` to dodge a funding
error**. When the user names a budget per strategy ("$1k on X, $2k on Y"), deploy each with its own
and **confirm the split before funding**.

`deploy.py` polls for ~150s and then **returns**, staying inside the ~180s tool timeout — with one
bounded exception, the stale-proof repair
([`references/refusal-playbook.md`](references/refusal-playbook.md)), whose note reaches stderr
*before* validation starts, so **a call killed mid-repair still says nothing was created**: read
`openclaw senpi deploy status`, and do NOT re-run `create`. A job still running at the lapse is **exit
`6` / pending** with the snapshot and the command to watch it — normal on a slow funding leg or a long
scanner interval, **not** a failure and not a reason to re-run `create`.

Inside the job, per instance: **reconcile → preflight → create → install → observe** — the phase names
`deploy status` prints while it runs; an existing live wallet is adopted at reconcile, never
duplicated, and only a **fresh** tick counts at observe. Per-step mechanics:
[`references/lifecycle.md`](references/lifecycle.md).

**Only one deploy runs at a time.** A second start is refused, naming the running job — watch that one.
**There is no `cancel` verb:** undeploying is closing the strategy (`close.py <id>`), not stopping the
job; a wedged deploy abandons itself at a step boundary and frees the slot on its own.

**Step 2 — poll `openclaw senpi deploy status` until it is terminal.** While running it prints the phase
as an in-progress fact; once terminal, the per-instance report with its evidence quoted (`totalFunded`,
the scanner row's `lastRunStatus`/`runCount`).

**Terminal outcomes** — the exit code and the report's `overall` are one branch table (for both
`openclaw senpi deploy status` and `deploy.py`):

| exit | `overall` | Meaning | What to do |
|---|---|---|---|
| `0` | `live` | every instance installed **and** a scanner tick observed | report live + the **How it runs** block. A `warn:` line (`[W_BUDGET_*]`) may ride a `live` report — relay it; it did **not** stop the deploy (see the relay contract below) |
| `4` | `installed-unobserved` | installed, no tick seen inside `--tick-wait` (or `--tick-wait 0` skipped the check) | say exactly that; check `openclaw senpi scanner -r <runtimeId>` in a few minutes. External scanners legitimately tick on long intervals. **`--tick-wait 0` can never report `live`** — nothing was verified |
| `6` | `pending` | a wallet still funding, or the job still running when the poll budget ran out | re-run the same deploy command — it resumes and adopts the wallet |
| `2` | `refused` | a gate said no (`[E_FUNDS_*]`, `[E_VALIDATE_*]`, `[E_UNIVERSE_NOT_LIVE]`, `[E_STATE_AMBIGUOUS_WALLETS]`, `[E_INSTANCE_BINDING_UNKNOWN]`, `[E_WALLET_OWNED_BY_OTHER_PACKAGE]`, `[INVALID_REQUEST]`) | **do what the refusal's code says** — per-code depth in [`references/refusal-playbook.md`](references/refusal-playbook.md); nothing was created past it |
| `3` | `failed` | a step genuinely failed (backend rejection, install error, scanner erroring) | read the quoted cause, fix it, re-run — **except `[E_INSTALL_INDETERMINATE]`** (an install whose outcome is UNKNOWN — nothing to fix, and the money may still be out: do its read first) **and `[E_WALLET_INSTALL_IN_FLIGHT]`** (a race, not a defect — "fix it" is not "clear the stuck install"; **never** `runtime delete` here). Both: per [`references/refusal-playbook.md`](references/refusal-playbook.md) |
| `5` | `interrupted` | a gateway restart killed the job mid-run | **nothing resumes on its own** — re-run the deploy; it reconciles |

**`2` is any gate saying no with nothing created past it** — the verb's refusals, and `deploy.py`'s own
structural preflight when `create`/`runtime` fail it (nothing started; fix what it names and re-run — a
bare retry refuses identically). **`1` is "could not answer", never an outcome**, and it is also what an
unrecognised status returns, so a new status can never read as success. Branch on the code.

**Re-running is always safe.** There is **no local deploy-state file**: the backend strategies and the
runtime registry are the record, so re-running the same command reconciles and adopts whatever already
exists instead of duplicating it. A wallet still **initializing** is **waited for** — but only the
statuses that resolve on their own are (a PAUSED strategy never becomes ACTIVE by itself, so it is
refused, not waited for — see the PAUSED section of
[`references/refusal-playbook.md`](references/refusal-playbook.md)).

> **Behaviour change from the old three-step flow.** `create` used to refuse an already-running `<id>`
> and to close a runtime-less one to force a fresh wallet; the verb **adopts** instead. Two
> consequences worth saying out loud to the user: deploy **never adds funds to a wallet that already
> exists** (ask for $500 against a wallet holding $100 and the report says the $500 was NOT added — top
> up separately, or `close.py` and redeploy), and "re-run to get a fresh wallet" is no longer a thing.

**Packages with no exit block are refused before anything is funded** — worth knowing *before* you
build or pick one; the refusal names the offending instances and computes its own re-check block
([`references/refusal-playbook.md`](references/refusal-playbook.md)).

> **Do NOT improvise.** A package strategy is a **runtime-supervised scanner** — deploy it **only** via
> `deploy.py create|runtime`. Never substitute a raw `strategy_create_custom_strategy` MCP call: that
> makes an **empty** custom-position strategy, not the running scanner, and it carries no attribution.
> Never reach for `openclaw senpi runtime create` — it is internal and skips the funds preflight, the
> attribution and the verified tick. Funding is **automatic** (HL perps → HL spot → EVM bridge).
> **Follow the refusal's code, exactly**: [`references/refusal-playbook.md`](references/refusal-playbook.md).

### Refusals and warns — the relay contract

Every refusal and warn is **rendered by the runtime against the state it actually read**, and names its
own next step. So:

- **Relay it verbatim** — **the figure it names, not one beside it** (a report carries the requested
  amount too; quoting that one is the wrong number). Never re-derive a number or a lifecycle claim.
- **Execute the step it names** — never improvise one, never widen its scope. If it names a read-only
  triage, that is the step. **Never substitute a destructive escape for a named non-destructive one.**
- **If it names no command, that is the answer**, not a gap for you to fill. Some reports
  deliberately carry none, because no safe command exists for that state.
- **`W_` is advisory — it blocked nothing.** Never report a deploy as failed *because of* a warn,
  and never close a wallet over one. A warn rides `failed` and `pending` reports too, so `overall`
  still decides whether it went through. `E_` means refused or failed.
- **One report, one teardown instruction.** Where `[E_ROLLBACK_INCOMPLETE]` appears it owns the
  cleanup — do that first, follow its command exactly, and **tell the user either way**: a funded,
  unwatched wallet is never left unreported.
- **A pre-money gate says what THAT RUN did, not what exists.** The universe and proof gates read one
  instrument list, or the package's own files, and stop before reading this package's live state — so
  their "nothing was created" is scoped to that run. Never widen it to "there is no wallet": on a
  resume the package may already own a funded, live wallet, and the unscoped sentence is what funds a
  second one beside it.

**Two money rules the report cannot enforce for you** — and, when a code fires, the per-code depth is
in [`references/refusal-playbook.md`](references/refusal-playbook.md):
- **Never lower `--budget` to clear a funding refusal without asking the user.** `[E_FUNDS_SHORT]`
  names the exact figure it *can* fund — offer it as a choice, alongside depositing more.
  `[E_FUNDS_BELOW_FLOOR]` means **no** budget is valid: help the user deposit
  (`senpi-deposit-withdraw-transfer`), and never suggest a smaller one.
- **Which wallet is which is the USER's call.** Where a refusal lists live wallets
  (`[E_STATE_AMBIGUOUS_WALLETS]`, `[E_INSTANCE_BINDING_UNKNOWN]`, `[E_WALLET_OWNED_BY_OTHER_PACKAGE]`),
  relay the list and ask. Triage is read-only — run the read the refusal names (`status.py <id>`, or
  `openclaw senpi status` / `strategy_list`). Never close or recreate to "start clean": that can tear
  down a funded live strategy, and a wallet stamped for another package holds someone else's funds.

### Report from the structured output, not raw logs

Then always close with the **How it runs** block below. `funded` is the backend's `totalFunded` and the
tick line is the scanner row's own fields — **quote both verbatim** (the document's shape:
[`references/lifecycle.md`](references/lifecycle.md)).

### The funded path — `deploy.py create|runtime`

`deploy.py` no longer deploys anything itself. Each of its **two** money-moving subcommands resolves the
package, runs the structural preflight, then starts the **same** verb — which holds the live-universe
gate itself, pre-money — polls it, and prints its report verbatim. Both keep the same flags and the
verb's exit codes. **The old `== 2` habit no longer catches a failure**: the pre-verb script exited 2 on
failure, this one exits 3, so anything branching on `== 2` alone silently treats every failed deploy as
a success. Branch on the whole table. **`create` and `runtime` are one path under two names** — either
one resumes, adopting whatever already exists.

> **`deploy.py verify <id>` is READ-ONLY** — it starts no deploy, funds nothing, installs nothing and
> **fetches nothing** (on-disk packages only). It quotes MCP `strategy_list` + `openclaw senpi runtime
> list` + `openclaw senpi status --json`, plus the last deploy job's `[W_*]` warns when that job was
> this package's; it never re-derives a status or a number. Exit codes are its own: **`0`** verified ·
> **`3`** not verified (it names, per instance, what is missing and the one non-destructive next step
> for that state) · **`1`** could not check (a read it needs failed — that is **not** "not live";
> re-read). **The resume is always `create`/`runtime`, never `verify`.** Where it quotes a wallet's
> `skillName` stamp, that is a **quote and never proof of who created the wallet**: any caller of the
> creation tool can write any stamp, so a raw-MCP create or an older/differently-cased id of your own
> renders the same row. Mechanics, the states it will and will not steer at, and the codes it can name:
> [`references/lifecycle.md`](references/lifecycle.md) ·
> [`references/refusal-playbook.md`](references/refusal-playbook.md).

### Host prerequisites
`openclaw` + the `@senpi-ai/runtime` plugin running, and a plugin **new enough to carry the `senpi
deploy` verb** — on a skewed box the start fails at exit `1` saying which side is behind, with
**nothing dispatched**: no job, no wallet, no funds ([`references/lifecycle.md`](references/lifecycle.md)
has both directions and the fix). Also: `SENPI_AUTH_TOKEN` exported (the same token the MCP session
uses); **Python 3 only — no PyYAML/pip needed**. Smoke with `deploy.py validate <id>` first.

### Final step — tell the user HOW each strategy runs (REQUIRED on every deploy)

The user just funded a strategy; the last thing they see must explain **how the thing they funded actually behaves** — not just "it's live." After the `live` report, ALWAYS close the confirmation with a compact **"How it runs"** block per deployed strategy (per instance when their configs differ). Read every value from the **deployed `<instance>/runtime.yaml`** and the package **`strategy.yaml` catalog** — never invent a number. Three things, plain language, no raw YAML:

- **Cadence — how often it acts.** From the `external_scanner`'s `interval_seconds`. If `inputs` carry a slower *decision* clock (`recalibrationHours`, `thesisRefreshHours`, `regimeRefreshHours`), lead with THAT and note the wake interval. Translate to human: `interval_seconds: 300` → "scans every 5 minutes"; `interval_seconds: 21600` + `recalibrationHours: 168` → "re-reads the whole market **weekly**, waking every 6h to act on that read."
- **Scoring — what it grades and the entry bar.** One or two sentences: the catalog `belief_plain`/`thesis` (what signal it scores) + the runtime `inputs` gate (`minScore` and the conviction bands, `leverageTiers`/`marginPctTiers`). e.g. "ranks the book by relative strength + smart-money lean; opens a name only above its score threshold, sizing bigger at higher conviction (leverage steps up base→apex)."
- **Protection — the DSL exit ladder.** From `exit.dsl_preset`: the hard stop (`phase1.max_loss_pct`), the profit-lock ladder (`phase2.tiers`: first `trigger_pct` → top `lock_hw_pct`), and any time cut (`weak_peak_cut`/`hard_timeout`). State whether it has a manual close action or is **DSL-only** (no `CLOSE_POSITION` action → "no manual exits — the stop does all the selling"). e.g. "hard stop at −18% from entry; as a winner runs, a trailing floor ratchets up, locking profit from +8% to +80%; a stalled position is cut at 48h."

Keep it to ~3 short lines per strategy. Multi-instance packages whose legs differ (e.g. a long book vs a short book, core vs ballast) get one block each **or** a shared block that names the per-side difference. This is what turns "it's live" into "here's exactly how it trades" — required even when the user didn't ask.

## Monitor — what am I running? / is it actually live?

**"What strategies am I running?" / "list my strategies" / "is my fleet healthy?"** →
`python3 scripts/status.py` (`<id>` filters, `--fast` skips the per-runtime health call, `--json` for
machine output). It is the single source of truth — live `strategy_list` ∪ `runtime list` (the same runtime-CLI
read `senpi-portfolio` also quotes — neither surface independently confirms the other), never the
ephemeral deploy state — so **don't hand-compose `strategy_list`**. A strategy with **no runtime is not
"broken"**: it is just not autonomous, and `status.py` labels how it is managed (copy, manual, …) — never
call it idle. The one real anomaly is an autonomous package strategy missing its runtime
(**no-runtime**). Full label set: [`references/lifecycle.md`](references/lifecycle.md).

**"Are my open positions protected? / do they have a stop-loss?"** → the DSL coverage verdict (PROTECTED
/ UNPROTECTED / STOP-NOT-ON-VENUE) — a separate read, not the runtime list above. Key trap: an
unprotected position is an **absence** in `senpi dsl positions` — reconcile the tracked set:
[`senpi-trading-runtime/references/dsl-protection-check.md`](../senpi-trading-runtime/references/dsl-protection-check.md).

Do **not** trust "runtime: running" alone. A strategy is **live** only when its runtime is running AND
each instance's `external_scanner` has a recent successful tick. Confirm it on a **read-only** surface
— every one of these is read-only; the money path is `deploy.py create|runtime` (see the funded path
above) and nothing here is it:
- `python3 scripts/deploy.py verify <id>` — the per-instance verdict over the surfaces below
- `python3 scripts/status.py <id>` — the fleet view + each runtime's own health verdict
- `openclaw senpi deploy status` / `deploy.py status [<id>]` — the agent's ONE last-deploy-job record
- `openclaw senpi scanner -r <runtime_id>` — the scanner rows (`lastRunStatus`, `runCount`), the tick itself
- `openclaw senpi status -r <runtime_id> --json` / `state -r <runtime_id> --json`; liveness decision
  tree → [`references/liveness-verification.md`](references/liveness-verification.md); DSL / action /
  position troubleshooting → `openclaw senpi dsl|action …` and
  `senpi-trading-runtime/references/runtime-concepts.md`

`runtime_id` = each instance's `runtime.yaml` top-level `name` (`spider-swing`); they all carry
`group: <id>`, so `openclaw senpi runtime list` matching `group == <id>` rediscovers them ledger-free.

## Close — stop → trigger → (agent polls)

```
python3 scripts/close.py spider          # stop runtime(s) + trigger strategy_close; re-run to poll
python3 scripts/close.py --all           # close EVERY open strategy (all packages) + delete runtimes
python3 scripts/close.py --strategy-id <id> | --address <wallet>   # a wallet with NO package at all
```
Per strategy: **stop the runtime** (if live) → **trigger `strategy_close`** (flattens **all** positions
+ closes the strategy, funds returned). `strategy_close` is **async**, so the script **does not wait** —
it returns `closing` and hands polling to you: **re-run `close.py spider`** until it reports `closed`.
Re-runs are idempotent. `--instance <name>` scopes an instance (needs its live runtime to map; else omit
to close all). **Redeploy** = `openclaw senpi validate` → `close` → `create`, in that order. Discovery
is strategy-driven: close also cleans up an attributed package's **orphaned** (no-runtime) wallet, and
`--strategy-id`/`--address` close one with **no package at all** ([`references/lifecycle.md`](references/lifecycle.md)).

## Applying an edit to a strategy that is already LIVE

"Make my live strategy more aggressive." **The edit is authored in `senpi-strategy-author`**, never here.
**Re-running `create` will NOT apply it** — it is idempotent, so it adopts the existing wallet and leaves
the deployed scanner as it is.

**Apply it in place — `openclaw senpi update`.** No close, no fresh wallet, no market exit; DSL state,
scanner stores and action history survive. `senpi validate <instance-dir>` writes the proof `--apply`
needs; `senpi update <instance-dir> --id <runtime_id>` PLANS, the same with `--apply` commits. **Read the
plan out first**: `dsl_preset` is **forward-only** — new entries only, never one already open (other `exit:`
fields, e.g. `order_type`, DO reach open positions) — never let "tighter" be heard as "my open trades are tighter".

**Only a changed `strategy.wallet`, a renamed or moved external scanner, or a changed `action_type` still need
close-and-redeploy**, which market-exits every open position and drops any custom ratchet ladder — take
**explicit consent in those words first**. Everything else: [`references/editing-a-live-strategy.md`](references/editing-a-live-strategy.md).

## Invariants

- The wallet-creation MCP call carries attribution **`skillName`/`skillVersion` = the package
  `strategy.yaml` `id`/`version`** (not this skill's); `deploy.py` does it automatically.
- The runtime package is **`@senpi-ai/runtime`** — never `@senpi/runtime`.

## Install — include the MCP helper

The scripts in `scripts/` import a vendored MCP helper, `scripts/mcp_client.py`, at runtime.
**Install the whole `scripts/` directory** — omitting `mcp_client.py` fails with `No module named
'mcp_client'`. Stdlib only, no other runtime dependencies.
