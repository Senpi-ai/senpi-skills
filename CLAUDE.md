# Senpi Skills — Repo Conventions for AI Editors

This repo is almost entirely AI-generated. The rules below exist because they have been forgotten or stripped during past rewrites. Preserve them on every edit.

---

## ▶▶ v2 architecture (`strategy-v2` branch) — strategies are PACKAGES, not skills

This branch reorganizes the repo around the principle that **a trading strategy is a deployable
package, not an agent skill**:

```
strategies/<id>/                   # a strategy package (e.g. strategies/spider/) — all packages live under strategies/
  scanner.py                       # signal producer — emits signals only, never executes/exits,
                                   #   never hardcodes a wallet; reads tunables via load_params()
  runtime.yaml (or runtime-*.yaml) # the deterministic runtime spec (one per instance)
  strategy.yaml                    # the deploy declaration & SINGLE source of truth:
                                   #   id, version, catalog, instances[], params
```

- A strategy has **no `SKILL.md`** and **no `references/skill-attribution.md`**. `strategy.yaml` is
  the single source of truth; `config/*.json` is retired (tunables live in `params`).
- **Skills** are the agent's lifecycle capability, split into: **`senpi-strategy-discover`** (find/
  recommend), **`senpi-strategy-author`** (build/edit), **`senpi-strategy-ops`** (install/monitor/
  uninstall). `senpi-trading-runtime` is the **infra bundle** (the `@senpi-ai/runtime` engine
  contract + the `senpi_runtime_helpers` SDK incl. `load_params` + the `senpi-helpers` CLI).
  `senpi-entrypoint` / `senpi-onboard` / `senpi-getting-started-guide` remain the front door.
- **Install is two steps** (because strategy-wallet creation is an async, funded MCP lifecycle): the
  **agent** creates the wallet(s) via MCP `strategy_create_custom_strategy(initialBudget≥100,
  positions=[], skillName=<id>, skillVersion=<version>)` — splitting the budget by `funding_share`,
  one wallet per instance, **min $100 each**, polling `strategy_list` by `strategyId` until `ACTIVE`
  and reading `strategyWalletAddress` — then the **CLI** `senpi-helpers install <pkg> --wallet
  <name>=0x..` deploys onto those ready addresses (render runtime → `runtime create` → launch scanner
  daemon → verify). The CLI never creates wallets. Attribution is the MCP tool's **`skillName` /
  `skillVersion`** params, valued from `strategy.yaml` `id` + `version` (the "Skill Attribution"
  section below is superseded for strategies; `strategy.yaml.version` is the one source).
