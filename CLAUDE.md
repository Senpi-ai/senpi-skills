# Senpi Skills — Repo Conventions for AI Editors

This repo is almost entirely AI-generated. The rules below exist because they have been forgotten
or stripped during past rewrites. Preserve them on every edit.

**BRANCH NOTE (strategy-composer-poc):** this branch is the composer world. The old lifecycle trio
— `senpi-strategy-author`, `senpi-strategy-discover`, `senpi-strategy-ops` — is DEAD here: dropped
from the runtime's skills manifest, removed from boxes, superseded end-to-end by
**`senpi-strategy-composer`**. Their directories remain ONLY for main-merge archaeology; never
route to them, quote them, or "fix" content inside them. `main`'s copy of this file still teaches
the 3-skill world — reconcile at main-merge time.

---

## ▶▶ Architecture — the composer owns the strategy lifecycle

**One skill owns the whole strategy lifecycle:** `senpi-strategy-composer`, driving the
`openclaw senpi composer` CLI (ships inside the runtime plugin):
`author → check → deploy → simulate → fund → install → update → status → close`.

- The strategy record is the **graph**: `<state>/composer/<strategy>/graph.yaml` (canonical
  layout: `graph.yaml · wallet.json · staged/ · releases/<hash8>/`). `metadata.spec` in the graph
  is the sole spec record — there is no persistent answers file. Machine-generated graphs
  regenerate from their own spec (`composer new <strategy> --from-spec`); hand-edited graphs ARE
  the spec permanently.
- Emitted units are **self-contained snapshots** (vendored `strategy_primitives`, content-addressed
  immutable release dirs). Nothing in this repo hand-writes `scan.py`/`runtime.yaml` for new work.
- **`senpi-trading-runtime`** remains the infra-contract skill — how the runtime plugin behaves
  (`scan(inputs, ctx)`, runtime.yaml schema, DSL engine, `openclaw senpi …` CLI). Its
  `references/` are the runtime-contract ground truth; if any other doc disagrees, the runtime
  repo itself wins.
- The remaining skills (portfolio, improve-trades, market-pulse, smart-money, trader-research,
  account-status, deposit-withdraw-transfer, why) are analysis/guidance utilities. Their
  descriptions route all strategy-scoped follow-ups to the composer.

**`strategies/<id>/` packages are the LEGACY CORPUS** (pre-composer deployable packages:
`strategy.yaml` + per-instance `runtime.yaml` + `scanners/scan.py`). They are ground truth for
archetype mapping and provenance — cite them, never extend the deploy path that consumed them
(`senpi-strategy-ops/scripts/deploy.py` is dead). `strategies/catalog.json` is generated history;
do not hand-edit.

---

## ▶ User wants help with a "strategy"? Classify the intent FIRST

- **Operational** — "Buy me HYPE 10x", "Open a short on BTC". The user named the position.
  Execute via MCP (`strategy_create_custom_strategy` for multi-asset positions). No package, no
  composer.
- **Copy trading — TWO valid paths, present both before creating anything:** (a) backend raw
  mirror (`strategy_create` with `traderAddress`) — exact mirror of one trader, no DSL protection,
  no filters; (b) composer `copy_trading` archetype — cohort conviction, filters, hysteresis
  exits, DSL protection. A copy ask with ANY conditions/filters attached is a strategy
  specification → composer.
- **Strategic** — "help me pick / build / design a strategy", "I have a trading idea", "which
  strategy should I run", or anything needing a supervised exit → **`senpi-strategy-composer`**.
  There is no separate picker, author, or ops skill.
- **Ambiguous** — ask the user before acting. A single disambiguation question costs nothing.

**Never stand up a named / DSL-protected / persistent strategy via raw MCP calls** — a raw call
carries no supervised exit and never registers with the runtime (the confirmed silent failure).
If protection is anywhere in the intent, the path is the composer.

---

## ▶ Writing or editing a trading strategy? Start here

Do **not** improvise a strategy from scratch, and do not hand-write scanner code —
**`senpi-strategy-composer/SKILL.md`** owns the flow (catalog grounding → the interview →
`composer new` → `check` GREEN → deploy/fund/install). Bespoke compute is a `pure_fn` inside the
graph, surgical graph edits are sanctioned, wholesale graph rewrites are not.

