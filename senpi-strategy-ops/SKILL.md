---
name: senpi-strategy-ops
description: >-
  Install / deploy / run a NAMED Senpi trading strategy (a.k.a. a "predator") —
  and monitor, troubleshoot, or uninstall a deployed one. Use when the user names
  a strategy to run, e.g. "install polar", "install the polar strategy", "install
  polar predator", "install the polar predators strategy", "deploy spider", "set
  up kodiak", "run the <name> strategy", "install a trading strategy", "is my
  strategy live?", "stop/uninstall <name>". The agent obtains a strategy wallet
  (create a new one via MCP strategy_create_custom_strategy, or reuse an existing
  one with consent) and runs `senpi-helpers install <id> --wallet <addr>
  --decision-model <model>`. The strategy <id> (e.g. polar, kodiak, spider) is the
  package folder; match the user's word to a registry/catalog id. NOT for choosing
  WHICH strategy (senpi-strategy-discover) or building/editing one
  (senpi-strategy-author).
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

## Install — resolve, get the wallet(s), then deploy

`senpi-helpers install` deploys onto wallet addresses you **already have**; it does NOT create or fund
wallets.

**Step 0 — resolve which strategy.** The user's word (e.g. "polar", "the polar predator") is a strategy
**`id`**. The `id` IS the package directory name (`polar/` = `id: polar`). Match it to an `id` in the
registry index:
```
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/strategy-v2/catalog.json
```
(or `ls` the installed packages on the host). If the user's word doesn't match any `id`, hand off to
**senpi-strategy-discover** to pick one. You pass that `id` (or its package path) to `senpi-helpers
install`; the package must be present on the host (the registry/repo checkout).

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

### Worked example — "install polar"

```
user: "install the polar strategy"
1. resolve   → id = polar (package polar/)
2. wallet    → ask the user: new wallet or existing? If new (confirm budget ≥ $100):
               strategy_create_custom_strategy(initialBudget=100, positions=[], skillName="polar",
                 skillVersion="5.0.0") → poll strategy_list until ACTIVE → strategyWalletAddress 0xABC…
3. deploy    → senpi-helpers install polar --wallet 0xABC… --decision-model claude-sonnet-4-20250514
4. report    → status "registered" (runtime + scanner registered)
5. confirm   → run Monitor (below) until the scanner has a recent tick, THEN tell the user it's live
```
Multi-instance (spider) is the same but one wallet per instance:
`senpi-helpers install spider --wallet swing=0x… --wallet scalp=0x… --decision-model <model>`.

### Manual-test prerequisites (host)

Before `senpi-helpers install` can run end-to-end:
- `openclaw` + the `@senpi-ai/runtime` plugin installed and running on the host.
- `senpi-helpers` on `PATH` (or call it at `~/.openclaw/skills/senpi-trading-runtime/senpi-helpers`).
- `SENPI_AUTH_TOKEN` exported in the shell that runs install (or the strategy's `auth_token_env`).
- A **funded, ACTIVE** strategy wallet for `--wallet` (created via MCP, or an existing one).
- A bare `--decision-model` name (no provider prefix), e.g. `claude-sonnet-4-20250514`.
- PyYAML available to the host Python; the strategy **package dir on disk** (repo/registry checkout).

Smoke it first with `--dry-run` (no side effects), then run for real, then Monitor.

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
