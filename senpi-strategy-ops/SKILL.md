---
name: senpi-strategy-ops
description: >-
  Install, monitor, and uninstall a Senpi trading strategy PACKAGE. Use when the
  user wants to deploy/install a strategy (create or use a wallet, create runtime,
  launch the scanner daemon), check whether a running strategy is healthy/live,
  troubleshoot a deployed strategy, or tear one down. NOT for building/editing a
  strategy (senpi-strategy-author) or choosing one (senpi-strategy-discover).
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Strategy Ops — install, monitor, uninstall

A strategy is a package (`scanner.py` + `runtime.yaml`(s) + `strategy.yaml`). Ops owns its deployed
lifecycle: **get the wallet(s) → deploy → verify.**

## Install — two steps: get the wallet(s), then deploy

`senpi-helpers install` deploys onto wallet addresses you **already have**; it does NOT create or fund
wallets.

**Step 1 — get the strategy wallet address for each instance** (split the budget by `funding_share`).
Two ways:
- **Create a new strategy wallet** via MCP (confirm with the user first; **min $100 per wallet**):
  ```
  strategy_create_custom_strategy(initialBudget=<≥100>, positions=[], skillName=<id>, skillVersion=<version>)
    → poll strategy_list by strategyId until status ACTIVE → read strategyWalletAddress
  ```
  (Creation runs CREATE_WALLET → FUND_WALLET → ACTIVE incl. bridging + a $1 fee — wait for ACTIVE.)
- **Use an existing strategy wallet** the user already holds (with their consent) — find it via
  `strategy_list`, or the user provides the address.

**Step 2 — deploy with the ready address(es):**
```
senpi-helpers install <package-dir> --wallet <name>=0x… [--wallet <name>=0x… …] \
                      --decision-model <bare-model> [--telegram-chat-id <id>] [--reinstall] [--dry-run]
```
- Single-instance: `--wallet 0x…`. Multi-instance (spider): `--wallet swing=0x… --wallet scalp=0x…`.
- **`--decision-model` is required** (a BARE model name, no provider prefix) unless the host already
  sets the strategy's `<MODEL_ENV>`.

The CLI runs the deterministic per-instance deploy (render → `runtime create` → launch the scanner
daemon with the declared env e.g. `SPIDER_LEG=swing` → registration check) and prints a structured
report. **Report from that, not from raw logs**:

```jsonc
{ "strategy": "spider", "version": "5.1.1", "status": "registered",   // registered ≠ live yet — run Monitor
  "attribution": { "skillName": "spider", "skillVersion": "5.1.1" },
  "instances": [ { "name":"swing","wallet":"0x…","runtime_id":"…","daemon":"…","status":"registered" },
                 { "name":"scalp","wallet":"0x…","runtime_id":"…","daemon":"…","status":"registered" } ] }
```
(`registered`/`already_installed` = deployed + registered; `degraded`/`failed`/`wallet_required`/
`decision_model_required` = a problem. **`registered` is not "ticking"** — confirm via Monitor.)

Preview first with `--dry-run`. The install report proves the runtime/scanner registered; before
declaring the strategy truly live, run the Monitor workflow below and confirm a recent successful
scanner tick. **Idempotency** is checked from **live runtime state** (not a ledger):
if a runtime already exists for a wallet and you didn't pass `--reinstall`, that instance reports
`already_installed` and is skipped. What each step does under the hood (+ the runtime-engine CLI) is in
[`references/deploy-and-teardown.md`](references/deploy-and-teardown.md); the install ledger is an
ephemeral install-time scratchpad only — see [`references/install-ledger.md`](references/install-ledger.md).

## Monitor — is it actually live?

Do **not** trust "runtime: running" alone. Verify each instance's scanner is ticking:
- field-level liveness decision tree → `references/liveness-verification.md`
- daemon CLI (`list`/`health`/`stats`) → `references/senpi-helpers-cli.md`
- runtime-engine CLI (`runtime create/list/delete/status`, `senpi state`) → `references/deploy-and-teardown.md`
- scanner lifecycle / env / restart → `references/external-producers.md`
- state-file schemas → `references/senpi-helpers-schema-history.md`
- the engine mental model (position_tracker → DSL → actions), for troubleshooting →
  `senpi-trading-runtime/references/runtime-concepts.md`

A strategy is **live** only when its runtime is running AND each instance's `external_scanner` has a
recent successful tick (`runCount > 0`, `lastRunFinishedAt` within `tick_seconds` + buffer).

## Uninstall / teardown

```
senpi-helpers uninstall <package-dir> [--instance <name>]
```

Ledger-free — derives teardown from the **package + live state**. Per instance, in order:
1. **Stop the scanner daemon** — found among running daemons (`senpi-helpers list`) by the instance's
   `scanner.name` → stop. No new entries.
2. **Verify flat / intended** — confirm open positions are handled per the user's intent (close now, or
   let DSL/runtime wind down). Never silently abandon open risk.
3. **Delete the runtime** — `runtime_id` = the instance's `runtime.yaml` top-level `name:`.
4. Repeat for every instance; report per-instance teardown status.

Full commands + ordering: [`references/deploy-and-teardown.md`](references/deploy-and-teardown.md)
(Teardown). **`--reinstall`** on install is the safe "redeploy in place": stop the old daemon → delete
+ recreate the runtime → relaunch the daemon (**same wallet**).

## Invariant

The agent's wallet-creation MCP call carries attribution `skillName`/`skillVersion` = the package
`id`/`version` from `strategy.yaml`.
The runtime package is **`@senpi-ai/runtime`** — never `@senpi/runtime`.
