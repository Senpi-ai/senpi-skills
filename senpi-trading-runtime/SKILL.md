---
name: senpi-trading-runtime
description: "Configure, deploy, and manage runtimes in the Senpi Trading Runtime OpenClaw plugin for automated on-chain position tracking with DSL trailing stop-loss protection. Use when a user needs to create or modify runtime YAML files, configure DSL (Dynamic Stop-Loss) exit engine parameters (phases, tiers, time-based cuts), set up the position_tracker scanner to monitor a wallet's positions on Hyperliquid, install/list/delete runtimes via CLI, inspect DSL-tracked positions, or check runtime health and system state. Triggers on mentions of senpi, trading runtime, DSL exit, stop-loss tiers, position tracker, trailing stop, openclaw senpi, dsl_preset, strategy YAML configuration, runtime status, runtime health, system state, or scanner health."
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Trading Runtime — OpenClaw Plugin

On-chain position tracker with automated DSL (Dynamic Stop-Loss) exit engine. Monitors a wallet's positions on Hyperliquid for lifecycle events (open, close, edit, flip) and applies two-phase trailing stop-loss protection to all positions.

## Core Concepts

**Flow:** Position Tracker scanner → detects position changes → DSL exit engine manages trailing stops

1. **Position Tracker** (`position_tracker` scanner) polls the wallet on-chain, detecting opens, closes, increases, decreases, and flips.
2. **DSL exit engine** monitors tracked positions on a timer, computing trailing stop-loss floors across two phases.
3. **Strategy** = a wallet address. The wallet address is the strategy identifier everywhere.

**Key insight:** The position tracker enables DSL protection for ALL positions of a strategy address — including those opened manually on the exchange or by other tools.

**Wallet uniqueness:** Only one running runtime per wallet address is allowed. Installing a second runtime for the same wallet is rejected. Delete the existing runtime first, then install the new one.

## Strategy Wallet Validation (MCP-first)

The runtime does **not** create wallets — it links to an existing strategy wallet that you create separately via Senpi MCP. Installing a runtime is a linking operation, never a wallet-creation operation.

### Wallet lifecycle (required order)

1. Create the strategy wallet via Senpi MCP (or confirm it already exists).
2. Put that wallet address into the runtime YAML (`strategy.wallet` / `${WALLET_ADDRESS}`).
3. Install/create the runtime — this links the runtime to the existing wallet for monitoring and exits.

Never treat `openclaw senpi runtime create` as wallet creation.

### Validation guardrail (mandatory before runtime create/install)

Use this exact MCP-first flow to avoid linking to an embedded wallet by mistake:

1. Call `strategy_list` and collect `strategies[].strategyWalletAddress`.
2. A runtime wallet is valid only if it appears in that list (case-insensitive match).
3. If the list is empty OR the provided wallet is not in the list: call `strategy_create_custom_strategy` to create a strategy wallet first.
4. Use the newly created strategy wallet address directly as `WALLET_ADDRESS` in the runtime YAML, then install the runtime.

**Hard rules:**
- Never use an embedded wallet / injected wallet for runtime linking.
- Never treat a wallet as a strategy wallet unless it appears in `strategy_list`.
- Runtime install is blocked until strategy wallet validation passes.
- Always confirm with the user before creating a new strategy wallet, and explicitly confirm the budget (`initialBudget`) that will be used.

**Example MCP flow:**

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

All commands require the OpenClaw gateway running (`openclaw gateway run`). Print a full commands cheatsheet at any time with `openclaw senpi --cheatsheet`.

### Runtime management

```bash
# Create a runtime from YAML file
openclaw senpi runtime create --path ./my-strategy.yaml

# Create with inline YAML content
openclaw senpi runtime create --content "<yaml>"

# Create with custom ID
openclaw senpi runtime create --path ./my-strategy.yaml --runtime-id my-name

# List installed runtimes (id, wallet, source, status)
openclaw senpi runtime list

# Delete a runtime by id or wallet address
openclaw senpi runtime delete --id <runtime_id>
openclaw senpi runtime delete --address <wallet>
openclaw senpi runtime delete <runtime_id>   # positional also works
```

