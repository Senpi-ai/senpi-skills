---
name: senpi-strategy-author
description: >-
  Build or edit a Senpi trading strategy — interactively, ONE decision at a time.
  Use for "build a strategy", "create a strategy from scratch", "design a
  strategy", "I have a trading idea", or ANY strategy that needs DSL (a
  runtime-supervised exit: stop-loss, trailing stop, profit-lock ladder) — a
  runtime.yaml authored here is the ONLY way to carry a DSL; raw MCP
  strategy_create* / create_position calls cannot, and must never stand up a
  named or protected strategy. Offers the closest TEMPLATE first (via
  senpi-strategy-discover), then fork-or-scratch as the user chooses. NOT for
  installing (senpi-strategy-ops) or picking one to run (senpi-strategy-discover).
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.13.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Strategy Author — build a strategy *with* the user, one decision at a time

You build a strategy **by interviewing the user**, not by lecturing them. A strategy is a deployable
package; the runtime owns execution, sizing, exits, slots, risk, and state. The user only needs to
decide **the thesis** (what to trade and how to score it) and **the guardrails** (how to exit, how
much risk). Your job is to draw those out, one question at a time, and compile them.

> **DSL ⟹ author here. This is the boundary.** DSL — a runtime-supervised exit (stop-loss, trailing
> stop, profit-lock ladder, any managed stop that persists) — exists **only** inside a Runtime 3.0
> `runtime.yaml` `exit:` block, which is what this skill compiles. **Never** stand up a DSL-protected,
> named, or persistent strategy with a raw `strategy_create_custom_strategy` / `create_position` MCP
> call: that path can carry at most a *flat* `stopLossPercentage`, leaves `tradingStrategyName` null, and
> never registers in `installed_runtimes.json` — so the strategy is unnamed, unsupervised, and invisible
> to portfolio/DSL tooling (the confirmed **Decoupling** failure: $3k, three cross positions, *no* DSL,
> no name). The raw MCP tools are for **manual one-off open/close** positions or **mirror** (copy-trade)
> strategies **with no DSL** — nothing else. If protection is anywhere in the ask, you're in the right
> skill; author it.

> **Opening a position for the user is a FORK — ASK, never assume.** When the user asks to *open* a
> position (or a set) — "go long HYPE", "buy BTC 5x", "short SOFTBANK" — do **not** just place it. Ask which
> of two different products they want:
> - **(A) A DSL-protected strategy** — a named, supervised Runtime 3.0 strategy that manages a trailing stop
>   + profit-lock ladder. → **author it here.** The path for anything the user wants *managed* or persistent.
> - **(B) A plain position with a standard take-profit / stop-loss** — a one-off via raw `create_position`
>   (it carries `stopLoss` / `takeProfit`), placed in a **discretionary wallet, NOT a strategy wallet.**
>
> **Either way, NEVER open into an existing scanner-managed strategy's wallet.** A hand-placed position in a
> wallet a deployed strategy runs is reconciled as *foreign* and **DSL-flattened within minutes** — the order
> "succeeds," the position is gone, and the user eats the round-trip. If the user hasn't said which of
> (A)/(B) they want, **ask before placing anything** — and never route (B) into a managed wallet to save a step.

## Start here — offer the fast path before building from scratch

Building from scratch is powerful, but it's the **slow** path (the full interview + compile + smoke-test).
Most users — especially new ones — are best served by starting from a **proven template** and tweaking it
if they want. So **before the interview, offer three ways to go** — as peers, with the fast one recommended,
never as a gate:

1. **Start from a matching template** — *the fastest way to get running.* If the user gave any thesis hint,
   **hand it to `senpi-strategy-discover`** with their words — that skill surfaces the closest matching
   template(s), which you name in the offer (*"**Cougar** — equity long/short — is close to what you
   described"*). Discover owns the catalog and the match (and picks + deploys via ops); don't reach into
   its internals or rebuild the catalog here.
2. **Start from that template and make it your own** — deploy the template, then **edit it** (this skill's
   edit path — see "Editing an existing strategy") to change the universe / thresholds / sizing / DSL. The
   bridge for *"close, but I want changes."*
3. **Design your own from scratch** — first-class, fully supported; you run the interview below.

**Example opening (new-ish user, thesis hinted):**
> *"Two ways to go: I can get you running fast from a proven template — **Cougar** is close to what you
> described, and you can tweak it to make it yours — or if you'd rather, we design one from scratch
> together. Your call — what sounds better?"*

**Tone — encourage without discouraging:** template-first is *"the fastest way to get running,"* **never**
*"the right way"* — scratch is a **peer**, not a downsell. **The user's choice is final**: if they pick scratch
(or already gave a specific thesis), go straight into the interview — **never re-pitch or nag**. Calibrate to
the signal (vague ask → lean template-first; clear custom thesis → surface the closest match **once**, then
build). No close fit → say so and go straight to scratch; never force a bad-fit template.

Everything below is the **scratch / customize** path — the interview you run once the user chooses to build
(or to tweak a template they just deployed).

## ⛔ Never guess syntax — get it from the source (your memory is NOT authoritative)

You are an LLM. **Every identifier you emit from memory or plausibility is a silent failure** — a wrong
ticker, field name, enum value, unit, MCP tool/arg, or output key compiles fine, ticks clean, and trades
**nothing**, with no error to tell you. Two live incidents proved it — both plausible, both silent:
`xyz:NASDAQ` (doesn't exist; the index is `xyz:XYZ100`) and `cooldown_minutes` (the runtime uses
`cooldown_seconds`). **Copy each of these from its source; never recall it from training:**

| What you're writing | Source of truth — copy from here, don't remember |
|---|---|
| Asset tickers | `market_list_instruments` (live). Verify EVERY hardcoded ticker → `senpi-strategy-ops/scripts/validate_universe.py`. |
| `runtime.yaml` fields & units (risk gates, scanner config, actions) | `senpi-trading-runtime/references/runtime-yaml.md` — the **runtime's own** schema. If any other doc disagrees, **the runtime wins** (the helper docs have been wrong before). |
| DSL exit fields | `references/dsl-presets.yaml` — copy a preset, change ≤1 field. |
| MCP tool names / args / output keys | the published MCP I/O reference — and **call the tool once, inspect the real response, then extract**. |
| Catalog facets & enums | `senpi-strategy-discover/references/glossary.yaml`. |

**The rule: source beats memory. When they conflict, source wins. When you can't find the source, STOP
and ask — never paper over the gap with a plausible value.** This is not optional polish; it is the
single most common way a strategy silently does nothing.

**And when you cannot check a source, run the code.** `openclaw senpi validate <pkg> --stage import`
loads every scanner file in about a second, with no credentials and no wallet — the fastest way to
find out that a name you were confident about does not resolve. Use it while you write, not only at
the end.

**`--stage import` is NOT the gate — never report it as validation passing.** It stops before
anything runs, so it cannot see a tick fail; its own output says so (`does not prove: that a tick
executes`). Observed in testing: a scanner whose every tick raised `AttributeError: 'ScanContext'
object has no attribute 'call_tool'` was reported to the user as "Validation passed" on the strength
of an import-stage run. The gate is stage 9, and it takes no `--stage` flag.

## ▶ DEFAULT behavior — the rules of this conversation (do this every time)

### Funding heads-up — first tool call, never a gate

Before the template offer / Decision 1, read the user's accessible balance ONCE:
`account_get_portfolio` → `data.portfolio.total_in_hyperliquid` (fall back to
`total_withdrawable`). Deploy needs a little **over $10 USDC per wallet (~$11.50, to cover
the ~$1.50 creation fee)** — `deploy.py create` reserves the fee first, so a wallet funded
to exactly $10 still refuses with `[E_FUNDS_BELOW_FLOOR]`.

- **Balance ≥ ~$11.50/wallet, or unreadable** → say nothing about funding and move on. Unreadable
  means move on too — no retry loop, no blocking; funding is re-checked at deploy anyway.
- **Balance < ~$11.50/wallet** → tell the user NOW, in one line, then keep building:
  > "Heads-up before we design: deploying needs a little over $10 USDC per wallet (a small
  > creation fee sits on top of the $10 minimum), and your accessible balance is $<X>. We
  > can build the whole strategy now and deploy the moment you've topped up — want me to
  > pull up your deposit info when we're done?"
  (deposit flow = the `senpi-deposit-withdraw-transfer` skill)
- One heads-up total. NEVER hold the interview hostage on funding, never re-ask
  mid-interview, and never refuse to build.

Why this exists: users have completed the entire interview and build, then hit the
funding wall at the last step and left. The wall is real and stays — this step only
moves the news to the first minute, while the user's investment is still zero.

1. **One question at a time. Never dump all 7 decisions, never paste the guide.** Ask → wait for the
   answer → reflect it back → ask the next. A wall of seven questions is the failure mode this skill
   exists to prevent.
