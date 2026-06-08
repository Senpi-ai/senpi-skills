---
name: senpi-strategy-author
description: >-
  Build or edit a Senpi trading strategy PACKAGE (scanner.py + runtime.yaml(s)
  + strategy.yaml). Use when the user wants to create a new autonomous strategy
  from scratch, clone/adapt an existing one, or tune an existing strategy's
  scanner logic, runtime config, DSL exits, risk gates, or params. NOT for
  installing/running a strategy (that's senpi-strategy-ops) or picking one to
  install (that's senpi-strategy-discover).
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Strategy Author — build & edit strategy packages

A **strategy is a package**, not a skill:

```
<id>/
  scanner.py        # signal producer — emits signals only, never executes/exits, never hardcodes a wallet
  runtime.yaml      # the deterministic runtime spec (one per instance): scanners, actions, DSL, risk
  strategy.yaml     # the deploy declaration (single source of truth): id, version, catalog, instances[], params
```

The `scanner.py` is authored against the `senpi_runtime_helpers` SDK (shipped in the
`senpi-trading-runtime` infra skill): `SenpiClient`, `producer_daemon`, `push_signal`, and
**`load_params()`** — the scanner reads every tunable from `strategy.yaml` `params`, and reads its
wallet address from the env the installer injects. Never hand-roll MCP/daemon/loops; never read a
`config/*.json` (that pattern is retired — `strategy.yaml params` is the only tunable source).

## Build a new strategy (fast path)

1. **Pick an archetype** → `references/producer-patterns.md` (clone the named example package).
2. **Read the build guide** → `references/strategy-creation.md` (the self-contained flow).
3. **Write the package**: `scanner.py` (from the SDK skeleton) + `runtime.yaml` (per instance) +
   `strategy.yaml`. Schemas:
   - `strategy.yaml` → `references/strategy-yaml-schema.md`
   - `runtime.yaml` → `references/yaml-schema.md`
   - DSL exits → `references/dsl-configuration.md` + `references/dsl-presets.yaml`
   - risk gates → `references/risk-gates.md`
   - signal wire format → `references/signal-schema.md`
   - worked examples → `references/strategy-examples.md`, `references/momentum-guarded-strategy.md`
4. **Validate** → `python3 senpi-strategy-author/scripts/validate_strategy.py <package-dir>` (0 errors).
5. **Install** is a separate step owned by **senpi-strategy-ops** (`install_strategy`).

## Edit / improve an existing strategy

Editing draws on the same references as authoring. Common edits:
- **Tune thresholds / asset sets / leverage** → change `strategy.yaml` `params` (the scanner reads
  them via `load_params()`; no code change). Re-validate.
- **Change exits** → `runtime.yaml` `dsl_preset` (see `references/dsl-configuration.md`).
- **Change risk caps** → `runtime.yaml` `risk.guard_rails` (see `references/risk-gates.md`).
- **Change signal logic** → `scanner.py` (the algorithm; keep it signal-only).

After any edit, run the validator, then re-run `install_strategy` (idempotent) via senpi-strategy-ops.

## Invariants (the validator enforces these)

- A strategy is a package; it is **not** a skill and carries no `SKILL.md` / attribution file.
- `strategy.yaml` `id` == package directory name; `version` is the single source for catalog +
  attribution (`strategy_id` / `strategy_version`).
- Each instance's `scanner.name` matches an `external_scanner` in its `runtime.yaml`; its `wallet_env`
  appears as `${…}` in that `runtime.yaml`.
- `params` is the only tunable source — the scanner reads it via `load_params()`; no second copy.
- The runtime package is **`@senpi-ai/runtime`** (with `-ai`) — never `@senpi/runtime`.
