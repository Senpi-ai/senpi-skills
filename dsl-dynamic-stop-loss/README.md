# Dynamic Stop Loss (DSL) v5.3.1

Two-phase trailing stop for Hyperliquid perps. Protects profits, limits losses, and syncs the stop loss directly to the exchange so positions are protected even if the cron process goes down.

## What DSL Does

When you open a leveraged position, DSL watches it:

**Phase 1 — "Let It Breathe."** Wide retrace (3% ROE from high water) with breach counting (1 consecutive breach before close by default). An absolute price floor caps maximum loss. The trade has room to develop without getting shaken out by normal noise.

**Phase 2 — "Lock the Bag."** Once the position hits the first profit tier (default 10% ROE), DSL shifts to tight trailing with tiered floors that ratchet up and never come back down. One breach to close. As profit grows, the floor tightens — at 50% ROE, only 1.0% retrace is allowed. Winners are protected aggressively.

**Exchange SL sync.** DSL doesn't just track the floor internally — it sets the actual stop loss on Hyperliquid via `edit_position`. If the cron process crashes, HL still executes the SL at the last synced price. Phase 1 uses MARKET orders (fast exit on loss). Phase 2 uses LIMIT orders (fee-optimized exit on profit).

## What's New in v5.3.1

### High Water Mode (`lockMode: "pct_of_high_water"`)
Trailing floor can be a **percentage of high-water ROE** instead of a fixed ROE lock. Set `lockMode: "pct_of_high_water"` and use `lockHwPct` on tiers (e.g. 85% of peak ROE). The floor trails the peak every tick with no ceiling — see [dsl-high-water-spec 1.0.md](dsl-high-water-spec%201.0.md) and [dsl-high-water-implementation-spec.md](dsl-high-water-implementation-spec.md).

**Upgrading from ROE-based (fixed_roe)?** If you have existing strategies or positions using fixed ROE tiers, we recommend migrating to High Water using `update-dsl` with a High Water config (e.g. `--configuration @dsl-profile.json`). See [references/migration.md](references/migration.md#migrating-from-roe-based-fixed_roe-to-high-water).

### What's New in v5.2

### CLI Lifecycle Manager (`dsl-cli.py`)
Full lifecycle management via command line. No more hand-editing state files.

| Command | What It Does |
|---|---|
| `add-dsl` | Creates strategy config + position state files. Outputs cron setup instructions. |
| `update-dsl` | Patches config (strategy-wide or per-position). Detects cron interval changes. |
| `pause-dsl` | Pauses monitoring without losing state. |
| `resume-dsl` | Resumes monitoring. |
| `delete-dsl` | Archives state, outputs cron removal instructions. |
| `status-dsl` | Reports current status of all positions. |
| `count-dsl` | Aggregate position counts per strategy. |
| `validate` | Validates a DSL config file before deploying. |

### Multi-Skill Integration
Any skill can now use DSL by calling the same CLI with its own profile. WOLF, FOX, TIGER, HAWK, OWL, SHARK — all use DSL through `dsl-cli.py add-dsl` with `--skill <their-skill> --configuration @<their-profile>`. One DSL engine, many consumers.

### SL Order Verification
After setting a stop loss via `edit_position`, DSL now calls `execution_get_order_status` to verify the SL was accepted. If the SL was filled (HL executed it between ticks), the state file is archived as `{asset}_archived_sl_{epoch}.json` — distinguishing exchange-executed stops from script-executed stops.

### Improved Reconciliation
Every tick, DSL cross-references local state files against the live clearinghouse. Four archive types track exactly what happened:

| Archive Suffix | Meaning |
|---|---|
| `_archived_{epoch}` | Script detected breach and closed |
| `_archived_sl_{epoch}` | Hyperliquid executed the SL order |
| `_archived_external_{epoch}` | Position closed externally (user, other agent, liquidation) |
| `_archived_inactive_{epoch}` | Strategy went inactive |

### Configurable Cron Interval
`cronIntervalMinutes` is now in the config (default 3). Change it via `update-dsl` and the CLI outputs `cron_schedule_changed: true` so the agent knows to recreate the cron.

### Config Validation
`dsl-cli.py validate --configuration @profile.json` checks tier ordering, retrace ranges, breach counts, and phase consistency before anything is deployed.

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| v5.3.1 | 2026-03-13 | High Water: infinite trail (point 4), SL sync every tick when floor moves (point 6); spec checklist tests |
| v5.3 | 2026-03-12 | High Water Mode: `lockMode: "pct_of_high_water"`, `lockHwPct` per tier; floor as % of peak ROE |
| v5.2 | 2026-03-01 | CLI lifecycle manager (add/update/pause/resume/delete/status/count/validate), multi-skill integration, SL order verification, improved reconciliation (4 archive types), configurable `cronIntervalMinutes`, config validation |
| v5.0 | — | Two-phase trailing stop, exchange SL sync, Phase 1/2 tiers |

## Default Profile

```json
{
  "phase1": {
    "enabled": true,
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 1
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
}
```

## Quick Start

```bash
# Add DSL to a strategy (auto-discovers positions from clearinghouse)
python3 scripts/dsl-cli.py add-dsl <strategy-id> \
  --skill dsl-dynamic-stop-loss \
  --configuration @dsl-profile.json

# If output says cron_needed: true → create the OpenClaw cron with the provided ID and schedule

# Check status
python3 scripts/dsl-cli.py status-dsl <strategy-id>

# Update config (e.g., tighter Phase 1)
python3 scripts/dsl-cli.py update-dsl <strategy-id> \
  --configuration '{"phase1":{"retraceThreshold":0.05}}'

# Remove DSL from a strategy
python3 scripts/dsl-cli.py delete-dsl <strategy-id>
# If output says cron_to_remove → remove that OpenClaw cron
```

## Files

| File | Purpose |
|---|---|
| `scripts/dsl-v5.py` | Cron runner — monitors positions, syncs SL to HL, closes on breach |
| `scripts/dsl-cli.py` | Lifecycle CLI — add, update, pause, resume, delete, status, count, validate |
| `scripts/dsl-cleanup.py` | Strategy directory cleanup |
| `dsl-profile.json` | Default DSL configuration |

## Requirements

- Python 3
- `mcporter` CLI (configured with Senpi auth)
- OpenClaw cron system
- Hyperliquid perp positions (main dex + xyz dex)

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
Source: https://github.com/Senpi-ai/senpi-skills
