---
name: senpi-trading-runtime
description: >-
  The runtime-knowledge bundle for the Senpi trading platform on Hyperliquid —
  everything about how a strategy interacts with the Senpi trading runtime engine
  (@senpi-ai/runtime). A strategy runs from a runtime.yaml that points at a Python
  module exporting scan(inputs, ctx); the runtime spawns and supervises that module,
  calls scan() every interval_seconds, and owns everything downstream — signal
  validation, dedup, sizing/execution (FEE_OPTIMIZED_LIMIT), slot accounting,
  risk guard_rails, two-phase DSL trailing-stop exits, and crash-safe position
  reconcile. scan() reads through a read-only ctx.senpi_mcp, carries cross-tick
  state in ctx.state, and returns a list of signal dicts. This skill is the
  shared runtime contract the lifecycle skills reference — it explains how the
  runtime behaves and how your code talks to it. It is NOT where you build,
  install, or pick a strategy: build/edit → senpi-strategy-author;
  install/monitor/uninstall → senpi-strategy-ops; find/recommend →
  senpi-strategy-discover. Triggers on: runtime.yaml, scan(inputs, ctx),
  external_scanner, ctx.senpi_mcp, ctx.state, signal_data_schema, trading
  runtime, position_tracker, DSL exit engine, runtime-concepts, @senpi-ai/runtime.
license: Apache-2.0
metadata:
  author: Senpi
  version: "4.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Trading Runtime — the runtime contract

This skill is **infrastructure**: the canonical knowledge of how the Senpi runtime
(**`@senpi-ai/runtime`**) behaves and how a strategy interacts with it. The lifecycle skills —
author (build), ops (install/monitor), discover (recommend) — reference this one for the contract.

## The runtime model

A strategy runs from a **`runtime.yaml`** that points at an in-repo Python module. The runtime **spawns and
supervises** that module and calls a frozen **`scan(inputs, ctx)`** every `interval_seconds`. The
division of labor is fixed:

- **Your code produces signals — nothing else.** `scan(inputs, ctx)` *reads* market and account data
  and *returns* a `list[dict]` of candidate signals. It does not open, close, size, schedule, or
  execute anything.
- **The runtime owns everything downstream:** scheduling (`interval_seconds`), spawning +
  supervising + restarting the scanner, validating (`signal_data_schema`) + de-duplicating the
  signals you return, **sizing & order execution** (`FEE_OPTIMIZED_LIMIT`), slot accounting,
  `risk.guard_rails`, the two-phase **DSL** trailing-stop exits, and **crash-safe position reconcile**
  on restart.

## How your code talks to the runtime

The interaction surface is small and one-directional — you read, you return signals, the runtime acts.

- **`runtime.yaml`** declares the scanner(s), the action gate, the exit engine, and the risk
  guard-rails, and passes author tunables down via `inputs:`. → `references/runtime-yaml.md`
- **`scan(inputs, ctx)`** is the single entry point. `inputs` is the runtime's `inputs:` map; `ctx`
  gives you:
  - **`ctx.senpi_mcp.call_tool(name, args)`** — the Senpi MCP client, **read-only** (market,
    account, leaderboard, discovery, `strategy_get*`, …). It is the only way to fetch data.
  - **`ctx.state`** — transactional, runtime-persisted history (`last()` / `append()` / `len`) for
    dedup, rotation, and first-seen ledgers; advances only on a clean tick.
  - **`ctx.wallet`** — the strategy's wallet address.
  - → `references/scan-contract.md`
- **The return value** is a `list[dict]`, one per candidate signal (`asset`, `direction`,
  `marginUsd`, `leverage`, `data{}`). The runtime validates each `data{}` against the runtime.yaml's
  `signal_data_schema`, then sizes, executes, and manages exits.

Keep the thesis logic in a sibling pure **`scoring.py`** (no I/O, no MCP) so it is unit-testable;
`scan.py` does the reads + state, `scoring.py` does the math.

## Runtime commands (essentials)

The plugin registers a `senpi` command group on the gateway. Deploying a runtime and checking it:

```bash
openclaw plugins install @senpi-ai/runtime
openclaw senpi runtime create -p runtime.yaml  # hot-loads; the runtime supervises the scanner
openclaw senpi runtime list                     # id, source, status (running/stopped)
```

Beyond `runtime create/list/delete`, the CLI exposes the runtime's live state — `senpi dsl
positions|inspect|closes` (the exit engine), `senpi action list|inspect|history|decisions` (the
decision layer), `senpi status`/`senpi state` (health), and `senpi guide …` (in-shell reference).
Full surface with every option → `references/runtime-cli.md`.

## The reference set

| Read this | For |
|---|---|
| `references/runtime-concepts.md` | How the runtime behaves end to end: the runtime pipeline, `position_tracker`, and the two-phase DSL exit engine |
| `references/runtime-yaml.md` | The `runtime.yaml` schema — every section, the `external_scanner` fields, the risk guard-rails |
| `references/scan-contract.md` | The author contract in depth: `scan(inputs, ctx)`, the `ctx` surface, the signal shape, and `scoring.py` |
| `references/runtime-cli.md` | The full `openclaw senpi …` command surface — runtime, dsl, action, status/state, skills, guide |

## Package naming (load-bearing)

The runtime package is **`@senpi-ai/runtime`** (with `-ai`) — the one users install on their hosts.
Always write it with the `-ai`.
