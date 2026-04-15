---
name: senpi-trading-runtime
description: >-
  Configure, deploy, and manage Senpi Trading Runtime (OpenClaw plugin @senpi-ai/runtime) for automated on-chain position tracking with DSL trailing stop-loss protection. Use when a user needs to create or modify runtime YAML files, configure DSL (Dynamic Stop-Loss) exit engine parameters (phases, tiers, time-based cuts), set up the position_tracker scanner to monitor a wallet's positions on Hyperliquid, install/list/delete runtimes via CLI, or inspect DSL-tracked positions. The runtime does NOT create strategy wallets; create/get the strategy wallet via Senpi MCP first, then link that existing wallet in runtime YAML. Triggers on mentions of senpi, Senpi runtime, DSL exit, stop-loss tiers, position tracker, trailing stop, openclaw senpi, dsl_preset, or strategy YAML configuration."
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Trading Runtime — OpenClaw Plugin

On-chain position tracker with automated DSL (Dynamic Stop-Loss) exit engine. Monitors a wallet's positions on Hyperliquid for lifecycle events (open, close, edit, flip) and applies two-phase trailing stop-loss protection to all positions. It links to an existing strategy wallet address and does not create wallets.

## Core Concepts

**Flow:** Position Tracker scanner → detects position changes → DSL exit engine manages trailing stops

1. **Position Tracker** (`position_tracker` scanner) polls the wallet on-chain, detecting opens, closes, increases, decreases, and flips.
2. **DSL exit engine** monitors tracked positions on a timer, computing trailing stop-loss floors across two phases.
3. **Strategy (in runtime)** = link to an existing wallet address. The wallet address is the strategy identifier everywhere, but wallet creation happens via Senpi MCP (not the runtime).

**Key insight:** The position tracker enables DSL protection for ALL positions of a strategy address — including those opened manually on the exchange or by other tools.

### Wallet lifecycle (required order)

1. Create strategy wallet address via Senpi MCP calls (or confirm it already exists).
2. Put that wallet address into runtime YAML (`strategies.<name>.wallet` / `${WALLET_ADDRESS}`).
3. Install/create runtime. This links the runtime to the existing wallet for monitoring and exits.

Never treat `openclaw senpi runtime create` as wallet creation.

### Strategy wallet validation (mandatory before runtime create/install)

Use this exact MCP-first guardrail to avoid using embedded wallets by mistake:

1. Call `strategy_list` and collect `strategies[].strategyWalletAddress`.
2. A runtime wallet is valid only if it appears in that list (case-insensitive match).
3. If list is empty OR provided wallet is not in list: call `strategy_create_custom_strategy` to create a strategy wallet first.
4. Use the newly created strategy wallet address directly as `WALLET_ADDRESS` in runtime YAML, then install runtime.

Hard rules:
- Never use embedded wallet/injected wallet for runtime linking.
- Never treat a wallet as a strategy wallet unless it exists in `strategy_list`.
- Runtime install is blocked until strategy wallet validation passes.
- Always confirm with the user before creating a new strategy wallet address, and explicitly confirm the budget (`initialBudget`) that will be used.

Example MCP flow (tool names from Senpi MCP):

```text
strategy_list({})
if provided_wallet not in strategies[].strategyWalletAddress:
  # confirm with user first: create new strategy wallet + initialBudget
  strategy_create_custom_strategy({
    initialBudget: <budget_usdc>,
    positions: [],
    skillName: <strategy_or_runtime_name>,
    skillVersion: "1.0.0"
  })
```

Notes:
- `initialBudget` is required when creating the strategy wallet.
- Use `positions: []` when you only need the strategy wallet created now and will trade later.

## CLI Commands

All commands require the OpenClaw gateway running (`openclaw gateway run`).

Use `openclaw senpi --cheatsheet` to print the full plugin command cheatsheet to stdout.

### Runtime management

Prerequisite: strategy wallet address already exists (created/fetched via Senpi MCP).

