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

## Runtime npm package — `@senpi-ai/runtime` (NOT `@senpi/runtime`)

**Always** reference the Senpi runtime plugin as **`@senpi-ai/runtime`** in any doc, script, config example, or error message. The name without `-ai` (`@senpi/runtime`) is a separate dev/internal package — its `latest` is a pre-release dev build and no stable `1.x.x` version is published. Operators copying `npm install @senpi/runtime@1.1.0` from your docs will hit a 404.

| | name | what it is | `latest` |
|---|---|---|---|
| ✅ use this | `@senpi-ai/runtime` | production, 27 stable versions | `1.1.0` |
| ❌ never | `@senpi/runtime` | dev / internal, all `-dev.*` | `1.1.0-dev.runtime-phase-2.*` |

Source-of-truth: `senpi-trading-runtime/package.json` declares `name: "@senpi-ai/runtime"`. Check that field if in doubt.

The wrong name is easy to type by reflex (every other Senpi npm scope is `@senpi-ai/...`, but muscle memory often shortens it). Grep every diff that touches `@senpi/` — if there's no `-ai`, it's wrong.
