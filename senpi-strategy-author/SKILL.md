---
name: senpi-strategy-author
description: >-
  Build or edit a Senpi trading strategy PACKAGE (Runtime 3.0): a scan.py + sibling
  scoring.py + one runtime.yaml per instance + strategy.yaml. Use when the user wants
  to create a new autonomous strategy from scratch, clone/adapt an existing one, or tune
  an existing strategy's scanner logic, runtime config, DSL exits, risk gates, or inputs.
  NOT for installing/running a strategy (that's senpi-strategy-ops) or picking one to
  install (that's senpi-strategy-discover).
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Strategy Author — build & edit strategy packages (Runtime 3.0)

A **strategy is a package**, not a skill. It runs on **Runtime 3.0**: the runtime spawns and supervises
a **`scan(inputs, ctx)`** function every `interval_seconds` and owns everything downstream — sizing,
execution, the two-phase DSL exit, risk guard-rails, slots, and cross-tick state. There is **no producer
daemon, no `push_signal`, no `senpi_runtime_helpers` SDK, no `load_params()`, no `strategy.yaml params`
block** — those are the retired v2 producer model. **Do not use them.**

## Package layout

```
<id>/
  strategy.yaml                 # deploy manifest: id, version, catalog, requires, instances[]
  <instance>/                   # one dir per instance (main, hedge, long, short, swing, scalp, …)
    runtime.yaml                # the runtime spec: external_scanner (inputs + signal_data_schema),
                                #   entry action, exit (DSL preset), risk guard_rails
    scanners/
      scan.py                   # exports scan(inputs, ctx) -> list[dict]; read-only MCP; single-pass
      scoring.py                # pure thesis math (no I/O/MCP); imported as `import scoring` — NO __init__.py
```

## The `scan()` contract — read this FIRST, don't author from memory

The **authoritative contract** is
[`senpi-trading-runtime/references/scan-contract.md`](../senpi-trading-runtime/references/scan-contract.md):
`scan(inputs, ctx)`, the frozen `ctx` surface, the return dict, and the read-only MCP boundary. Copy the
skeleton from there. In short:

- **`scan(inputs, ctx) -> list[dict]`** — single-pass, synchronous, **read-only**. No `while True`, no
  `sleep`, no daemon. It returns candidate signals; the runtime sizes + executes + exits. On any failure,
  **return `[]`** (a raised exception rolls the whole tick back).
- **Tunables live in the runtime's `inputs:` block**, read via `inputs.get("minScore", 4)`. There is no
  `params` block and no `load_params()`.
- **Data via `ctx.senpi_mcp.call_tool(name, args)`** — read-only tools only (`market_*`, `leaderboard_*`,
  `discovery_*`, `strategy_get*`, …); every mutation tool raises `PermissionError`. Guard each call in
  try/except and degrade to `[]`, never crash.
- **Pure math in a sibling `scoring.py`** (`import scoring`; **NO `__init__.py`** — the sibling resolves
  via the scanner dir on `sys.path`). `scan.py` does reads + state; `scoring.py` does numbers.
- **Cross-tick memory via `ctx.state`** (`last()` → mutate → `append(dict)`), bounded by
  `state_history_max_count`.
- **Sizing:** emit **`marginPct`** (PERCENT of withdrawable, top-level — the fleet standard, ~97 of 102
  scanners) and the runtime sizes `(marginPct/100) × withdrawable`. `marginUsd` (top-level fixed USD)
  also works; the fleet uses `marginPct`.

## Build a new strategy (fast path)

1. **Pick an archetype + clone a gold template.** The [archetype → gold-template table in
   `references/strategy-creation.md`](references/strategy-creation.md#step-1--pick-the-archetype--gold-template-to-clone-then-map-it-to-a-dsl-preset)
   maps each archetype → an example package under `strategies/<id>/`. Clone its
   `<instance>/scanners/scan.py` + `scoring.py` + `runtime.yaml`, then adapt — never hand-roll from scratch.
2. **Read the build guide** → `references/strategy-creation.md` (the self-contained 3.0 flow).
3. **Write the package**: `<instance>/scanners/scan.py` (from the scan-contract skeleton) + sibling
   `scoring.py` + `<instance>/runtime.yaml` + `strategy.yaml`. Schemas:
   - `scan()` contract → `senpi-trading-runtime/references/scan-contract.md`
   - `runtime.yaml` fields + units → `senpi-trading-runtime/references/runtime-yaml.md` (**the runtime's
     own schema — it outranks every helper doc; if they disagree, the runtime wins**)
   - `strategy.yaml` → `references/strategy-yaml-schema.md`
   - DSL exits → `references/dsl-configuration.md` + `references/dsl-presets.yaml`
   - risk gates → `references/risk-gates.md`
   - catalog facets → `senpi-strategy-discover/references/glossary.yaml`
   - worked examples → `references/strategy-examples.md`
4. **Unit-test `scoring.py`** on sample candles (it's pure — no mocks).
5. **Validate** → `python3 senpi-strategy-author/scripts/validate_strategy.py strategies/<id>` (0 errors).
6. **Install** is a separate step owned by **senpi-strategy-ops** (`deploy.py`).

## Edit an existing strategy

- **Tune thresholds / universe / leverage** → the instance's `runtime.yaml` `inputs:` block (the scan
  reads via `inputs.get()`; no code change). Re-validate.
- **Change exits** → `runtime.yaml` `exit:` `dsl_preset` (see `references/dsl-configuration.md`).
- **Change risk caps** → `runtime.yaml` `risk.guard_rails` (see `references/risk-gates.md`).
- **Change signal logic** → `scanners/scan.py` + `scoring.py` (keep the scan read-only + signal-only).

After any edit, run the validator, then re-deploy via senpi-strategy-ops.

## Invariants (the validator enforces these — see `scripts/validate_strategy.py`)

- A strategy is a **package**, not a skill; it carries no `SKILL.md` / attribution file.
- `strategy.yaml` `id` == package directory name; `version` is the single source for catalog +
  attribution; `requires.runtime` is `>=3.0.0`.
- Each instance's `runtime.yaml` `external_scanner` points at a `scanners/scan.py` that **defines
  `scan(inputs, ctx)`**, with a **sibling `scoring.py`** and **NO `__init__.py`** in the scanners dir.
- Every `data{}` key the scan emits is declared in that instance's `signal_data_schema`.
- `data_retention_seconds` ∈ [3600, 604800]; `cooldown_seconds` ≥ 60; `per_asset_cooldown_seconds` ≥ 300;
  every instance ships a DSL `exit:` block (protection is not optional).
- The runtime package is **`@senpi-ai/runtime`** (with `-ai`) — never `@senpi/runtime`.

## ⛔ Never guess syntax — source beats memory (your recall is NOT authoritative)

Every ticker / `runtime.yaml` field / unit / MCP tool+arg / enum you emit from memory is a **silent
no-trade** — it compiles, ticks clean, and trades nothing. Copy each from its source, never recall it:

| What you're writing | Copy from |
|---|---|
| Asset tickers | `market_list_instruments` (live) → verify with `senpi-strategy-ops/scripts/validate_universe.py` |
| `runtime.yaml` fields & units | `senpi-trading-runtime/references/runtime-yaml.md` (the runtime wins over any helper doc) |
| The `scan()` return + `ctx` surface | `senpi-trading-runtime/references/scan-contract.md` |
| DSL exit fields | `references/dsl-presets.yaml` (copy a preset, change ≤1 field) |
| Catalog facets & enums | `senpi-strategy-discover/references/glossary.yaml` |

When source and memory conflict, **source wins**. When you can't find the source, **stop and ask** —
never paper over the gap with a plausible value.
