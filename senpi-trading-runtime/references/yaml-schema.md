# Strategy YAML Reference

Schema reference for the runtime YAML file. Defines every top-level section, every field, wiring rules, template variables, and validation errors.

Install a YAML file with `openclaw senpi runtime create --path /path/to/your.yaml`. For a complete end-to-end strategy example, see [momentum-guarded-strategy.md](momentum-guarded-strategy.md). For DSL exit engine details, see [dsl-configuration.md](dsl-configuration.md). For ready-to-use position-tracker variants, see [strategy-examples.md](strategy-examples.md).

---

## Top-level sections

| Key | Required | Purpose |
|-----|----------|---------|
| `name` | yes | Runtime name (human-readable identifier). |
| `version` | no | Runtime version string. |
| `description` | no | Free-form description. |
| `strategy` | yes | Wallet, slots, risk profile, leverage — see [Define Your Strategy](#define-your-strategy). |
| `scanners` | conditional | At least one scanner is required when any action depends on one. See [Connect Your Signal Source](#connect-your-signal-source). |
| `actions` | no | Trade entry and lifecycle actions. See [Define How Trades Are Entered](#define-how-trades-are-entered). |
| `exit` | no | DSL exit engine config. See [Configure Exit Management](#configure-exit-management). |
| `risk` | no | Guard-rail gates. See [Configure Risk Protection](#configure-risk-protection). |
| `notifications` | no | Telegram notifications. See [Set Up Notifications](#set-up-notifications). |
| `health` | no | Health-check configuration (passthrough record). |
| `hooks` | no | Lifecycle hooks (array; passthrough). |
| `advanced` | no | Advanced runtime options (passthrough record). |

---

## Building Blocks

### Define Your Strategy

The `strategy` block tells the runtime which wallet to trade and how much capital to allocate.

| Field | What it does | Required |
|-------|-------------|----------|
| `wallet` | Your wallet address (use `${WALLET_ADDRESS}` to keep it in env) | **yes** |
| `slots` | Max concurrent positions | no |
| `margin_per_slot` | Fixed USD margin per position | no |
| `margin_pct` | Margin as % of account (positive, max 100) | no |
| `trading_risk` | Risk profile: `conservative`, `moderate`, or `aggressive` | no |
| `default_leverage` | Leverage multiplier | no |
| `leverage_multipliers` | Per-risk-level leverage overrides: `{ conservative?, moderate?, aggressive? }` | no |
| `enabled` | Set to `false` to pause without uninstalling | no |

### Set Up Notifications

Get Telegram alerts for position opens, closes, stop-loss updates, and errors.

```yaml
notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

| Field | What it does | Default |
|-------|-------------|---------|
| `telegram_chat_id` | Your Telegram chat ID | — |
| `dsl_lifecycle` | Notify on DSL position open/close | `true` |
| `dsl_notify_sl_updates` | Notify on stop-loss level changes | `false` |
| `action_lifecycle` | Notify on action execution (open/close) | `true` |
| `gateway_url` | Override the OpenClaw gateway URL for delivery | `http://127.0.0.1:18789` |
| `gateway_token` | Override the OpenClaw gateway auth token | `process.env.OPENCLAW_GATEWAY_TOKEN` |

> **Field name caveat:** The `notifications` block currently accepts unknown keys without erroring, so a typo like `action_lifecycle_notifications` is silently ignored and the default takes effect. Use the exact key shown above.

**Requirement:** Telegram delivery needs an OpenClaw gateway token — either `OPENCLAW_GATEWAY_TOKEN` in the runtime's environment, or `gateway_token` in this block.

### Configure Risk Protection

Risk gates are checked before every trade entry. If any gate is not "OPEN", the entry is skipped.

```yaml
risk:
  data_retention_hours: 72    # How long to keep PnL history (max 168 = 7 days)
  guard_rails:
    daily_loss_limit_pct: 4           # Stop if daily loss exceeds 4%
    max_entries_per_day: 6            # Max 6 entries per UTC day
    bypass_max_entries_per_day_on_profit: false  # At cap: allow more if today_snapshot.pnl.delta_since_open > 0
    max_consecutive_losses: 3         # Pause after 3 losers in a row
    cooldown_minutes: 90              # Pause duration after consecutive losses
    drawdown_halt_pct: 20             # Stop if drawdown from peak exceeds 20%
    drawdown_reset_on_day_rollover: false  # Keep drawdown tracking across days
    per_asset_cooldown_minutes: 45    # No re-entry on same asset for 45 min
```

`max_entries_per_day` is enforced using MCP trade and position times as **Unix seconds** vs UTC midnight in seconds (see [`risk-gates.md`](risk-gates.md)). With `bypass_max_entries_per_day_on_profit: true`, at the cap the runtime consults `today_snapshot.pnl.delta_since_open` (shared with daily-loss snapshot when configured, else one lazy MCP call).

All guard rail fields are optional — only configure the gates you want active.

### Connect Your Signal Source

Scanners detect trading opportunities. For an external momentum strategy, you need two scanners:

**Position tracker** (required for exit management):
```yaml
- name: position_tracker
  type: position_tracker
  interval: 10s
```

**External scanner** (receives pushed signals from your producer):
```yaml
- name: external_momentum
  type: external_scanner
  outputs:
    signals: true
    context: false
  config:
    fields:
      sourceScannerId: { type: string, required: true }
      sourceSignalType: { type: string, required: true }
      sourceTimestamp: { type: number, required: true }
      sourceFactors: { type: object, required: true }
      sourceMeta: { type: object, required: true }
```

Rules for external scanners:
- Do NOT set `interval` (it's push-driven, not polled)
- Must enable at least one of `outputs.signals` or `outputs.context`
- Must define `config.fields` with at least one field
- Each field needs a `type`: `string`, `number`, `boolean`, `object`, or `array`

### Define How Trades Are Entered

Actions decide what to do with signals. For LLM-driven entries:

```yaml
- name: momentum_entry
  action_type: OPEN_POSITION
  decision_mode: llm
  decision_model: claude-sonnet-4-20250514
  scanners: [external_momentum]
  min_confidence: 7
  params:
    order_type: FEE_OPTIMIZED_LIMIT
    fee_optimized_limit_options:
      ensure_execution_as_taker: true
      execution_timeout_seconds: 15
  context:
    - type: signal
      scanner: external_momentum
  decision_prompt: |
    Your prompt here...
    {{signal_external_momentum}}
```

| Field | What it does |
|-------|-------------|
| `name` | Action identifier (required) |
| `action_type` | Registered action type (required): `OPEN_POSITION`, `CLOSE_POSITION`, or `POSITION_TRACKER` |
| `decision_mode` | `llm` (AI decides), `rule` (automatic), or `no_decision` (disabled) |
| `decision_model` | Which LLM model to use. **Bare model name only — no provider prefix.** See [Model name format](#model-name-format). |
| `scanners` | Which scanner(s) trigger this action |
| `min_confidence` | Minimum LLM confidence (1-10) to execute the trade |
| `params.order_type` | `MARKET` or `FEE_OPTIMIZED_LIMIT` |
| `context` | What data to inject into the prompt |
| `decision_prompt` | The prompt template with `{{placeholders}}` |

#### Model name format

Pass the **bare** model name to `decision_model`. The runtime forwards it to the OpenClaw `llm-task` gateway, which adds its own provider prefix when routing. Passing a prefixed name causes a double-prefix and the gateway responds with `500 Unknown model`.

| | Example |
|---|---|
| Valid | `gemini-2.5-pro`, `claude-sonnet-4-20250514`, `gemini-3.1-pro-preview` |
| Invalid | `google/gemini-2.5-pro`, `anthropic/claude-sonnet-4-20250514` |

**Provider selection is not configured here.** The runtime auto-detects the backend at boot: if `ANTHROPIC_API_KEY` is set in the runtime's environment, it uses the Anthropic SDK directly; otherwise it routes through the OpenClaw gateway (which requires `OPENCLAW_GATEWAY_TOKEN`). The bare-name rule applies to the gateway path — the default in production.

### Configure Exit Management

The DSL exit engine monitors your open positions and closes them based on configurable rules.

**Top-level `exit` fields:**

| Field | What it does | Default |
|-------|-------------|---------|
| `engine` | Exit engine identifier; typically `dsl` | — |
| `interval_seconds` | DSL polling interval (integer, 5–3600) | `30` |
| `order_type` | Order type for DSL-initiated closes: `MARKET` or `FEE_OPTIMIZED_LIMIT` | `MARKET` |
| `fee_optimized_limit_options.ensure_execution_as_taker` | Ensure fill as taker if limit times out | — |
| `fee_optimized_limit_options.execution_timeout_seconds` | Limit-order timeout (integer, 1–300) | — |
| `dsl_preset` | DSL exit profile (see below) | — |

**Time-based cuts** close positions that aren't performing:
- `hard_timeout`: Close after N minutes no matter what
- `weak_peak_cut`: Close if peak ROE% never reaches `min_value` after N minutes
- `dead_weight_cut`: Close if position is at or below break-even after N minutes

**Phase 1** (fixed floor + trailing):
- `max_loss_pct`: Maximum allowed loss before closing (e.g. 2.5 = close at -2.5%)
- `retrace_threshold`: Once ROE% reaches this level, a trailing floor activates
- `consecutive_breaches_required`: How many consecutive ticks below floor before closing

**Phase 2** (dynamic high-water tiers):
Each tier locks in a percentage of your high-water mark as a floor:
```yaml
tiers:
  - { trigger_pct: 6,  lock_hw_pct: 35 }   # At +6% ROE, lock 35% of peak
  - { trigger_pct: 10, lock_hw_pct: 55 }   # At +10% ROE, lock 55% of peak
  - { trigger_pct: 15, lock_hw_pct: 70 }   # At +15% ROE, lock 70% of peak
  - { trigger_pct: 20, lock_hw_pct: 85 }   # At +20% ROE, lock 85% of peak
```

Tiers must be listed in ascending order by `trigger_pct`.

---

## Template Variables

In your `decision_prompt`, use `{{placeholder}}` to inject data. The placeholder name is derived from your `context` entries:

| Context entry | Placeholder name | What it contains |
|---------------|-----------------|------------------|
| `{ type: signal, scanner: "X" }` | `{{signal_X}}` | Latest signal data from scanner X |
| `{ type: context, scanner: "X" }` | `{{context_X}}` | Retained context artifact from scanner X |
| `{ type: strategy, value: "X" }` | `{{strategy_X}}` | Strategy-level parameter X |
| `{ type: asset-trend, value: "X" }` | `{{asset_trend_X}}` | Asset trend data for X |

Additionally, any key in `params` is available as a placeholder (e.g. `params: { my_val: "hello" }` → `{{my_val}}`).

Every `{{placeholder}}` in your prompt **must** resolve to a declared context entry or param key. If it doesn't, the YAML will fail validation.

---

## Environment Variables

Use `${VAR}` or `${VAR:-default}` in any string value to reference environment variables:

```yaml
strategy:
  wallet: "${WALLET_ADDRESS}"              # Required: resolves from env
notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"  # Resolves from env
```

If the env var is not set, `${VAR}` resolves to an empty string. Use `${VAR:-fallback}` to provide a default.

---

## Required Wiring Rules

These are hard requirements — if you violate them, the YAML will fail to load:

1. **If you use exit management (`dsl_preset`), you must include a position tracker:**
   - A scanner with `type: position_tracker`
   - An action with `action_type: POSITION_TRACKER` subscribed to that scanner

2. **If you have an `OPEN_POSITION` action, you must have a `scanners` block** with at least one scanner defined.

3. **External scanners must NOT have `interval` or `depends_on`** — they are push-driven.

4. **Scanner names must be unique** across the entire YAML.

5. **Every `{{placeholder}}` in a decision_prompt must resolve** to a context entry or params key.

6. **Phase 2 tiers must be sorted ascending** by `trigger_pct`.

7. **`max_loss_pct` is required** when phase1 is enabled — set it in `dsl_preset.phase1.max_loss_pct` or at the preset root.

---

## Validation Errors & Fixes

| Error you see | What's wrong | How to fix |
|---------------|-------------|-----------|
| "name is required at the top" | Missing `name` field | Add `name: my-strategy` at top level |
| "strategy must have a wallet" | Missing or empty wallet | Add `strategy: { wallet: "0x..." }` |
| "when using actions that depend on scanners, you must also add a non-empty 'scanners:' block" | OPEN_POSITION action but no scanners | Add a `scanners:` block with at least one scanner |
| "Duplicate scanner name(s)" | Two scanners have the same name | Rename one of them |
| "external_scanner does not allow interval" | You set `interval` on an external scanner | Remove the `interval` field |
| "external_scanner requires a non-empty config.fields map" | Missing field definitions | Add `config: { fields: { ... } }` |
| "DSL requires at least one position-tracker scanner" | Exit management without position tracking | Add a `position_tracker` scanner |
| "DSL requires a POSITION_TRACKER action" | Exit management without tracker action | Add a `POSITION_TRACKER` action |
| "Action decision_prompt references names with no matching context entry or param" | `{{placeholder}}` doesn't match any context entry | Check that your context entries match your placeholders |
| "max_loss_pct is required" | Phase1 enabled without max_loss_pct | Add `max_loss_pct` to phase1 or preset root |
| "exit / DSL preset validation failed" | Invalid DSL preset structure | Check tier sort order and required fields |
