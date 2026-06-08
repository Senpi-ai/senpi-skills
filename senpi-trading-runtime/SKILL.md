---
name: senpi-trading-runtime
description: >-
  Infra bundle for the Senpi trading platform on Hyperliquid: the runtime engine
  contract (@senpi-ai/runtime — external_scanner signals, LLM/rule action gates,
  risk guard_rails, FEE_OPTIMIZED_LIMIT execution, position_tracker, two-phase
  DSL trailing-stop exits), the bundled stdlib-only Python Producer SDK
  (senpi_runtime_helpers — SenpiClient, producer_daemon, scanner_lock,
  tick_cache, parallel, load_params), and the senpi-helpers operator CLI +
  fleet_heartbeat monitor. This skill is the shared runtime/SDK machinery the
  lifecycle skills reference; it is NOT the place to build, install, or pick a
  strategy. Route those: build/edit → senpi-strategy-author; install/monitor/
  uninstall → senpi-strategy-ops; find/recommend → senpi-strategy-discover.
  Triggers on: senpi_runtime_helpers, push_signal, producer_daemon, scanner_lock,
  tick_cache, load_params, senpi-helpers CLI, DSL exit engine, runtime-concepts,
  @senpi-ai/runtime.
license: Apache-2.0
metadata:
  author: Senpi
  version: "3.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Trading Runtime — infra bundle (runtime engine + Producer SDK + ops CLI)

This skill is **infrastructure**, not a strategy and not a lifecycle skill. It ships the host-side
machinery every strategy shares:

- **The runtime engine contract** — `@senpi-ai/runtime` (the OpenClaw plugin). It consumes a
  `runtime.yaml`: `external_scanner` signals → rule/LLM action gate → `OPEN_POSITION` via
  `FEE_OPTIMIZED_LIMIT`, `position_tracker` for on-chain changes, declarative `risk.guard_rails`, and
  the two-phase **DSL** trailing-stop exit engine. Conceptual model: `references/runtime-concepts.md`.
- **The Python Producer SDK** — `senpi_runtime_helpers/` (stdlib-only). The canonical way to author a
  scanner: `SenpiClient` (direct-HTTPS MCP), `producer_daemon` (the tick loop), `scanner_lock`,
  `tick_cache`, `parallel`, `push_signal`, and **`load_params`** (reads a strategy package's
  `strategy.yaml` params). Never hand-roll MCP/daemon/loops.
- **The operator CLI** — `senpi-helpers` (`list`/`health`/`stats`/`stop`/`restart`) and
  `scripts/fleet_heartbeat.py` (fleet liveness digest).

## Package naming (load-bearing)

The production runtime users install is **`@senpi-ai/runtime`** (with `-ai`). `@senpi/runtime` (no
`-ai`) is a separate internal dev-only package and must never appear on a user-facing branch. If you
are about to write `@senpi/runtime`, stop and default to `@senpi-ai/runtime`.

## Where to go

| You want to… | Skill |
|---|---|
| build or edit a strategy package (`scanner.py` + `runtime.yaml` + `strategy.yaml`) | **senpi-strategy-author** |
| install / monitor / uninstall a deployed strategy | **senpi-strategy-ops** |
| find / recommend a strategy to install | **senpi-strategy-discover** |
| onboard / set up Senpi | **senpi-onboard**, **senpi-entrypoint** |

A **strategy is a deployable package, not a skill.** This bundle provides the engine + SDK + CLI those
packages run on.
