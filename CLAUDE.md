# Senpi Skills — Repo Conventions for AI Editors

This repo is almost entirely AI-generated. The rules below exist because they have been forgotten or stripped during past rewrites. Preserve them on every edit.

---

## ▶▶ Architecture — strategies are PACKAGES, not skills

A trading strategy is a **deployable package, not an agent skill**. All packages live under
`strategies/`:

```
strategies/<id>/                   # a strategy package (e.g. strategies/spider/)
  strategy.yaml                    # THIN deploy manifest & single source of truth for
                                   #   id, version, catalog facets, instances[] — tunables do
                                   #   NOT live here (no `params`; that design is retired)
  <instance>/                      # one dir per instance (`main`, or e.g. `swing`/`scalp`)
    runtime.yaml                   # the self-contained runtime spec for this instance —
                                   #   scanners+inputs (the tunables), actions, exit (DSL), risk
    scanners/
      scan.py                      # exports scan(inputs, ctx) -> list[dict]; read-only, pure,
                                   #   single-pass; the runtime spawns + supervises it
      scoring.py                   # optional pure math (no I/O) so the edge unit-tests
```

- A strategy package has **no `SKILL.md`** and no attribution file. `strategy.yaml` is the single
  source of truth for `id`/`version`.
- **Skills** are the agent's lifecycle capability: **`senpi-strategy-discover`** (find/recommend),
  **`senpi-strategy-author`** (build/edit), **`senpi-strategy-ops`** (install/monitor/close).
  **`senpi-trading-runtime`** is the infra-contract skill — how `@senpi-ai/runtime` behaves
  (`scan(inputs, ctx)`, runtime.yaml schema, DSL engine, `openclaw senpi …` CLI). The remaining
  skills (portfolio, improve-trades, market-pulse, smart-money, trader-research, account-status,
  deposit-withdraw-transfer, why) are analysis/guidance utilities.
- **Install/teardown is `senpi-strategy-ops`**, always: `deploy.py create <id> --budget <usd>`
  (one named wallet per instance via `strategy_create_custom_strategy`, budget split by
  `funding_share`, min $100/wallet, resumable) → `deploy.py runtime <id>` (renders each
  runtime.yaml onto its wallet, `openclaw senpi runtime create`) → optional `verify`. Teardown is
  `close.py <id>` (or `--all`) — **never a raw `strategy_close`** (it strands the runtime).
  Attribution is automatic: `deploy.py` passes **`skillName`/`skillVersion` from `strategy.yaml`
  `id`/`version`** on every wallet-creation call.
- **`strategies/catalog.json` is GENERATED** from `strategies/*/strategy.yaml` via
  `senpi-trading-runtime/scripts/gen_catalog.py` — never hand-edit it. It is written to **two
  places**: repo `strategies/catalog.json` (source of truth) AND
  `senpi-strategy-discover/catalog.json` (bundled so the catalog travels with the discover skill
  when installed standalone). Keep both in sync by re-running `gen_catalog.py`.
- **Validate** a package with `senpi-strategy-author/scripts/validate_strategy.py <dir>` and
  `senpi-strategy-ops/scripts/validate_universe.py <dir>` (every hardcoded ticker must be a live
  HL instrument — a dead name silently no-trades; `deploy.py create` runs this as a preflight and
  refuses to fund a bad universe).

---

## ▶ User wants help with a "strategy"? Classify the intent FIRST

The word "strategy" is overloaded in Senpi and the wrong path is the most common silent failure:

- **Operational** — "Buy me HYPE 10x", "Open a short on BTC", "Copy this trader". The user named
  the position or trader. Execute via MCP `strategy_create_custom_strategy` (multi-asset
  positions) or `strategy_create` (copy-trader). No recommendation, no package.
- **Strategic** — "Help me pick a strategy", "What should I trade?", "Recommend a strategy" →
  **`senpi-strategy-discover`** (the analyst-style picker over `catalog.json`; deploy handoff to
  ops). "Build a strategy", "I have a trading idea", or anything needing a DSL exit →
  **`senpi-strategy-author`** (template-first, then interview). A NAMED strategy to
  install/monitor/close ("deploy spider") → **`senpi-strategy-ops`**.
