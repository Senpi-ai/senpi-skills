# Senpi Skills — Repo Conventions for AI Editors

This repo is almost entirely AI-generated. The rules below exist because they have been forgotten or stripped during past rewrites. Preserve them on every edit.

---

## Skill Attribution (REQUIRED for strategy-representing skills only)

**Scope:** This rule applies only to skills that *are* a trading strategy — i.e. adopting the skill results in a new strategy wallet being created and run under its thesis. This covers every animal-named / strategy-named skill in this repo (bald-eagle, cobra, mamba, owl, tiger-strategy, wolf-strategy, whale-index, etc.).

**Out of scope** (do NOT add attribution files to these): onboarding/guide skills (senpi-onboard, senpi-entrypoint, senpi-getting-started-guide), runtime and infrastructure skills (senpi-trading-runtime, dsl-dynamic-stop-loss), utility skills (fee-optimizer, opportunity-scanner, emerging-movers), and analysis/feedback-loop skills (wolf-howl).

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

### Before you write `@senpi/runtime` (no `-ai`), ask the user

If you (the AI) are about to commit `@senpi/runtime` anywhere — producer, doc, `runtime.yaml`, comment, anywhere — **stop and ask first**. Use plain language:

> "I'm about to write `@senpi/runtime` (no `-ai`). Two packages exist:
>
> - **`@senpi-ai/runtime`** — the package your end users install. Default for anything shipping to `main`. If users will read or run this file, this is the answer.
> - **`@senpi/runtime`** — internal package, used to test runtime-plugin changes on Railway boxes ourselves. Only correct if this work is on a feature branch validating a runtime build, NOT for anything users install.
>
> Which one do you want?"

When unsure, default to **`@senpi-ai/runtime`**. A working user install beats a clever test reference.
