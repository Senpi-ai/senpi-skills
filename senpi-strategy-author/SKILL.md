---
name: senpi-strategy-author
description: >-
  Build a Senpi trading strategy from scratch — interactively, ONE decision at a
  time. Use when the user wants to create, design, or build a new autonomous
  strategy: "build a strategy", "help me build a trading strategy", "create a
  strategy from scratch", "walk me through building a strategy", "design a
  strategy", "I have a trading idea". USE THIS SKILL TO CREATE ANY STRATEGY THAT
  HAS DSL — a runtime-supervised exit (stop-loss / trailing stop / profit-lock
  ladder / any managed stop that persists). Authoring a Runtime 3.0 runtime.yaml
  here is the ONLY correct way to create a DSL-protected strategy. A raw MCP
  strategy_create_custom_strategy / create_position call CANNOT carry a DSL and
  MUST NEVER be used to stand up a named, DSL-protected, or persistent strategy
  (it yields an unnamed, unsupervised, registry-less position bundle with at most
  a flat stop — the confirmed Decoupling failure). The raw MCP tools are ONLY for
  manual one-off open/close positions or mirror (copy-trade) strategies that have
  NO DSL. If DSL / a trailing stop / "protect this" is anywhere in the intent →
  author it here. FIRST, before building from scratch, offer the fast path: surface
  the closest matching TEMPLATE (via senpi-strategy-discover) as the recommended
  on-ramp for new users — start from it, fork-and-customize it, or design your own;
  the user's choice is final, scratch is a peer not a downsell. If they choose scratch
  (or already gave a specific custom thesis), DEFAULT behavior: ask the 7 design
  decisions one question at a time, reflect each answer back, then assemble + smoke-test the package. Also
  edits existing strategies. NOT for installing (senpi-strategy-ops) or picking
  one to run (senpi-strategy-discover).
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.4.0"
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

## Start here — offer the fast path before building from scratch

Building from scratch is powerful, but it's the **slow** path (the full interview + compile + smoke-test).
Most users — especially new ones — are best served by starting from a **proven template** and tweaking it
if they want. So **before the interview, offer three ways to go** — as peers, with the fast one recommended,
never as a gate:

1. **Start from a matching template** — *the fastest way to get running.* If the user gave any thesis hint,
   surface the closest one: `python3 senpi-strategy-discover/scripts/discover.py --theme "<their words>"
   --no-market` and read `meta.theme_matches`. Name the top fit (*"**Cougar** — equity long/short — is
   close to what you described"*). The template path is **senpi-strategy-discover**'s job (it picks +
   deploys via ops) — hand it off; don't rebuild the catalog here.
2. **Start from that template and make it your own** — deploy the template, then **edit it** (this skill's
   edit path — see "Editing an existing strategy") to change the universe / thresholds / sizing / DSL. The
   bridge for *"close, but I want changes."*
3. **Design your own from scratch** — first-class, fully supported; you run the interview below.

**Example opening (new-ish user, thesis hinted):**
> *"Two ways to go: I can get you running fast from a proven template — **Cougar** is close to what you
> described, and you can tweak it to make it yours — or if you'd rather, we design one from scratch
> together. Your call — what sounds better?"*

**Tone — encourage without discouraging (this is the whole point):**
- Template-first is *"the fastest way to get running,"* **never** *"the right way."* Scratch is a **peer**,
  not a downsell.
- **The user's choice is final.** If they pick scratch — or already gave a specific custom thesis — go
  straight into the interview. **Never re-pitch templates, never nag.**
- **Calibrate to the signal.** Brand-new / vague ask → lean into template-first + the fork path. Clear
  custom thesis → surface the closest match **once** (*"heads-up, Cougar is close to this"*), then respect
  their call and build.
- If discover surfaces **no** close fit, say so and go straight to scratch — never force a bad-fit template.

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

## ▶ DEFAULT behavior — the rules of this conversation (do this every time)

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
   (`interval_seconds`). **Never hand-roll stops — copy a preset from `references/dsl-presets.yaml`.**

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
the catalog entry, then unit-test → validate → hand to smoke-test."* Then tick through it, reporting each:

1. **Confirm the spec.** Replay name + thesis + all 7 + opening constraints → get a "yes." *("You said
   rotate the cohort every 3 days — that's in.")* Nothing is written before this yes.
2. **Scaffold.** Match the idea to an archetype row in `references/creating-a-strategy.md`, create the
   package dirs, and state the archetype + file plan. → *"Matched the cohort-rotation archetype; scaffolding
   `strategies/<id>/…`."* This lets the user catch a wrong archetype/universe **before** you write code.
3. **`scoring.py`** (pure math). Write it → one line on what it scores. → *"scoring.py in — ranks the cohort
   by 3-day relative strength."*
4. **`<instance>/scanners/scan.py`** (read-only, emits `marginPct` intent). Write it → one line on what it
   emits.
5. **`runtime.yaml`** — the plain-language **`description`** of the thesis + how it works (the runtime
   registers it and senpi-portfolio reads it back as the mandate) plus inputs, entry action, DSL preset,
   risk gates. Write it → one line on the thesis + DSL + risk posture.
6. **`strategy.yaml`** — catalog facets from the glossary. Write it → *"catalog entry in."*
7. **Unit-test `scoring.py`** on sample candles (pure — no mocks). Run it → report pass/fail as its own beat.
8. **Validate** → `python3 senpi-strategy-author/scripts/validate_strategy.py strategies/<id>` (0 errors),
   then the **universe gate** → `python3 senpi-strategy-ops/scripts/validate_universe.py strategies/<id>`
   — every hardcoded ticker must be a live HL instrument (`deploy.py create` also runs this as a preflight
   and refuses to fund a bad universe; derived-universe strategies pass trivially). Run each, report the
   result. **If validation fails, narrate the fix and re-run — don't go silent while you debug.**
9. **Smoke-test (hand to `senpi-strategy-ops`):** dry-run → run `scan()` once on live read-only MCP →
   tiny deploy → confirm the runtime **accepted** a signal (`openclaw senpi state -r <id>-<inst>
   --json`), not just that it ticked. **Green = `scan` → signal → runtime-accepted, end to end.**

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
- **Funding the new wallet** ($100/instance floor) comes from the embedded wallet at deploy. If the
  embedded wallet is short on USDC because funds are in other strategies, **offer options** — deposit
  more, or `strategy_withdraw_funds` from an existing strategy (it keeps running) and fund the new
  one. Present these; never frame it as "shut down X first."

The wallet creation + funding happens in the deploy step (`senpi-strategy-ops` `deploy.py create`
makes one new wallet per instance). Authoring just designs the package; **concurrency is automatic.**

## Invariants (every guess in this system fails silently — hold these)

- **`scan(inputs, ctx)` is read-only, pure, single-pass.** Return `[]` on any error. No daemon, no
  `push_signal`, no `sleep`, no file writes, no wallet hardcoding.
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

## Handoff

Authoring produces the package only. **Deploy/monitor/close is `senpi-strategy-ops`** — hand off the
`id` once the smoke test is green. Attribution (`skillName`/`skillVersion`) is set by ops from
`strategy.yaml` `id`/`version`.