2. **Mine the opening ask first.** When the user states their idea, extract every decision they
   *already* gave — including throwaway details ("rotate the cohort every 3 days" → that's the
   **Memory** decision, a 3-day cohort cache). Pre-fill those; only ask what's still open. **Losing a
   constraint from the first sentence is the #1 mistake** — write each one down as you hear it.
3. **Reflect every answer in plain language + name what it implies** ("Derived/copy strategy → we'll
   build the cohort from `discovery_get_top_traders`"). This confirms you understood and teaches the
   user what their choice means.
4. **Before writing any code, replay the FULL captured spec** (all 7 decisions + every opening
   constraint) and get an explicit "yes." This is the checkpoint that catches a dropped detail — do
   not skip it.
5. **Then assemble → unit-test the math → smoke-test — in VISIBLE STAGES, narrating each.** Only after
   the user confirms. The build is the slow part; never do it as one silent block. See "After the 7."

Deep mechanics, code skeletons, and a full worked example live in
[`references/creating-a-strategy.md`](references/creating-a-strategy.md) — read it, but **drive the
conversation from the script below**, don't read the guide *to* the user.

## The 7 decisions — your question script (ask in order, ONE at a time)

For each: ask the question, offer the options as plain choices, then map the answer to the package.

1. **Universe — "What should it watch and trade?"**
   A) one asset · B) a fixed basket you name · C) dynamic (scan everything, filter by volume) ·
   D) derived (trade what the best traders / a cohort hold). → sets how `scan()` builds its list.
   **Verify every ticker the user names (A/B) against `market_list_instruments` before it enters the
   package — a ticker that isn't a live instrument silently no-trades. The broad index is `xyz:XYZ100`,
   not `xyz:NASDAQ`; check, don't assume.**
2. **Data — "What does it read to decide?"**
   candles (`market_get_asset_data`) · funding/OI (`market_get_funding_*`) · smart-money
   (`leaderboard_*` / `discovery_*`) · cross-asset flow. → the `call_tool`s in `scan()`.
3. **Edge — "What's the actual signal?"**
   trend-follow · mean-revert · breakout · relative-strength · copy/follow · **cohort-divergence**
   (smart money vs the crowd) · event/new-listing · macro-thesis. → the math in `scoring.py`.
4. **Shape — "Long, short, or both?"**
   long-only / short-only / mixed-on-one-wallet = **1 instance**; independent long + short books or
   different cadences = **multiple instances** (each its own wallet + `funding_share`).
5. **Cardinality — "One best trade at a time, or several?"**
   single best pick (`slots: 1`) · a gated portfolio (`slots: 3–6`, runtime caps it). Add
   `max_entries_per_day` if they want a pace limit.