Hard-won invariants (each failed silently in production once):

- **Per-signal sizing is `marginPct` (percent of withdrawable, (0,100]) + `leverage` — there is
  no `marginUsd`.** An unknown top-level signal key is dropped with only a stderr warn and sizing
  falls back to config.
- **Never hardcode a ticker you didn't verify** against `market_list_instruments`.
  `xyz:XYZ100`, not `xyz:NASDAQ`; quoted-in-the-leaderboard-feed ≠ tradeable.
- **Copy identifiers from their source, never from memory** — a plausible field name, enum, or
  unit compiles fine, ticks clean, and trades nothing.

---

## Skill Attribution

Composer-minted strategies are attributed on the wallet-creation call as
**`skillName="senpi-strategy-composer"` + `skillVersion`**, with the strategy's name in
**`strategyName`** (H4, 2026-07-20). A strategy is never a skill — the old convention
(`skillName` = the strategy name) mis-taught readers and is retired; strategies already live in
the field with the old shape remain discoverable (`composer close` matches both shapes). Legacy
corpus packages were attributed from `strategy.yaml` `id`/`version` via the dead ops deploy —
historical record only. Never strip attribution params from example `strategy_create*` calls in
docs.

**Why this matters:** strategies created in the wild are tracked back to the originating skill
for performance attribution, fleet analytics, and debugging. A strategy created without these
fields is effectively orphaned — we can see the trades but not which thesis produced them.

---

## Runtime npm package — `@senpi-ai/runtime` (NOT `@senpi/runtime`) on `main`

**Any skill committed to `main` that references the runtime plugin MUST use `@senpi-ai/runtime`.**
That's the production npm package — what end users install on their Railway / OpenClaw hosts.
Source of truth: the runtime repo (`~/workspace/senpi/senpi-trading-runtime/package.json`, name
`@senpi-ai/runtime`).

`@senpi/runtime` (no `-ai`) is a **separately-published internal package for runtime-side dev
testing on Railway boxes**. It only ships `-dev.*` pre-releases — there is no stable release.
Feature branches doing runtime-plugin validation may legitimately pin it; **anything on `main`
must not**. Operators running `npm install @senpi/runtime` from a skill on `main` hit a 404 — the
user's box stays broken until someone tells them which package they actually wanted.

This mistake recurs. If you see `@senpi/` (no `-ai`) in any doc, `runtime.yaml` comment, scanner
docstring, or error-message string on `main`, treat it as a bug and fix it. Grep every diff that
touches `@senpi/` — if there's no `-ai` after `@senpi`, it's the wrong package for `main`.
(NB: on THIS branch the dev-channel `@senpi/runtime@branch-feat-strategy-composer-poc` pin is the
sanctioned exception — it is exactly the runtime-validation case.)

### Before you write `@senpi/runtime` (no `-ai`), ask the user — on every branch

If you (the AI) are about to commit `@senpi/runtime` anywhere on **any** branch — scanner, doc,
`runtime.yaml`, comment, anywhere — **stop and ask first**. The rule applies regardless of which
branch you're on, because the AI can't reliably tell whether the current branch will land on
`main` later. Use plain language:

> "I'm about to write `@senpi/runtime` (no `-ai`). Before I do, please confirm which package you mean.
>
> - **`@senpi-ai/runtime`** — the package your end users install on their Railway / OpenClaw hosts. Use this for anything that may land on `main`.
>   - **If you pick this when you actually wanted the internal one**: nothing breaks. The file just won't reference your in-flight test build.
>
> - **`@senpi/runtime`** — internal package; we use it to test runtime-plugin changes on Railway boxes ourselves. Only correct on a feature branch validating a runtime build, never on anything users install.
>   - **If you pick this when users will install this skill**: their `npm install @senpi/runtime` returns **404**. Their OpenClaw host won't boot. Their trading agent goes offline until someone tells them the right package name.
>
> Default if you're not sure: **`@senpi-ai/runtime`**.
>
> Which one do you want?"

The consequences are asymmetric. The wrong `@senpi-ai/runtime` pick is recoverable on a single
feature branch. The wrong `@senpi/runtime` pick on `main` blocks every user from installing the
skill. When in doubt, default to **`@senpi-ai/runtime`**.
