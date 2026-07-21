---
name: senpi-trading-runtime
description: >-
  Raw-plumbing REFERENCE for the Senpi runtime engine (@senpi-ai/runtime) on
  Hyperliquid — the runtime.yaml schema, the scan(inputs, ctx) contract, the DSL
  exit engine internals, and the raw `openclaw senpi state|scanner|dsl|action|risk`
  command surface. This is NOT a first-stop skill: every strategy-scoped question
  (build, deploy, install, status, protection, review, close) belongs to the
  senpi-strategy-composer front door (the `senpi_strategy` tool / `composer
  status|review`). You reach THIS skill only at rung 2 of the ladder — when
  `composer status` flags trouble it cannot itself explain and you must read the
  raw runtime state or understand a field's underlying semantics. Reference for
  plumbing facts, not operating flows.
license: Apache-2.0
metadata:
  author: Senpi
  version: "4.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Trading Runtime — raw-plumbing reference

This skill is the **plumbing reference** for the Senpi runtime engine
(**`@senpi-ai/runtime`**): the `runtime.yaml` schema, the `scan(inputs, ctx)` contract, the DSL
exit engine internals, and the raw `openclaw senpi …` command surface. It documents *facts about the
plumbing*, not how to operate a strategy.

## Boundary — this is rung 2, not the front door

Every strategy-scoped question — build, discover, deploy, fund, install, update, **status,
protection, review, close**, "what's my strategy doing?", "is it protected?", "why did(n't) it
trade?" — belongs to the **`senpi-strategy-composer`** front door, driven by the model-visible
**`senpi_strategy`** tool (or `openclaw senpi composer …`). The composer owns those operating flows
and renders authoritative, relay-verbatim answers (`composer status` / `composer review`). Do **not**
answer those from this skill.

**The 2-rung ladder (owned and taught by the composer skill):**

1. **Rung 1 — `composer status <target>` (or bare `composer status` for the portfolio view).**
   This is the front door. It renders the lifecycle chain, per-scanner cadence/last-check/quiet-check
   counts, every open position's protection in plain ROE, and the risk gates. Its output is relayed
   verbatim.
2. **Rung 2 — the raw plumbing, i.e. THIS skill.** You drop here **only when `composer status` flags
   trouble it cannot explain** — a degraded scanner it can't diagnose, a protection anomaly, an
   `UNAVAILABLE` live section, or a question about what an underlying field *means*. Then you read the
   raw runtime state (`openclaw senpi state|scanner|dsl positions|dsl inspect --json`) and/or consult
   the references below. Raw outputs are **also relayed verbatim, never re-derived** (a re-derivation
   flipped a PnL sign on real money once).

You do **not** hand-write `scan.py` or `runtime.yaml` for new work, and you do **not** hand-edit an
emitted/installed `runtime.yaml` to change a live strategy (content-addressing rejects the edit and
the installed copy is frozen inline — a known trap). The composer emits and deploys those units; the
schema and contract below are for **reading and diagnosing** what it emitted, not for authoring.

## The runtime model (orientation)

A strategy runs from a **`runtime.yaml`** that points at an in-repo Python module. The runtime
**spawns and supervises** that module and calls a frozen **`scan(inputs, ctx)`** every
`interval_seconds`. The division of labor is fixed:

- **Scanner code produces signals — nothing else.** `scan(inputs, ctx)` *reads* market and account
  data and *returns* a `list[dict]` of candidate signals. It never opens, closes, sizes, schedules,
  or executes anything.
- **The runtime owns everything downstream:** scheduling, spawning + supervising + restarting the
  scanner, validating (`signal_data_schema`) + de-duplicating signals, **sizing & order execution**
  (`FEE_OPTIMIZED_LIMIT`), slot accounting, `risk.guard_rails`, the two-phase **DSL** trailing-stop
  exits, and **crash-safe position reconcile** on restart.

The interaction surface is small and one-directional — the scanner reads via `ctx`, returns signals,
and the runtime acts. Field-level detail lives in the references.

## The reference set

| Read this | For |
|---|---|
| `references/runtime-concepts.md` | How the runtime behaves end to end: the runtime pipeline, `position_tracker`, and the two-phase DSL exit engine |
| `references/runtime-yaml.md` | The `runtime.yaml` schema — every section, the `external_scanner` fields, the risk guard-rails (for reading/diagnosing an emitted unit) |
| `references/scan-contract.md` | The runtime's frozen `scan(inputs, ctx)` contract: the `ctx` surface, the read-only MCP boundary, the signal shape |
| `references/runtime-cli.md` | The full raw `openclaw senpi …` command surface — the rung-2 plumbing behind `composer status` |
| `references/dsl-protection-check.md` | Rung-2 protection triage: reading the raw `dsl positions/inspect/closes` fields + the open-vs-tracked reconciliation, once `composer status` has flagged a protection anomaly |

## Package naming (load-bearing)

The runtime package is **`@senpi-ai/runtime`** (with `-ai`) — the one users install on their hosts.
Always write it with the `-ai`.
