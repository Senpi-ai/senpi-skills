---
name: senpi-strategy-author
description: >-
  Build a Senpi trading strategy from scratch — interactively, ONE decision at a
  time. Use when the user wants to create, design, or build a new autonomous
  strategy: "build a strategy", "help me build a trading strategy", "create a
  strategy from scratch", "walk me through building a strategy", "design a
  strategy", "I have a trading idea". DEFAULT behavior: ask the 7 design
  decisions one question at a time, reflect each answer back, then assemble +
  smoke-test the package. Also edits existing strategies. NOT for installing
  (senpi-strategy-ops) or picking one to run (senpi-strategy-discover).
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.0.0"
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
5. **Then assemble → unit-test the math → smoke-test.** Only after the user confirms.

Deep mechanics, code skeletons, and a full worked example live in
[`references/creating-a-strategy.md`](references/creating-a-strategy.md) — read it, but **drive the
conversation from the script below**, don't read the guide *to* the user.

## The 7 decisions — your question script (ask in order, ONE at a time)

For each: ask the question, offer the options as plain choices, then map the answer to the package.

1. **Universe — "What should it watch and trade?"**
   A) one asset · B) a fixed basket you name · C) dynamic (scan everything, filter by volume) ·
   D) derived (trade what the best traders / a cohort hold). → sets how `scan()` builds its list.
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

## After the 7 — confirm, assemble, smoke-test

1. **Replay the full spec** (name + thesis + all 7 + opening constraints) → get a "yes." *("You said
   rotate the cohort every 3 days — that's in.")*
2. **Assemble the package** — match the idea to an archetype row in `references/creating-a-strategy.md`,
   then write: `scoring.py` (pure math) · `<instance>/scanners/scan.py` (read-only, emits `marginPct`
   intent) · `runtime.yaml` (inputs, entry action, DSL preset, risk gates) · `strategy.yaml` (catalog
   facets from the glossary).
3. **Unit-test `scoring.py`** on sample candles (it's pure — no mocks needed).
4. **Validate** → `python3 senpi-strategy-author/scripts/validate_strategy.py strategies/<id>` (0 errors).
5. **Smoke-test (hand to `senpi-strategy-ops`):** dry-run → run `scan()` once on live read-only MCP →
   tiny deploy → confirm the runtime **accepted** a signal (`openclaw senpi state -r <id>-<inst>
   --json`), not just that it ticked. **Green = `scan` → signal → runtime-accepted, end to end.**

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

## Editing an existing strategy

Same references; usually no rebuild: tune `runtime.yaml` `inputs` (universe/thresholds/sizing), swap
the `dsl_preset`, adjust `risk.guard_rails`, or change the `scoring.py` math. Re-validate, then
re-smoke-test if you touched `scan.py`/`runtime.yaml`.

## Handoff

Authoring produces the package only. **Deploy/monitor/close is `senpi-strategy-ops`** — hand off the
`id` once the smoke test is green. Attribution (`skillName`/`skillVersion`) is set by ops from
`strategy.yaml` `id`/`version`.