### DSL position inspection

```bash
# All active DSL-tracked positions
openclaw senpi dsl positions
openclaw senpi dsl positions --runtime <id>
openclaw senpi dsl positions --address <0x...>
openclaw senpi dsl positions --json

# Inspect one position (full DslState)
openclaw senpi dsl inspect <ASSET>
openclaw senpi dsl inspect SOL --runtime <id>
openclaw senpi dsl inspect BTC --json

# Archived (closed) positions
openclaw senpi dsl closes
openclaw senpi dsl closes --limit 20 --json
```

### Health and system state

Use these commands to check whether the runtime is operating correctly and to diagnose issues.

**`status`** — lightweight health check. Use as a first step when something seems wrong (e.g., positions not being tracked, stop-losses not firing, scanners not running). Shows overall health, scanner summary, and DSL summary per runtime.

```bash
openclaw senpi status                  # Health for all running runtimes
openclaw senpi status --runtime <id>   # Health for a specific runtime
openclaw senpi status --json           # Raw JSON output
```

**`state`** — full runtime snapshot. Use when `status` shows a problem and you need to dig deeper — it includes scanner registration details, DSL monitor telemetry (tick counts, errors, timing), active positions, and state directory location.

```bash
openclaw senpi state                   # Full state for all running runtimes
openclaw senpi state --runtime <id>    # Full state for a specific runtime
openclaw senpi state --json            # Raw JSON output
```

**When to use which:**
- Start with `status` — if everything shows healthy, the runtime is operating normally.
- Move to `state` when `status` reports degraded/unhealthy and you need to understand why (e.g., which scanner is erroring, whether the DSL monitor is stuck, tick error counts).
- Use `dsl positions` / `dsl inspect` for position-level detail (trailing stop floors, current tier, breach counts) — those are about individual positions, not runtime health.

### In-shell reference

```bash
openclaw senpi guide              # Overview and quick command list
openclaw senpi guide scanners     # Scanner types and config fields
openclaw senpi guide actions      # Action types and decision modes
openclaw senpi guide dsl          # DSL exit engine reference
openclaw senpi guide examples     # Print minimal strategy YAML
openclaw senpi guide schema       # Full YAML schema
openclaw senpi guide version      # Plugin version and changelog URL
```

### External scanner ingest

Use this flow when a scanner's data comes from another system and should be
pushed into the runtime instead of polled on an interval. Declare the scanner
in YAML, then push payloads via the CLI.

```yaml
scanners:
  - name: custom_regime
    type: external_scanner
    outputs:
      signals: false
      context: true
    retention: last_only
    config:
      fields:
        regime: { type: string, required: true }
        confidence: { type: number, required: true }
```

```bash
openclaw senpi external-scanner ingest \
  --address 0xYourStrategyWallet \
  --scanner custom_regime \
  --payload '{"data":{"regime":"RISK_ON","confidence":0.91}}'
```

The CLI accepts `--payload <json>` inline or `--payload-path <file>` for a JSON file. Payloads may be a single-signal shape (`asset`, `direction`, `score`, `signal_type`, `data`) or a batch (`{"signals":[...]}`). For context-only scanners, just send a `data` blob.

Use namespaced prompt/context keys only:

- `{{signal_custom_regime}}` for ingested signal arrays
- `{{context_custom_regime}}` for retained external context
- flat aliases like `{{custom_regime}}` are not supported

For producer operations (scheduling with `openclaw cron`, shipped producers, custom producer guidance), see [External Producers](references/external-producers.md). For a complete strategy wired end-to-end with the shipped momentum producer, see [Momentum-Guarded Strategy](references/momentum-guarded-strategy.md).