```bash
# Create a runtime from YAML file
openclaw senpi runtime create --path ./my-strategy.yaml
openclaw senpi runtime create -p ./my-strategy.yaml          # short form

# Create with inline YAML content
openclaw senpi runtime create --content "<yaml>"
openclaw senpi runtime create -c "<yaml>"                    # short form

# Create with custom ID
openclaw senpi runtime create --path ./my-strategy.yaml --runtime-id my-name

# List installed runtimes (id, wallet, source, status)
openclaw senpi runtime list

# Delete a runtime
openclaw senpi runtime delete <runtime_id>                   # positional id
openclaw senpi runtime delete --id <runtime_id>              # named flag
```

### DSL position inspection

```bash
# All active DSL-tracked positions
openclaw senpi dsl positions
openclaw senpi dsl positions -r <id>                         # filter by runtime id
openclaw senpi dsl positions -a <addr>                       # filter by wallet address
openclaw senpi dsl positions --json

# Inspect one position (full DslState)
openclaw senpi dsl inspect <ASSET>
openclaw senpi dsl inspect SOL -r <id>
openclaw senpi dsl inspect SOL -a <addr>
openclaw senpi dsl inspect BTC --json

# Archived (closed) positions — reason, ROE, phase, tier
openclaw senpi dsl closes
openclaw senpi dsl closes -r <id>
openclaw senpi dsl closes -a <addr>
openclaw senpi dsl closes -l <n>
openclaw senpi dsl closes --json
```

### Runtime diagnostics

```bash
openclaw senpi status                    # lightweight health summary (all runtimes)
openclaw senpi status -r <id>
openclaw senpi status --json

openclaw senpi state                     # full operational state (all runtimes)
openclaw senpi state -r <id>
openclaw senpi state --json
```

### In-shell reference (`senpi guide`)

```bash
openclaw senpi guide                 # overview + quick command list
openclaw senpi guide scanners        # scanner types and config fields
openclaw senpi guide actions         # action types and decision modes
openclaw senpi guide dsl             # DSL exit engine: phases, tiers, time cuts
openclaw senpi guide examples        # print minimal strategy YAML to stdout
openclaw senpi guide schema          # full YAML schema field reference
openclaw senpi guide version         # plugin version and changelog URL
```

### CLI preferences (`senpi config`)

Persists to `~/.openclaw/senpi-cli.json`. Restart gateway to apply.

```bash
openclaw senpi config set-chat-id <chatId>
openclaw senpi config set-senpi-jwt-token <token>
openclaw senpi config set-state-dir <dir>
openclaw senpi config get <key>      # telegram-chat-id | senpi-jwt-token | state-dir
openclaw senpi config list           # secrets masked
openclaw senpi config unset <key>
openclaw senpi config reset
```

### Gateway RPC (advanced)

**Common calls:**

```bash
openclaw gateway call senpi.installRuntime --params '{"runtimeYamlContent":"..."}'
openclaw gateway call senpi.listRuntimes --json --params '{}'
openclaw gateway call senpi.deleteRuntime --params '{"id":"my-runtime"}'
openclaw gateway call senpi.listDslPositions --json --params '{}'
openclaw gateway call senpi.getDslPositionState --json --params '{"asset":"SOL"}'
openclaw gateway call senpi.listDslArchives --json --params '{"limit":20}'
openclaw gateway call senpi.getHealthStatus --json --params '{}'
openclaw gateway call senpi.getSystemState --json --params '{}'
```

**Methods summary**

| Method | Params | Success response |
|--------|--------|------------------|
| `senpi.installRuntime` | `runtimeYamlContent` or `runtimeYamlPath` | Runtime installed |
| `senpi.listRuntimes` | — | List of runtimes |
| `senpi.deleteRuntime` | `id` or address | Deleted |
| `senpi.listDslPositions` | `runtimeId?` | Active positions |
| `senpi.getDslPositionState` | `asset`, `runtimeId?` | Single position state |
| `senpi.listDslArchives` | `runtimeId?`, `address?`, `limit?` | `closes[]` (asset, direction, entryPrice, lastPrice, currentROE, closeReason, closedAt, phase, …) |
| `senpi.getHealthStatus` | `runtimeId?` (string) | `status` or `statuses[]` (health, components.scanners, components.dsl) |
| `senpi.getSystemState` | `runtimeId?` (string) | `state` or `states[]` (health, stateDir, scanner/DSL components) |