- **Ambiguous** — ask the user before acting. A single disambiguation question costs nothing.

**Never default to `strategy_create_custom_strategy` for an ambiguous "what should I trade?"**
That tool is for specific positions the user named. And **never stand up a named / DSL-protected /
persistent strategy via raw MCP calls** — a raw call can't carry a DSL and never registers with
the runtime (the confirmed Decoupling failure). If protection is anywhere in the intent, the path
is author → ops.

---

## ▶ Writing or editing a trading strategy? Start here

Do **not** improvise a strategy from scratch. **`senpi-strategy-author/SKILL.md`** owns the flow
(template-first offer → the 7-decision interview → staged build), and its
**`references/creating-a-strategy.md`** is the self-contained build guide — archetype table with a
**Clone from** column of real packages under `strategies/`, the `scan(inputs, ctx)` skeleton, a
complete `runtime.yaml`, DSL presets, and the gotchas.

Deep references (edge cases; the guide links them):

- **`senpi-trading-runtime/references/runtime-yaml.md`** — the authoritative `runtime.yaml`
  schema. If any other doc disagrees, **the runtime wins**; ultimate source of truth is the
  runtime repo itself (`~/workspace/senpi/senpi-trading-runtime`).
- **`senpi-trading-runtime/references/scan-contract.md`** — `scan(inputs, ctx)` in depth: the
  `ctx` surface, the signal shape, `signal_data_schema`.
- **`senpi-trading-runtime/references/runtime-cli.md`** + **`runtime-concepts.md`** +
  **`dsl-protection-check.md`** — the `openclaw senpi …` CLI, the engine mental model, and the
  DSL-coverage verdict procedure.
- **`senpi-strategy-author/references/dsl-presets.yaml`** — copy a preset, change ≤1 field.

Hard-won invariants (each failed silently in production once):

- **Per-signal sizing is `marginPct` (percent of withdrawable, (0,100]) + `leverage` — there is
  no `marginUsd`.** An unknown top-level signal key is dropped with only a stderr warn and sizing
  falls back to config.
- **Never hardcode a ticker you didn't verify** against `market_list_instruments`
  (`validate_universe.py`). `xyz:XYZ100`, not `xyz:NASDAQ`; quoted-in-the-leaderboard-feed ≠
  tradeable.
- **Copy identifiers from their source, never from memory** — a plausible field name, enum, or
  unit compiles fine, ticks clean, and trades nothing.
- **Coin symbols on money-moving tools: plain for main-dex crypto, dex-prefixed for HIP-3
  assets — never mix the two up.** On `strategy_create_custom_strategy`, `create_position`,
  `edit_position`, `close_position`, `strategy_close_positions`, `ratchet_stop_*`, and the
  estimate tools, main-dex crypto stays bare (`BTC`, not `xyz:BTC`) and HIP-3 builder-dex assets
  carry their dex prefix — today that's the XYZ dex (equities, metals, indices, energy):
  `xyz:BRENTOIL`, not `BRENTOIL`. When unsure which side a symbol falls on, check
  `market_list_instruments`. Unknown coins are rejected as `INVALID_ARGUMENT` — never retry
  them. In `discovery_*` output, `coinDisplayName` (`"NVDA"`) is display-only — **always copy
  the `coin` field into tool calls**, it holds the tradeable form.

---

## Skill Attribution

Strategies are attributed via the MCP wallet-creation call's **`skillName`/`skillVersion`**,
valued from the package's `strategy.yaml` `id`/`version`. `senpi-strategy-ops/scripts/deploy.py`
does this automatically — never deploy a package another way, and never strip those params from
example `strategy_create*` calls in docs. Utility/guide skills (portfolio, discover, author, ops,
etc.) do not create strategy wallets themselves and carry no attribution files.

**Why this matters:** strategies created in the wild are tracked back to the originating package
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
(`senpi-strategy-author/scripts/validate_strategy.py` also flags any bare `@senpi/runtime` in a
package.)

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
