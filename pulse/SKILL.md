---
name: Pulse
description: Adaptive market scanner that breathes with the market
author: 0xTrench
license: Apache-2.0
compatibility:
  openclaw: ">=0.15.0"
  senpi: ">=1.0.0"
metadata:
  category: scanner
  model_tier: budget
  cron_type: isolated
---

# Pulse — Adaptive Market Scanner

An adaptive, universal market scanner that automatically adjusts its polling frequency based on market signal density.

## Architecture Overview

Pulse operates as a three-state heat engine:

```
    COLD (5min) ←─── signal count drops ─────┐
         │                                   │
         │ signals detected                  │
         ↓                                   │
    WARM (3min) ←─── mixed signals ─────    │
         │                             │    │
         │ high signal density         │    │
         ↓                             │    │
     HOT (90s) ──── action=escalate ───┴────┘
```

### Heat Levels

- **COLD**: 5-minute intervals, baseline monitoring
- **WARM**: 3-minute intervals, elevated activity detected
- **HOT**: 90-second intervals, high signal density requires action

### State Transitions

1. **COLD → WARM**: Signal count ≥ warm_threshold with action=none
2. **WARM → HOT**: Signal count ≥ hot_threshold OR action=escalate
3. **HOT → WARM**: No escalation for hot_persistence cycles
4. **WARM → COLD**: consecutiveEmpty ≥ decay_threshold
5. **HOT → COLD**: No signals for decay_threshold cycles

## Quick Start

1. **Setup**: `python3 scripts/pulse-setup.py --config-path pulse-config.json`
2. **Test**: `python3 scripts/pulse-scanner.py --config-path pulse-config.json --dry-run`
3. **Schedule**: Use the generated cron templates to add to OpenClaw

## Detailed Rules

### Heat Escalation
- Signal source returns `action=escalate` → immediate HOT
- Signal count ≥ warm_threshold → WARM (if COLD)
- Signal count ≥ hot_threshold → HOT (if WARM)

### Heat Decay
- `consecutiveEmpty` counter increments when no signals found
- WARM/HOT → COLD when `consecutiveEmpty ≥ decay_threshold`
- Counter resets when signals detected

### Persistence
- HOT state persists for `hot_persistence` cycles after escalation
- Prevents rapid bouncing between states
- Graceful decay: HOT → WARM → COLD

### State Machine Transitions

```
State Transitions:
- COLD + signals ≥ warm_threshold → WARM
- WARM + signals ≥ hot_threshold OR action=escalate → HOT  
- HOT + no escalation for hot_persistence cycles → WARM
- WARM/HOT + consecutiveEmpty ≥ decay_threshold → COLD
```

## API Dependencies

Pulse requires these Senpi MCP tools:

- **Primary signal source**: `leaderboard_get_smart_money_token_inflows` (configurable)
- **Alternative sources**: Any Senpi leaderboard or market tool that returns signal arrays

The signal source is configurable via `signal_source` in config:
- `"leaderboard_get_smart_money_token_inflows"` (default)
- `"leaderboard_get_trader_performance"` 
- Custom script path: `"/path/to/custom-scanner.py"`

## State Schema

See `references/state-schema.md` for complete heat state file structure.

Minimal state:
```json
{
  "level": "cold|warm|hot",
  "consecutiveEmpty": 0,
  "lastEscalation": "2026-03-04T07:31:19Z",
  "updatedAt": "2026-03-04T08:33:42Z"
}
```

## Cron Setup

See `references/cron-templates.md` for ready-to-use cron mandates.

### Main Session (System Events)
```bash
openclaw cron add "Pulse Scanner" \
  --schedule "*/5 * * * *" \
  --session-target main \
  --payload '{"kind":"systemEvent","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py"}' \
  --wake-mode now
```

### Isolated Session (Dedicated Agent)
```bash
openclaw cron add "Pulse Scanner" \
  --schedule "*/5 * * * *" \
  --session-target isolated \
  --payload '{"kind":"agentTurn","text":"Run pulse scanner: /data/workspace/skills/pulse/scripts/pulse-scanner.py"}' \
  --wake-mode now
```

## Known Limitations

1. **Signal Source Coupling**: Default assumes Senpi leaderboard format with `signals` array
2. **Interval Granularity**: OpenClaw cron minimum is 1 minute (90s rounds up)
3. **State Persistence**: Requires writable state directory
4. **Error Recovery**: Failed API calls don't reset heat state (fail-safe behavior)
5. **Multi-Instance**: Requires unique `instance_id` in config for parallel scanners

## Configuration Reference

### Required Parameters
- `signal_source`: Tool name or script path for signal detection
- `state_file`: Path to heat state JSON file
- `warm_threshold`: Signal count to trigger WARM state
- `hot_threshold`: Signal count to trigger HOT state

### Tunable Parameters
- `cold_interval_ms`: 300000 (5 minutes)
- `warm_interval_ms`: 180000 (3 minutes)  
- `hot_interval_ms`: 90000 (90 seconds)
- `decay_threshold`: 3 (cycles before cooling)
- `hot_persistence`: 5 (cycles before HOT→WARM)

### Signal Filtering
- `min_traders`: Minimum trader count per signal
- `min_velocity`: Minimum velocity threshold
- `max_price_change_4h`: Price change ceiling (percentage)

### Entry Actions
- `escalation_action`: "alert" | "script" | "wake_main"
- `escalation_script`: Script to run on action=escalate
- `alert_target`: Notification target (telegram:123456)

### Instance Support
- `instance_id`: Unique identifier for parallel scanners
- `state_dir`: Directory for instance-specific state files

See `pulse-config.py` for complete `PulseConfig` dataclass definition.