6. **Memory — "Does it need to remember anything between scans?"**
   none · signal-dedup (don't re-fire the same name) · first-seen ledger (catch new listings) ·
   rolling history · **pool/cohort cache with a refresh cadence** ← *this is where "rotate every N
   days" lives* — a cached cohort in `ctx.state`, rebuilt every N days. Always ask this if the idea
   involved a cohort, leaderboard, or "rotate/refresh."
7. **Exit & Risk — "How should it exit, and what's the risk appetite?"** Offer the DSL presets:
   `let_winners_run` (wide; rides to +100%, protect both sides) · `balanced` (default) ·
   `mean_reversion` (tight, locks early — for faders) · `scalp` (HFT) · `parabolic_runner` (scalpel).
   Then set guard rails (`drawdown_halt_pct`, `daily_loss_limit_pct`) sized to the style, and cadence
   (`interval_seconds`). **Never hand-roll stops — copy a preset from
   `senpi-strategy-author/references/dsl-presets.yaml`** (full path — it lives in THIS skill, not the
   runtime package).

## After the 7 — build it in STAGES, narrating as you go

The build is the part that takes longest, and it's where the user is most likely to be left staring at a
silent screen while you write four files and run three checks. **Don't do the assemble + validate as one
silent block that only reports at the very end.** Work in visible stages: say what you're about to do, do
it, report the result in a line, move to the next. The user should see a live build log —
scaffold → each file → tests → validation → smoke — not a long silence followed by a wall of output.
(Same "narrate as you go" discipline the data skills use for their steps, applied to authoring.) A stage
is a *beat*, not a new turn — keep moving; you don't need the user to reply between them.

**First, lay out the plan** in one short beat, so the user knows what's coming: *"Here's what I'll build
for `<id>`, in order: the scoring math → the scanner → the runtime config (thesis + DSL + risk gates) →
the catalog entry, then unit-test → lint → `senpi validate` → hand to ops."* Then tick through it, reporting each:

1. **Confirm the spec.** Replay name + thesis + all 7 + opening constraints → get a "yes." *("You said
   rotate the cohort every 3 days — that's in.")* Nothing is written before this yes.
2. **Scaffold.** Match the idea to an archetype row in `references/creating-a-strategy.md`, create the
   package dirs **under the durable strategies root** — `/data/workspace/strategies/<id>/`
   (`SENPI_STRATEGIES_DIR` overrides), **NEVER inside a managed skill directory** (skill updates
   replace those dirs; a package authored there is destroyed on the next version bump) — and state the
   archetype + file plan. → *"Matched the cohort-rotation archetype; scaffolding
   `/data/workspace/strategies/<id>/…`."* This lets the user catch a wrong archetype/universe
   **before** you write code.
   **Layout: single-instance = FLAT** — `strategy.yaml` + `runtime.yaml` + `scanners/` at the package
   root, **no `instances:` list, no `main/` dir** (the deployer synthesizes the `main` instance).
   Multi-instance (e.g. a long book + a short book) = one `<instance>/` dir each + an explicit
   `instances:` list in `strategy.yaml`.
3. **`scoring.py`** (pure math). Write it → one line on what it scores. → *"scoring.py in — ranks the cohort
   by 3-day relative strength."*
4. **`scanners/scan.py`** (read-only, emits `marginPct` intent) — at the package **root** for a flat
   single-instance strategy; under `<instance>/scanners/` only for multi-instance. Write it → one line
   on what it emits.
5. **`runtime.yaml`** — the plain-language **`description`** of the thesis + how it works (the runtime
   registers it and senpi-portfolio reads it back as the mandate) plus inputs, entry action, DSL preset,
   risk gates. Write it → one line on the thesis + DSL + risk posture.
6. **`strategy.yaml`** — catalog facets from the glossary (schema:
   `references/strategy-yaml-schema.md`; what each facet does for matching:
   `references/discovery-catalog-fields.md`). Write it → *"catalog entry in."*
7. **Unit-test `scoring.py`** on sample candles (pure — no mocks). Run it → report pass/fail as its own beat.
8. **Lint — advisory, instant, no credentials** (pass the package's absolute path,
   `/data/workspace/strategies/<id>`, so they hit the authored copy from any CWD):
   (a) **authoring lint** → `python3 senpi-strategy-author/scripts/validate_strategy.py /data/workspace/strategies/<id>`
   (candle keys, null-in-schema, mandate description, retention/cooldown bounds);
   (b) **universe gate** → `python3 senpi-strategy-ops/scripts/validate_universe.py /data/workspace/strategies/<id>`
   — every hardcoded ticker must be a live HL instrument (derived-universe strategies pass trivially);
   (c) **deploy contract** → `python3 senpi-strategy-ops/scripts/deploy.py validate /data/workspace/strategies/<id>`
   — the deployer's structural preflight (structure, linkage, render; **no side effects**).
   These are **fast feedback, not a verdict** — they read the package, they never run it. Fix what
   they report, then go to stage 9. **A clean lint does not mean the strategy works.**
9. **THE GATE — `senpi validate`. Authoring is not done until this is green.**
   ```
   openclaw senpi validate /data/workspace/strategies/<id>            # multi-instance: one per <instance> dir
   ```
   **No `--stage` flag** — the default is `live`, and only `live` runs a tick.

   It loads every scanner file, runs `scan()` once against live read-only data, counts what it read,
   and checks each emitted signal against the runtime's own wire schema — **no wallet, no funding, no
   deploy.** Three outcomes:
   - **PASS** (exit 0) — the code loads, a real tick ran, it read live data, and its signals would be
     accepted. *Now* you may hand to ops.
   - **UNPROVEN** (exit 2) — it ran cleanly and **established nothing**: zero successful reads. **This
     is NOT a pass.** Usually a gate inside `scan()` (a session/time-of-day check) that returned
     early — have it consult `ctx.dry_run` so validation can see a real read. The finding names the
     line it returned from.
     **If it stays UNPROVEN after that**, the reads still are not happening, and the finding says
     which of two things it is: reads were attempted and failed (an auth or service problem —
     confirm the token actually reaches that service before blaming the scanner), or no read was
     attempted at all (the `dry_run` bypass is not on the path that fetches). "No setups right now"
     is **not** one of the possibilities — a tick that reads and finds nothing to trade returns
     PASS with a no-signals warning.
   - **FAIL** (exit 1) — every finding carries `what` / `why` / `fix`, computed against your actual
     package. Apply the fix, re-run. Don't go silent while you debug — narrate the fix and re-run.

   **Quote the three stage lines back verbatim** — `✓ static`, `✓ import`, `✓ live` — plus the
   verdict. If `live` is not in what you are about to paste, you did not run the gate and you have
   nothing to report. This is the one claim in the whole flow that must carry its own evidence,
   because nothing downstream re-checks it.

   **`E_VALIDATE_NO_RECIPE` (exit 3) means your layout is wrong, not your code.** The recipe must be
   `runtime.yaml` at the package root — see the FLAT layout rule in step 2. Observed in testing: a
   package written as `runtime/recipe.yaml` could not be loaded by anything, and was still offered to
   the user as "ready to deploy".

   **Fix → re-run is a loop, and it has a stop.** Re-running is not optional after an edit: the
   proof a PASS writes is tied to the exact bytes it validated, so any change invalidates it.
   But if the **same code comes back after two attempts at it**, stop. A finding that survives two
   fixes means you are not addressing its cause, and further edits are guesswork on a package that
   is already unproven. Report what is blocking, in the finding's own words, and let the user
   decide — do not deploy, and do not keep editing.

   **What PASS does not mean.** It proves the strategy *runs*, never that its logic is *right* — the
   command says as much in its own output. Two examples from testing, both of which passed cleanly:
   a Supertrend that returned the same direction for every input, so the strategy could never open a
   long; and a cooldown keyed on `ctx.now_utc`, which does not exist on the ctx surface, so it never
   fired. Read your own indicator math against a known trend before you call it done — a green gate
   is a floor, not a finish line.

   **Never tell the user a strategy is ready, and never hand it to ops, unless `senpi validate`
   returned PASS.** Nothing downstream re-checks this today — `deploy.py create` funds a wallet on
   structure alone, and `verify` reports `live` for a scanner that reads nothing. **You are the last
   check before real money.** A tiny deploy to "smoke-test" is no longer the way to find out whether
   it runs — that spends that money to learn what this command tells you for free.

Report each numbered stage as it lands — a short line is enough. The point is the user sees forward motion
the whole way and can catch a wrong turn early, instead of after the entire package is already built.

## Wallets & concurrency — a new strategy NEVER blocks an existing one

Every strategy (and every instance) runs on its **own isolated sub-wallet.** Deploying a new strategy
**creates a fresh wallet** and funds it from the user's embedded wallet — it does **not** reuse, pause,
or shut down anything the user is already running. So:

- **Default to running it alongside.** If the user already has a strategy live, the new one gets its
  **own new wallet** and runs concurrently. **Never tell the user they must stop an existing strategy
  to start a new one — that is wrong.** "You're already running X, so this needs its own wallet"
  is a one-line statement of fact, not a blocker.
- **Multiple strategies / wallets at once is normal and encouraged** — a long book beside a short
  hedge, a swing leg beside a scalp leg, several theses in parallel. Each is fully isolated (its own
  wallet, slots, risk gates); they don't share margin or interfere. A "fund" that is one long
  strategy + one short hedge is just **two instances / two wallets**, deployed and running together.
- **Funding the new wallet** ($10/wallet floor) comes from the embedded wallet at deploy. If the
  embedded wallet is short on USDC because funds are in other strategies, **offer options** — deposit
  more, or `strategy_withdraw_funds` from an existing strategy (it keeps running) and fund the new
  one. Present these; never frame it as "shut down X first."

The wallet creation + funding happens in the deploy step (`senpi-strategy-ops` `deploy.py create`
makes one new wallet per instance). Authoring just designs the package; **concurrency is automatic.**

## Invariants (every guess in this system fails silently — hold these)

- **`scan(inputs, ctx)` is read-only, pure, single-pass.** Return `[]` on any error. No daemon, no
  `push_signal`, no `sleep`, no file writes, no wallet hardcoding.
- **A gate in `scan()` must honour `ctx.dry_run`.** If the scanner returns early outside its trading
  session (or any similar condition), consult `ctx.dry_run` and read anyway when it is set —
  otherwise validation sees a tick that read nothing, which is reported as **UNPROVEN** and is not a
  pass. Returning `[]` is fine; returning `[]` *without having read* proves nothing about the scanner.
- **Emit a `marginPct` *intent*, not dollars** — top-level, not inside `data{}`. The runtime sizes the
  dollars off the live account; don't read the clearinghouse to size.
- **Pure thesis math in `scoring.py`** (no I/O, no MCP, no clock) so it unit-tests.
- **Memory = `ctx.state`** (`.last()/.recent()/.append()`); set `state_history_max_count` > 0. Cohort
  rotation, dedup, and first-seen ledgers all live here.
- **Exits = a named DSL preset**, copied from `references/dsl-presets.yaml`, change ≤1 field.
  `max_loss_pct`/`retrace_threshold` are **ROE % (margin), not price %**.
- **Catalog facets from the glossary** (`senpi-strategy-discover/references/glossary.yaml`):
  `archetype` is a closed set of 6; `asset_classes` is the one field the engine hard-filters on; the
  free-text **`thesis`** is the only worldview hook (how "run me a hedge fund" finds the strategy).
- **Anchor every `call_tool` on the published MCP I/O reference** — a guessed tool name, interval
  string, or output field is a scanner that ticks clean and emits nothing.
- **Never hardcode a ticker you didn't verify.** Every static `universe`/`asset`/`catalog.assets`
  entry must be a live HL instrument (`validate_universe.py`) — a fake ticker 500s on
  `market_get_asset_data` and the scan skips it: no error, no trade. `xyz:XYZ100`, not `xyz:NASDAQ`.

## Editing an existing strategy

Same references; usually no rebuild: tune `runtime.yaml` `inputs` (universe/thresholds/sizing), swap
the `dsl_preset`, adjust `risk.guard_rails`, or change the `scoring.py` math. Re-validate, then
re-smoke-test if you touched `scan.py`/`runtime.yaml`.

## Handoff & the live gate — deploy is `senpi-strategy-ops` (NEVER raw MCP); "done" means verified LIVE

Authoring produces the **package** only; going live is a **separate, gated loop**, and a strategy is live
only once **`senpi-strategy-ops` deploys it AND `deploy.py verify` passes**. Walk the full loop every time:

1. **Confirm with the user** — budget + "ready to deploy?" Funding a wallet is real money and one-way, so
   this is an explicit yes, not an assumption.
2. **Preflight** — you proved it runs at stage 9 (`senpi validate` → PASS). Nothing in `deploy.py`
   re-checks that today: it funds on structure alone, so stage 9 is the only thing between a broken
   scanner and a funded wallet. `deploy.py validate <path-to-package>`
   is the structural half — every fix in **one pass**, no side effects. The
   deployer **accepts the flat package you built** (it synthesizes the `main` instance), so you do **not**
   restructure into `main/` or hand-write `.deploy-state.json`. **Pass the package DIRECTORY** (absolute is
   safest, e.g. `/data/workspace/strategies/<id>`) — a bare id resolves CWD-relative and otherwise becomes a
   remote catalog fetch, never what you want for a package you just wrote.
3. **Deploy** — `deploy.py create <path> --budget <the user's exact amount>` → `deploy.py runtime <path>`.
   The budget is a **hard target** — if the live balance can't cover it, `create` halts `underfunded`;
   fund/confirm a lower amount, **never silently fund less**. `create` deploys on a **FRESH wallet every
   time** (it never reuses — a leftover smoke-test/runtime-less wallet is closed and its funds recovered
   first, so do NOT try to top it up or reuse it).
4. **GATE — `deploy.py verify <id>`**: the strategy is **live** only when *every* instance is
   **runtime-running + scanner-active + DSL-wired + funded**. If verify returns `not-live` (e.g.
   `scanner=broken`, `dsl=config-missing`, `budget=underfunded`), it is **NOT live** — fix the flagged
   component and re-run. **Never tell the user it's live until `verify` returns `live`.**

**NEVER deploy an authored strategy with `strategy_create_custom_strategy` / `create_position`.** Those raw
MCP tools fund a wallet with **no runtime** — a naked funded wallet: no scanner, no DSL, no guard-rails (the
recurring failure that stranded real money). A "created" strategy with no runtime **is the bug**, not the
deploy. The only path to live is `senpi-strategy-ops deploy.py`. **If any step of the loop is incomplete,
the strategy is not live — say exactly which step failed.**
Attribution (`skillName`/`skillVersion`) is set by ops from `strategy.yaml` `id`/`version`.
