---
name: senpi-trading-runtime
description: >-
  How a Senpi trading strategy interacts with the runtime engine (@senpi-ai/runtime)
  on Hyperliquid: a strategy runs from a runtime.yaml pointing at a Python module that
  exports scan(inputs, ctx), which the runtime supervises and calls each interval, then
  owns execution, risk guard_rails, and two-phase DSL trailing-stop exits. Use when
  working with runtime.yaml, the scan(inputs, ctx) contract, external_scanner, ctx, or
  the DSL exit engine — including verifying open positions are protected by DSL (have a
  working stop-loss). The shared runtime contract the lifecycle skills reference. NOT
  for building, installing, or picking a strategy (→ senpi-strategy-author /
  senpi-strategy-ops / senpi-strategy-discover).
license: Apache-2.0
metadata:
  author: Senpi
  version: "3.2.3"
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
  `marginPct`, `leverage`, `data{}`). The runtime validates each `data{}` against the runtime.yaml's
  `signal_data_schema`, then sizes, executes, and manages exits.

Keep the thesis logic in a sibling pure **`scoring.py`** (no I/O, no MCP) so it is unit-testable;
`scan.py` does the reads + state, `scoring.py` does the math.

## Runtime commands (essentials)

The plugin registers a `senpi` command group on the gateway. Deploying a strategy and checking it:

```bash
openclaw plugins install @senpi-ai/runtime
openclaw senpi deploy -p <package-dir> --budget <usd>  # ONE verb, detached: funds preflight → wallet create+fund → install → one observed tick
openclaw senpi deploy status                            # poll until terminal; the verified report (read-only)
openclaw senpi runtime list                             # id, source, status ("running — NO ENTRY SCANNERS" = scanners never wired)
```

**Deploy a package through `senpi-strategy-ops`' `deploy.py create <id> --budget <usd>`**, which
resolves the package, runs the structural preflight and drives that same verb. The verb owns the
gates — the live-universe check (`[E_UNIVERSE_NOT_LIVE]`, pre-money), the funds preflight, the
`skillName`/`skillVersion` attribution and the verified tick. **`runtime create` is internal**: it
installs a runtime and skips every one of those, so it is not the deploy path.

Beyond `deploy`/`deploy status` and `runtime list/delete`, the CLI exposes the runtime's live state — `senpi dsl
positions|inspect|closes` (the exit engine), `senpi action list|inspect|history|decisions` (the
decision layer), `senpi risk` (am I allowed to trade, and why not), `senpi audit` (backend trade
trail with AI reasoning), `senpi scanner` (per-scanner health, liveness, and a `(no signals yet)` flag for scanners that run but produce nothing),
`senpi events`/`senpi explain <asset>` (the local domain-event log — the trade narrative, and one
asset's stitched lifecycle), `senpi status`/`senpi state` (health — fail-closed: an external scanner
never proven by a tick reads `unknown`, not `healthy`; non-healthy scanners get their own line with
restart count and cause), and `senpi guide …` (in-shell reference). Full surface with every option →
`references/runtime-cli.md`.

**To confirm open positions are actually stop-loss protected** (a position with no DSL shows up as an
*absence* in `dsl positions`, so it's easy to miss) → the verdict procedure in
`references/dsl-protection-check.md`.

## The reference set

| Read this | For |
|---|---|
| `references/runtime-concepts.md` | How the runtime behaves end to end: the runtime pipeline, `position_tracker`, and the two-phase DSL exit engine |
| `references/runtime-yaml.md` | The `runtime.yaml` schema — every section, the `external_scanner` fields, the risk guard-rails |
| `references/scan-contract.md` | The author contract in depth: `scan(inputs, ctx)`, the `ctx` surface, the signal shape, and `scoring.py` |
| `references/runtime-cli.md` | The full `openclaw senpi …` command surface — runtime, dsl, action, status/state, skills, guide |
| `references/dsl-protection-check.md` | **Verify open positions are DSL-protected** — the PROTECTED / UNPROTECTED / STOP-NOT-ON-VENUE verdict + the open-vs-tracked reconciliation |

## Package naming (load-bearing)

The runtime package is **`@senpi-ai/runtime`** (with `-ai`) — the one users install on their hosts.
Always write it with the `-ai`.