Close reasons for `listDslArchives`: `dsl_breach`, `hard_timeout`, `weak_peak_cut`, `dead_weight_cut`, `exchange_sl_hit`, `manual`.

## Runtime YAML

The runtime YAML drives all behavior. Top-level keys: `name`, `strategies`, `scanners`, `actions`, `exit`, `notifications`.

```yaml
name: my-tracker
version: 1.0.0
description: >
  On-chain position tracker with DSL trailing stop-loss.

strategies:
  main:
    wallet: "${WALLET_ADDRESS}"
    budget: 500
    slots: 2
    margin_per_slot: 200
    trading_risk: conservative    # conservative | moderate | aggressive
    enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

exit:
  engine: dsl
  interval_seconds: 30            # how often the price monitor runs (5-3600)
  dsl_preset:                     # single preset (no named map needed)
    hard_timeout:
      enabled: true
      interval_in_minutes: 360
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 120
      min_value: 5
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 60
    phase1:
      enabled: true
      max_loss_pct: 4.0
      retrace_threshold: 7
      consecutive_breaches_required: 1
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 7,  lock_hw_pct: 40 }
        - { trigger_pct: 12, lock_hw_pct: 55 }
        - { trigger_pct: 15, lock_hw_pct: 75 }
        - { trigger_pct: 20, lock_hw_pct: 85 }

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

Environment variable substitution: `${VAR}` and `${VAR:-default}` resolved at load time.

For full field details: [YAML Schema Reference](references/yaml-schema.md)

## DSL Exit Engine — Key Concepts

**Two-phase trailing stop-loss** protecting open positions:

**Phase 1** (from entry until the first tier is reached):
- **Absolute floor** from `max_loss_pct` (cap on loss from entry, scaled by leverage)
- **Trailing floor** from `retrace_threshold` (ROE % pullback from high-water mark)
- Effective floor = stricter of both (LONG: higher price; SHORT: lower)
- Optional **time-based cuts** at preset level: `hard_timeout`, `weak_peak_cut`, `dead_weight_cut`
- Exchange SL stays at the absolute floor only; tighter exits enforced by runtime

**Phase 2** (after the first tier is reached — Phase 2 always starts at tier 0):
- **Tier floor** = `lock_hw_pct` % of current high-water ROE (trails as HW advances, never loosens)
- Exchange SL tracks the full effective floor and updates as it moves

**Tiers** are profit milestones (by ROE % from entry). Each tier has two fields: `trigger_pct` (ROE % that activates it) and `lock_hw_pct` (% of high-water ROE to lock as floor). Tiers must have strictly increasing `trigger_pct`.

**Consecutive breaches** are tick-based (not time-based). Each monitor tick (every `interval_seconds`), if price violates the floor, the breach counter increments. Recovery resets it.

**Retrace convention:** `retrace_threshold` is ROE %. Engine converts to price: `retrace_ROE% / 100 / leverage`. At 10x leverage, 7% ROE retrace = 0.7% price below high-water.

For full DSL configuration with all fields, time-based cuts, close reasons, and events: [DSL Configuration Reference](references/dsl-configuration.md)

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `WALLET_ADDRESS` | Yes | Existing strategy wallet address from Senpi MCP (used in YAML via `${WALLET_ADDRESS}`). |
| `SENPI_API_KEY` | For live MCP | Senpi MCP authentication. |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID for notifications. |
| `DSL_STATE_DIR` | No | Override DSL state file directory. |

## References

- [Runtime Concepts](references/runtime-concepts.md) — Conceptual explanation of scanners, actions, DSL phases, and what every field controls in trading terms
- [YAML Schema Reference](references/yaml-schema.md) — All runtime YAML fields and DSL preset options
- [DSL Configuration Reference](references/dsl-configuration.md) — Full DSL exit engine: phases, tiers, time cuts, close reasons, events
- [Strategy Examples](references/strategy-examples.md) — Ready-to-use YAML examples with different DSL tuning profiles
- [Migration from DSL Cron](references/migration-from-dsl-cron.md) — Upgrade from old `dsl-v5.py` cron to plugin runtime
- [Runtime Template](references/runtime-template.yaml) — Starter runtime.yaml with field mapping comments
