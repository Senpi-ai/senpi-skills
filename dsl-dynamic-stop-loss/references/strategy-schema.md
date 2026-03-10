# Strategy Config Schema (`strategy-{strategyId}.json`)

Strategy-level DSL configuration and status. Stored at `{DSL_STATE_DIR}/{strategyId}/strategy-{strategyId}.json`. Created by `dsl-cli.py add-dsl`; updated by `update-dsl`, `pause-dsl`, `resume-dsl`, and `delete-dsl`. Cron job ID is generated and stored by the CLI when a cron is first needed.

## Full Example

```json
{
  "strategyId": "uuid-of-strategy",
  "wallet": "0xStrategyWalletAddress",
  "status": "active",
  "createdAt": "2026-03-07T10:00:00.000Z",
  "updatedAt": "2026-03-07T10:00:00.000Z",
  "createdBySkill": "wolf-strategy",
  "cronJobId": "cron-job-uuid-from-openclaw",
  "defaultConfig": {
    "phase1": {
      "enabled": true,
      "retraceThreshold": 0.03,
      "consecutiveBreachesRequired": 3
    },
    "phase2TriggerTier": 0,
    "phase2": {
      "enabled": true,
      "retraceThreshold": 0.015,
      "consecutiveBreachesRequired": 1,
      "tiers": [
        {"triggerPct": 10, "lockPct": 5},
        {"triggerPct": 20, "lockPct": 14},
        {"triggerPct": 30, "lockPct": 22, "retrace": 0.012},
        {"triggerPct": 50, "lockPct": 40, "retrace": 0.010},
        {"triggerPct": 75, "lockPct": 60, "retrace": 0.008},
        {"triggerPct": 100, "lockPct": 80, "retrace": 0.006}
      ]
    }
  },
  "positions": {
    "ETH": {"dex": "main", "addedAt": "2026-03-07T10:00:00.000Z"},
    "xyz:SILVER": {"dex": "xyz", "addedAt": "2026-03-07T10:00:00.000Z"}
  }
}
```

## Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `strategyId` | string | Strategy UUID |
| `wallet` | string | Strategy wallet address (0x...) |
| `status` | string | `active` \| `paused` \| `completed` |
| `createdAt` | string | ISO 8601 timestamp |
| `updatedAt` | string | ISO 8601 timestamp |
| `createdBySkill` | string | Skill that created DSL (e.g. `wolf-strategy`, `dsl-tight`) |
| `cronJobId` | string | OpenClaw cron job ID; generated and stored by the CLI when cron is needed (add-dsl); agent creates the OpenClaw cron with this ID |
| `cronScheduleMinutes` | number | Interval (minutes) the current cron was created with. Set by CLI on add-dsl (when creating cron) and on update-dsl when `cronIntervalMinutes` changes. Used to detect interval changes so the CLI can output remove/recreate cron intent. |
| `defaultConfig` | object | Default DSL config applied to new positions (see [state-schema.md](state-schema.md) for phase/tier fields). `cronIntervalMinutes` (default 3) is **general** — how often the DSL cron runs; when changed, the agent must remove the old cron and create a new one with the new schedule. May also include Phase 1 time-based options as extensible objects: `phase1.hardTimeout`, `phase1.weakPeakCut`, `phase1.deadWeightCut` (each with `enabled`, `intervalInMinutes`; weakPeakCut also `minValue`). |
| `positions` | object | Map of asset → `{ dex, addedAt }` for audit. Reconciles to current position state files on disk whenever the CLI saves the strategy config (stale entries are removed). |

### `status` values

| Value | Meaning |
|-------|---------|
| `active` | DSL cron running; positions monitored |
| `paused` | DSL cron runs but all positions have `active: false` |
| `completed` | DSL torn down; strategy dir retained until cleanup |

## Relationship to position state files

- **Position state files** (`{asset}.json`, `xyz--SYMBOL.json`) hold per-position runtime state (highWaterPrice, currentTierIndex, floorPrice, etc.) and are created from `defaultConfig` with optional overrides.
- **strategy-{strategyId}.json** is the source of truth for strategy-level defaults and cron ID. The cron runner (`dsl-v5.py`) does not read it; it only lists position state files in the strategy directory and skips files whose name starts with `strategy-`.

## Path conventions

- Base directory: `DSL_STATE_DIR` (default `/data/workspace/dsl`)
- Strategy directory: `{DSL_STATE_DIR}/{strategyId}/`
- Strategy config: `{DSL_STATE_DIR}/{strategyId}/strategy-{strategyId}.json`
- Position state: `{DSL_STATE_DIR}/{strategyId}/{asset}.json` (main) or `{DSL_STATE_DIR}/{strategyId}/xyz--SYMBOL.json` (xyz)

See [state-schema.md](state-schema.md) for position state file schema and path rules.