- **`strategies/catalog.json` is GENERATED** from `strategies/*/strategy.yaml` via
  `senpi-trading-runtime/scripts/gen_catalog.py` — never hand-edit it. `gen_catalog.py` writes it to **two
  places**: the repo `strategies/catalog.json` (source of truth) AND `senpi-strategy-discover/catalog.json`
  (bundled with the discover skill, so the catalog travels with it when installed standalone — where
  `../../strategies/` doesn't exist). Keep both in sync by re-running `gen_catalog.py`; never hand-edit either.
- **Validate** a package with `senpi-strategy-author/scripts/validate_strategy.py <dir>`.

The sections below predate v2; where they say "strategy = skill", read it through this model.

---

## ▶ User wants help with a "strategy"? Classify the intent FIRST

The word "strategy" is overloaded in Senpi and the wrong path is the most common silent failure. Two paths, opposite code:

- **Operational** — "Buy me HYPE 10x", "Open a short on BTC", "Copy this trader". The user named the position or trader. **Execute via MCP `strategy_create_custom_strategy`** (multi-asset positions) **or `strategy_create`** (copy-trader). Purely functional — no recommendation, no template, no producer code.
- **Strategic** — "Help me pick a strategy", "What should I trade?", "Build a trading strategy", "Recommend a strategy". The user wants help deciding. **Template-first picker:** recommend 2–3 fits from `catalog.json` + the archetype → gold-template table in `senpi-strategy-author/references/strategy-creation.md`, install via `install_skill`. **Fallback** (only if no template fits): author a new one via `senpi-strategy-author/references/strategy-creation.md`.
- **Ambiguous** — ask the user before acting. A single disambiguation question costs nothing.

**The canonical router with the full picker flow and the what-never-to-do list:** [`senpi-entrypoint/references/strategy-intent-routing.md`](senpi-entrypoint/references/strategy-intent-routing.md). Every surface points there.

**Never default to `strategy_create_custom_strategy` for an ambiguous "what should I trade?"** That tool is for specific positions the user named. Using it as the default for strategic queries is the exact failure mode the router exists to prevent.

---

## ▶ Writing or editing a trading strategy (the build-new-autonomous-agent path)? Start here

This section applies only to the **strategic / build-from-scratch** path above — *not* to operational MCP position-opening.

Do **not** improvise a strategy from scratch. **Read one doc first: [`senpi-trading-runtime/references/strategy-creation.md`](senpi-trading-runtime/references/strategy-creation.md).** It's the self-contained fast path — the 5-step flow, the producer-only-emits-signals invariant, an archetype→example→DSL-preset table, an inline producer skeleton, a complete `runtime.yaml`, the DSL presets, and the gotchas, all in a single fetch. You should not need to browse the repo to produce a working strategy.

The deep references below are for edge cases only — `strategy-creation.md` links to them when needed:

- **`senpi-strategy-author/references/strategy-creation.md`** — its Step 1 holds the full archetype → gold-template table (each row names a real package to clone).
- **`senpi-trading-runtime/references/python-producer-sdk.md`** — full SDK (batch, parallel, cache, errors). Never hand-roll MCP calls or the daemon loop.
- **`senpi-trading-runtime/references/yaml-schema.md`** + **`risk-gates.md`** + **`dsl-configuration.md`** + **`dsl-presets.yaml`** — every `runtime.yaml` field, the risk guard-rails, and the DSL exit.
- **`senpi-trading-runtime/references/senpi-helpers-cli.md`** — verify the daemon is alive + ticking.

Fastest correct path: read `strategy-creation.md` → pick an archetype → clone the named example agent (`kodiak`, `cheetah`, `roach`) → swap in your signal logic → tune thresholds + DSL preset.

---

## Skill Attribution (REQUIRED for strategy-representing skills only)

**Scope:** This rule applies only to skills that *are* a trading strategy — i.e. adopting the skill results in a new strategy wallet being created and run under its thesis. This covers every animal-named / strategy-named skill in this repo (e.g. grizzly, kodiak, polar, cheetah, bald-eagle, owl, scorpion).

**Out of scope** (do NOT add attribution files to these): onboarding/guide skills (senpi-onboard, senpi-entrypoint, senpi-getting-started-guide) and the runtime/infrastructure skill (senpi-trading-runtime — which bundles the DSL exit engine and the Python Producer SDK natively).

If you're creating a new skill and are unsure: if the skill represents a *trading thesis* that gets deployed as its own strategy wallet, it's in scope. If it's a guide, utility, infra, or analysis tool, it's out of scope.

Every call to `strategy_create` or `strategy_create_custom_strategy` made from an in-scope skill **must** include `skill_name` and `skill_version`:

```json
{
  "tool": "strategy_create_custom_strategy",
  "args": {
    "initialBudget": 500,
    "positions": [],
    "skill_name": "<skill-directory-name>",
    "skill_version": "<version-from-SKILL.md-frontmatter>"
  }
}
```

The same applies to `strategy_create`:

```json
{
  "tool": "strategy_create",
  "args": {
    "traderAddress": "0x...",
    "initialBudget": 500,
    "skill_name": "<skill-directory-name>",
    "skill_version": "<version-from-SKILL.md-frontmatter>"
  }
}
```

**Why this matters:** strategies created in the wild are tracked back to the originating skill for performance attribution, fleet analytics, and debugging. A strategy created without these fields is effectively orphaned — we can see the trades but not which thesis produced them.

### Rules for editing skills

When you create, rewrite, or modify any skill in this repo:

1. **Never remove** the `## Skill Attribution` section from `SKILL.md` or the `references/skill-attribution.md` file. If you're rewriting a SKILL.md from scratch, port this section over verbatim.
2. **Every skill directory must contain** `references/skill-attribution.md` with the canonical snippet for that skill (its own `skill_name` and `skill_version`).
3. **Every `SKILL.md` must contain** a `## Skill Attribution` section that points at `references/skill-attribution.md`.
4. **Bump `skill_version` in both places** (SKILL.md frontmatter and `references/skill-attribution.md`) when the skill's strategy-creation behavior changes meaningfully. Keep them in sync.
5. **Any code or docs that show example `strategy_create*` calls** (e.g. SKILL.md quickstarts, recipe files, scripts) must include `skill_name` and `skill_version` in the example.

### Canonical template for `references/skill-attribution.md`

```markdown
# Skill Attribution

When calling `strategy_create` or `strategy_create_custom_strategy`, always include:

```json
"skill_name": "<skill-name>",
"skill_version": "<version>"
```

This is required for attribution and tracking. Example:

```json
{
  "tool": "strategy_create_custom_strategy",
  "args": {
    "initialBudget": 500,
    "positions": [],
    "skill_name": "<skill-name>",
    "skill_version": "<version>"
  }
}
```
```

### Canonical SKILL.md section

```markdown
## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
```

### Creating a new skill

A new skill is not complete until both files above exist with the correct `skill_name` (matching the directory name) and `skill_version` (matching the SKILL.md frontmatter `version` field).

---

## Runtime npm package — `@senpi-ai/runtime` (NOT `@senpi/runtime`) on `main`

**Any skill committed to `main` that references the runtime plugin MUST use `@senpi-ai/runtime`.** That's the production npm package — what end users install on their Railway / OpenClaw hosts. `latest = 1.1.0`. Source-of-truth: `senpi-trading-runtime/package.json` declares `name: "@senpi-ai/runtime"`.

`@senpi/runtime` (no `-ai`) is a **separately-published internal package for runtime-side dev testing on Railway boxes**. It only ships `-dev.*` pre-releases — there is no stable `1.x.x`. Feature branches doing runtime-plugin validation may legitimately pin it; **anything on `main` must not**. Operators running `npm install @senpi/runtime@1.1.0` from a skill on `main` hit a 404 — the user's box stays broken until someone tells them which package they actually wanted.

This mistake recurs. If you see `@senpi/` (no `-ai`) in any doc, `runtime.yaml` comment, producer docstring, or error-message string on `main`, treat it as a bug and fix it. Grep every diff that touches `@senpi/` — if there's no `-ai` after `@senpi`, it's the wrong package for `main`.

### Before you write `@senpi/runtime` (no `-ai`), ask the user — on every branch

If you (the AI) are about to commit `@senpi/runtime` anywhere on **any** branch — producer, doc, `runtime.yaml`, comment, anywhere — **stop and ask first**. The rule applies regardless of which branch you're on, because the AI can't reliably tell whether the current branch will land on `main` later. Use plain language:

> "I'm about to write `@senpi/runtime` (no `-ai`). Before I do, please confirm which package you mean.
>
> - **`@senpi-ai/runtime`** — the package your end users install on their Railway / OpenClaw hosts. Use this for anything that may land on `main`.
>   - **If you pick this when you actually wanted the internal one**: nothing breaks. The file just won't reference your in-flight test build.
>
> - **`@senpi/runtime`** — internal package; we use it to test runtime-plugin changes on Railway boxes ourselves. Only correct on a feature branch validating a runtime build, never on anything users install.
>   - **If you pick this when users will install this skill**: their `npm install @senpi/runtime@1.1.0` returns **404**. Their OpenClaw host won't boot. Their trading agent goes offline until someone tells them the right package name.
>
> Default if you're not sure: **`@senpi-ai/runtime`**.
>
> Which one do you want?"

The consequences are asymmetric. The wrong `@senpi-ai/runtime` pick is recoverable on a single feature branch. The wrong `@senpi/runtime` pick on `main` blocks every user from installing the skill. When in doubt, default to **`@senpi-ai/runtime`**.