### Diagnostic action commands

Use these when a trade didn't fire as expected, to audit decision-engine runs and execution history.

```bash
openclaw senpi action list                          # All registered actions with counters
openclaw senpi action inspect <action-name>         # Persisted latest state for one action
openclaw senpi action history [action-name]         # Rolling execution history
openclaw senpi action decisions [action-name]       # Rows where decision engine ran (LLM reasoning)
```

All accept `--runtime <id>`, `--address <wallet>`, `--limit <n>`, and `--json`.

### Configuration

```bash
openclaw senpi config set-chat-id <chatId>           # Telegram notifications
openclaw senpi config set-senpi-jwt-token <token>     # Senpi MCP auth
openclaw senpi config set-state-dir <dir>             # State directory
openclaw senpi config get <key>
openclaw senpi config list
openclaw senpi config unset <key>
openclaw senpi config reset
```

## Runtime YAML

The runtime YAML drives all behavior. Top-level keys: `name`, `strategy`, `scanners`, `actions`, `exit`, `risk`, `notifications`.

```yaml
name: my-tracker
version: 1.0.0
description: >
  On-chain position tracker with DSL trailing stop-loss.

strategy:
  wallet: "${WALLET_ADDRESS}"
  slots: 2
  margin_pct: 15
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
  order_type: FEE_OPTIMIZED_LIMIT # MARKET (default) or FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true       # fall back to market if maker doesn't fill
    execution_timeout_seconds: 15         # seconds to wait for maker fill (1-300, default 45)
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

- **Phase 1** (entry → first tier): limits downside. `max_loss_pct` sets a hard loss cap; `retrace_threshold` trails the high-water mark. Optional time-based cuts (`hard_timeout`, `weak_peak_cut`, `dead_weight_cut`) close stagnant positions.
- **Phase 2** (after first tier reached): locks in profits. Each tier's `lock_hw_pct` sets a floor as a % of the high-water ROE — the floor trails upward and never loosens.

**Tiers** are profit milestones. `trigger_pct` = ROE % that activates the tier, `lock_hw_pct` = % of high-water ROE to lock as floor. Tiers must have strictly increasing `trigger_pct`.

**Tuning guidance:**
- Higher `max_loss_pct` = more room before hard stop (conservative: 5-6%, aggressive: 2-3%)
- Higher `retrace_threshold` = more tolerance for pullbacks from peak
- Higher `consecutive_breaches_required` = more tolerant of temporary dips (1 = instant, 3 = sustained)
- Longer time-cut intervals = more patience before cutting underperformers

For full DSL mechanics (retrace math, breach logic, close reasons, events): [DSL Configuration Reference](references/dsl-configuration.md)

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `WALLET_ADDRESS` | Yes | Strategy wallet address (used in YAML via `${WALLET_ADDRESS}`). |
| `SENPI_API_KEY` | For live MCP | Senpi MCP authentication. |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID for notifications. |
| `DSL_STATE_DIR` | No | Override DSL state file directory. |

## References

- [Runtime Concepts](references/runtime-concepts.md) — Conceptual explanation of scanners, actions, DSL phases, and what every field controls in trading terms
- [Strategy YAML Reference](references/yaml-schema.md) — Schema reference: top-level sections, building blocks, template variables, wiring rules, validation errors
- [DSL Configuration Reference](references/dsl-configuration.md) — DSL exit engine: phases, tiers, time cuts, tuning guidance, close reasons, events
- [Strategy Examples](references/strategy-examples.md) — Position-tracker runtimes with different DSL tuning profiles
- [Momentum-Guarded Strategy](references/momentum-guarded-strategy.md) — End-to-end quick-start strategy exercising external scanners, LLM decisions, risk gates, and DSL exits — with producer cron setup
- [External Producers](references/external-producers.md) — How to schedule, deploy, and build external-scanner producers
