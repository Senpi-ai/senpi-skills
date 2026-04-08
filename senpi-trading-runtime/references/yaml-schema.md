# Runtime YAML Schema Reference

Complete field reference for the runtime YAML configuration. Environment variable substitution: `${VAR}` and `${VAR:-default}` resolved at load time.

---

## Table of Contents

- [Top-level keys](#top-level-keys)
- [strategies](#strategies)
- [scanners](#scanners)
- [actions](#actions)
- [exit (DSL)](#exit-dsl)
- [notifications](#notifications)

---

## Top-level keys

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `name` | string | Yes | Runtime name (min 1 char). |
| `version` | string | No | Version identifier. |
| `description` | string | No | Human-readable description. |
| `strategies` | object | Yes | Keyed strategy configs (non-empty). |
| `scanners` | array | Yes | Scanner definitions. |
| `actions` | array | Yes | Action definitions. |
| `exit` | object | Yes | Exit / DSL block. |
| `notifications` | object | No | Notification config. |

---

## strategies

Each key is a strategy name; value is the strategy config. At least one strategy required.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `wallet` | string | Yes | Wallet address. Supports `${VAR}` substitution. This is the **strategy identifier** used everywhere. |
| `budget` | number | No | Total budget for this strategy. |
| `slots` | number | No | Max concurrent positions. |
| `margin_per_slot` | number | No | Margin allocated per position slot. |
| `trading_risk` | string | No | `conservative`, `moderate`, or `aggressive`. |
| `enabled` | boolean | No | Default `true`. Set `false` to disable without removing. |

```yaml
strategies:
  main:
    wallet: "${WALLET_ADDRESS}"
    budget: 500
    slots: 2
    margin_per_slot: 200
    trading_risk: conservative
    enabled: true
```

---

## scanners

Array of scanner definitions.

### position_tracker

Monitors a wallet's positions on Hyperliquid and detects lifecycle events (opened, closed, increased, decreased, flipped). Enables DSL protection for all positions of the strategy address — even those opened manually or by other tools.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `name` | string | Yes | Unique name, referenced in `actions[].scanners`. |
| `type` | string | Yes | `position_tracker` |
| `interval` | string | Yes | Poll interval, e.g. `"10s"`. |

```yaml
scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s
```

---

## actions

### POSITION_TRACKER action

Processes position lifecycle signals from the position_tracker scanner.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `name` | string | Yes | Unique action name. |
| `action_type` | string | Yes | `POSITION_TRACKER` |
| `decision_mode` | string | Yes | `rule` (automatic processing). |
| `scanners` | array | Yes | Must reference the position_tracker scanner. |

```yaml
actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]
```

---

## exit (DSL)

The DSL exit block configures the Dynamic Stop-Loss engine. Uses `dsl_preset` (singular) for a single exit profile.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `engine` | string | Yes | — | Must be `"dsl"`. |
| `interval_seconds` | integer | No | `30` | How often the price monitor runs (seconds). Range: 5–3600. |
| `dsl_preset` | object | Yes | — | Single preset config (see below). |

For full DSL configuration details: [DSL Configuration Reference](dsl-configuration.md)

### dsl_preset fields

**Time-based cuts** (at preset level, evaluated in Phase 1 only):

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `hard_timeout` | object | No | Close after N minutes in Phase 1. |
| `weak_peak_cut` | object | No | Close if peak ROE stayed below threshold. |
| `dead_weight_cut` | object | No | Close if position never went positive. |
| `phase1` | object | Yes | Phase 1 config. |
| `phase2` | object | Yes | Phase 2 config with tiers. |

Time-based cuts are defined at the preset level (siblings of `phase1` and `phase2`).

### phase1

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `enabled` | boolean | No | `true` | `false` = skip Phase 1 rules. |
| `max_loss_pct` | number | Yes | — | Max loss % from entry. Range: (0, 100]. |
| `retrace_threshold` | number | Yes* | — | ROE % retrace from high-water. *When enabled. |
| `consecutive_breaches_required` | integer | Yes* | — | Ticks below floor before exit (>= 1). *When enabled. |

### phase2

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `enabled` | boolean | No | `true` | `false` = never transition to Phase 2. |
| `tiers` | array | Yes | — | Ordered tier objects (see below). |

Phase 2 is exchange-SL driven. It starts at tier 0 (first tier).

### Tier definition

Each tier has exactly two fields. Tiers must be sorted ascending by `trigger_pct`.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `trigger_pct` | number | Yes | ROE % from entry that activates this tier. Strictly increasing. |
| `lock_hw_pct` | number | Yes | Lock floor at this % of high-water ROE (0-100). |

```yaml
exit:
  engine: dsl
  interval_seconds: 30
  dsl_preset:
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
```

---

## notifications

| Key | Type | Description |
|-----|------|-------------|
| `telegram_chat_id` | string | Telegram chat ID for lifecycle notifications. Supports `${VAR}` substitution. |

```yaml
notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```
