# DSL CLI Commands — Design Plan

## Overview

This document defines the design for exposing DSL lifecycle operations as Python CLI commands. The goal is to make the DSL skill composable — other skills (e.g. `wolf-strategy`, `dsl-tight`) can invoke DSL setup, updates, and teardown programmatically without duplicating DSL logic.

### Core Constraint

**One DSL per strategy.** A single strategy-level configuration governs all positions within the strategy. Positions added after initial setup are automatically picked up by the existing cron. The cron is created once and lives for the lifetime of the DSL on that strategy.

---

## Architecture

### New File: `scripts/dsl-cli.py`

A single script with subcommands (via Python `argparse` subparsers). Each subcommand corresponds to one CLI operation. The script reads/writes strategy and position state files and manages the cron.

```
scripts/
  dsl-v5.py          # existing — cron runner (unchanged)
  dsl-cleanup.py     # existing — strategy dir teardown (unchanged)
  dsl-cli.py         # NEW — lifecycle CLI with subcommands
```

### New File: Strategy Config (`strategy-{strategyId}.json`)

A new strategy-level JSON file stored alongside position state files, with strategy ID in the filename:

```
{DSL_STATE_DIR}/{strategyId}/
  strategy-{strategyId}.json   # strategy-level DSL config + status
  ETH.json                     # per-position state
  BTC.json                     # per-position state
  xyz--SILVER.json             # per-position state
```

`strategy-{strategyId}.json` is the source of truth for:
- Default DSL configuration (tiers, phases, breach settings)
- DSL status at strategy level: `active`, `paused`, `completed`
- Metadata: `createdAt`, `createdBySkill`, `strategyId`, `wallet`

Position state files (`{asset}.json`) continue to hold per-position runtime state (highWater, currentTier, floorPrice, etc.). On `add-dsl`, position state files are created from the strategy-level defaults, with optional per-position overrides.

---

## Strategy Config Schema (`strategy-{strategyId}.json`)

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
      "retraceThreshold": 0.03,
      "consecutiveBreachesRequired": 3
    },
    "phase2TriggerTier": 0,
    "phase2": {
      "retraceThreshold": 0.015,
      "consecutiveBreachesRequired": 1
    },
    "tiers": [
      {"triggerPct": 10, "lockPct": 5},
      {"triggerPct": 20, "lockPct": 14},
      {"triggerPct": 30, "lockPct": 22, "retrace": 0.012},
      {"triggerPct": 50, "lockPct": 40, "retrace": 0.010},
      {"triggerPct": 75, "lockPct": 60, "retrace": 0.008},
      {"triggerPct": 100, "lockPct": 80, "retrace": 0.006}
    ],
  },
  "positions": {
    "ETH": {"dex": "main", "addedAt": "2026-03-07T10:00:00.000Z"},
    "xyz:SILVER": {"dex": "xyz", "addedAt": "2026-03-07T10:00:00.000Z"}
  }
}
```

### `status` field values

| Value | Meaning |
|-------|---------|
| `active` | DSL cron running, positions monitored |
| `paused` | DSL cron still runs but all positions have `active: false` — no monitoring, no closes |
| `completed` | DSL archived (positions all closed or deleted); strategy dir retained until cleanup |

---

## CLI Subcommands

### Global Arguments (all subcommands)

| Arg | Default | Description |
|-----|---------|-------------|
| `--state-dir` | `/data/workspace/dsl` | Overrides `DSL_STATE_DIR` env var |

---

### `add-dsl`

Sets up DSL for a strategy. Creates strategy config `strategy-{strategyId}.json` and per-position state files. Creates the cron if none exists for this strategy.

#### Signatures

```bash
# Add DSL for a single position, defaults, called from another skill
python3 scripts/dsl-cli.py add-dsl <strategy-id> <asset> <dex> --skill <skill-name>

# Add DSL for all current positions in the strategy (agent resolves positions from MCP)
python3 scripts/dsl-cli.py add-dsl <strategy-id> --skill <skill-name>

# Add DSL for a single position with custom configuration
python3 scripts/dsl-cli.py add-dsl <strategy-id> <asset> <dex> [--configuration ...]

# Add DSL for all current positions with custom configuration
python3 scripts/dsl-cli.py add-dsl <strategy-id> [--configuration ...]
```

#### Positional Arguments

| Arg | Required | Description |
|-----|----------|-------------|
| `strategy-id` | Yes | Strategy UUID |
| `asset` | No | Asset ticker (e.g. `ETH`, `xyz:SILVER`). Omit to apply to all positions. |
| `dex` | No | `main` or `xyz`. Required if `asset` is specified. |

#### Optional Arguments

| Arg | Description |
|-----|-------------|
| `--skill <name>` | Name of the calling skill (e.g. `wolf-strategy`). Stored in the strategy config as `createdBySkill`. |
| `--wallet <addr>` | Strategy wallet address. Optional — fetched from MCP `strategy_get` if not provided and not already in the strategy config. Passing it skips the MCP call. |
| `--entry-price <float>` | Optional override for entry price. When omitted, entry price is read from clearinghouse state. Useful when the calling skill has a more precise average fill price from a multi-fill open. |
| `--configuration <json>` | JSON string or `@file.json` with DSL config (see Configuration Schema below). Applies to position if `asset` specified; becomes strategy default if no `asset`. |

#### Behavior

1. If the strategy config file (`strategy-{strategyId}.json`) does not exist: create it with `status: active`, `defaultConfig` from defaults or `--configuration`, `createdBySkill` from `--skill`, and emit cron intent.
2. If the strategy config exists and `status: active`: append the new position(s) without touching the cron.
3. **In all cases** (whether `asset` is specified or not), the CLI calls mcporter to resolve position data:
   - `strategy_get(strategy_id)` → wallet (skipped if wallet already in strategy config or passed via `--wallet`)
   - `strategy_get_clearinghouse_state(wallet)` → position data (direction, leverage, size, entryPrice) for the asset(s)
4. If `asset` specified: resolves that single position from clearinghouse, creates `{asset}.json`. Uses `--entry-price` override if provided; otherwise uses clearinghouse `entryPrice`.
5. If `asset` omitted: creates a state file for every active position found in clearinghouse. Positions already having an active state file are skipped (idempotent).
6. Cron: If `cronJobId` already exists in the strategy config, no new cron is created. Otherwise, CLI outputs cron intent (see Cron Management below) for the agent to execute.

#### Output (stdout, JSON)

```json
{
  "status": "ok",
  "strategy_id": "uuid",
  "action": "created" | "updated",
  "positions_added": ["ETH", "xyz:SILVER"],
  "cron_created": true,
  "cron_job_id": "cron-uuid"
}
```

---

### `update-dsl`

Updates DSL configuration. Does **not** modify runtime state (highWater, currentTier, etc.) — only config fields. Takes effect on the next cron run.

#### Signatures

```bash
# Update strategy-level default config (applies to all new positions going forward; does NOT retroactively change existing position files)
python3 scripts/dsl-cli.py update-dsl <strategy-id> --configuration <json>

# Update config for a single position only (overrides strategy default in that position's state file)
python3 scripts/dsl-cli.py update-dsl <strategy-id> <asset> <dex> --configuration <json>
```

#### Behavior

- **Strategy-level update** (`update-dsl strategy-id --configuration ...`): Updates `defaultConfig` in the strategy config AND retroactively patches all existing active position state files with the new config fields. Runtime fields (highWater, currentTier, floorPrice, etc.) are preserved in each position file — only config fields are overwritten. Takes effect on the next cron run.
- **Position-level update** (`update-dsl strategy-id asset dex --configuration ...`): Merges the `--configuration` into the specified `{asset}.json` state file only. Runtime fields preserved; only config fields are patched.
- Both modes update `updatedAt` in the strategy config.
- If `absoluteFloor` is not included in the config patch but `phase1.retraceThreshold` is changed, `absoluteFloor` is recalculated automatically from `entryPrice`, `leverage`, and the new `retraceThreshold` (ROE formula: LONG → `entry × (1 - retrace/leverage)`, SHORT → `entry × (1 + retrace/leverage)`).

#### Output

```json
{
  "status": "ok",
  "strategy_id": "uuid",
  "scope": "strategy" | "position",
  "asset": "ETH",
  "fields_updated": ["tiers", "phase1.retraceThreshold"]
}
```

---

### `pause-dsl`

Pauses DSL monitoring. The cron continues to run but does nothing for paused positions.

#### Signatures

```bash
# Pause all positions in a strategy
python3 scripts/dsl-cli.py pause-dsl <strategy-id>

# Pause a single position
python3 scripts/dsl-cli.py pause-dsl <strategy-id> <asset> <dex>
```

#### Behavior

- **Strategy-level pause**: Sets `status: "paused"` in the strategy config. Sets `active: false` in all active position state files (preserving runtime state).
- **Position-level pause**: Sets `active: false` in that position's state file only. Strategy status remains `active`.
- The cron still runs but `dsl-v5.py` skips positions with `active: false`.
- Runtime state (highWater, currentTier, etc.) is preserved for resume.

> **Note**: A new field `pausedAt` (ISO timestamp) is added to each paused state file so resume can log how long it was paused.

#### Output

```json
{
  "status": "ok",
  "strategy_id": "uuid",
  "scope": "strategy" | "position",
  "paused": ["ETH", "xyz:SILVER"]
}
```

---

### `resume-dsl`

Resumes paused DSL monitoring.

#### Signatures

```bash
# Resume all paused positions in a strategy
python3 scripts/dsl-cli.py resume-dsl <strategy-id>

# Resume a single paused position
python3 scripts/dsl-cli.py resume-dsl <strategy-id> <asset> <dex>
```

#### Behavior

- **Strategy-level resume**: Sets `status: "active"` in the strategy config. Sets `active: true` in all position state files that were paused (have `pausedAt` set). Removes `pausedAt` field.
- **Position-level resume**: Sets `active: true` in that position's state file. Removes `pausedAt`.
- `highWaterPrice` and all runtime state are preserved from before the pause — monitoring resumes from where it left off.
- If the position has moved significantly while paused, agent should consider whether to recalculate floors. The CLI does not do this automatically.

#### Output

```json
{
  "status": "ok",
  "strategy_id": "uuid",
  "scope": "strategy" | "position",
  "resumed": ["ETH", "xyz:SILVER"]
}
```

---

### `delete-dsl`

Tears down DSL for a strategy (or single position). Archives state files and removes the cron.

#### Signatures

```bash
# Delete DSL for entire strategy
python3 scripts/dsl-cli.py delete-dsl <strategy-id>

# Delete DSL for a single position (DSL continues on other positions)
python3 scripts/dsl-cli.py delete-dsl <strategy-id> <asset> <dex>
```

#### Behavior

- **Strategy-level delete**:
  1. Sets `active: false` in all position state files.
  2. Renames each active position state file to `{asset}_archived_deleted_{epoch}.json`.
  3. Renames the strategy config to `strategy_archived_{epoch}.json`.
  4. Removes the cron job (using `cronJobId` from the strategy config).
  5. Runs `dsl-cleanup.py` to remove the strategy directory (since no active files remain).
- **Position-level delete**:
  1. Sets `active: false` in `{asset}.json`.
  2. Renames `{asset}.json` to `{asset}_archived_deleted_{epoch}.json`.
  3. Removes the position from `positions` map in the strategy config.
  4. Does NOT remove cron — remaining positions still monitored.
  5. If no active positions remain, also removes cron and runs cleanup.

#### Output

```json
{
  "status": "ok",
  "strategy_id": "uuid",
  "scope": "strategy" | "position",
  "archived": ["ETH", "xyz:SILVER"],
  "cron_removed": true,
  "cleanup_run": true
}
```

---

### `status-dsl`

Reports current DSL status.

#### Signatures

```bash
# Strategy-level status
python3 scripts/dsl-cli.py status-dsl <strategy-id>

# Position-level status
python3 scripts/dsl-cli.py status-dsl <strategy-id> <asset> <dex>
```

#### Output — Strategy level

```json
{
  "strategy_id": "uuid",
  "status": "active" | "paused" | "completed",
  "created_by_skill": "wolf-strategy",
  "cron_job_id": "cron-uuid",
  "positions": {
    "ETH": {
      "dex": "main",
      "status": "active",
      "phase": 2,
      "current_tier": 2,
      "high_water_price": 3200.00,
      "floor_price": 3150.00,
      "last_check": "2026-03-07T09:57:00Z"
    },
    "xyz:SILVER": {
      "dex": "xyz",
      "status": "paused",
      "phase": 1,
      "current_tier": -1,
      "high_water_price": 32.10,
      "floor_price": 31.50,
      "last_check": "2026-03-07T09:57:00Z"
    }
  }
}
```

#### Output — Position level

```json
{
  "strategy_id": "uuid",
  "asset": "ETH",
  "dex": "main",
  "status": "active",
  "phase": 2,
  "direction": "LONG",
  "leverage": 10,
  "entry_price": 3000.00,
  "high_water_price": 3200.00,
  "floor_price": 3150.00,
  "current_tier_index": 2,
  "current_breach_count": 0,
  "last_check": "2026-03-07T09:57:00Z",
  "last_price": 3185.00
}
```

---

### `count-dsl`

Returns aggregate counts of position states for a strategy.

#### Signature

```bash
python3 scripts/dsl-cli.py count-dsl <strategy-id>
```

#### Output

```json
{
  "strategy_id": "uuid",
  "total": 4,
  "active": 2,
  "paused": 1,
  "completed": 1,
  "positions": {
    "active": ["ETH", "BTC"],
    "paused": ["xyz:SILVER"],
    "completed": ["HYPE"]
  }
}
```

`completed` = positions whose state files have been archived (files matching `*_archived_*.json`). They are counted but not monitored.

---

## Configuration Architecture

### Ownership Model

Each skill that integrates DSL **owns its own configuration**. It is not embedded in `dsl-cli.py`. Instead:

- Each skill (`dsl-dynamic-stop-loss`, `wolf-strategy`, `dsl-tight`, etc.) can ship a **`dsl-profile.json`** (or any JSON file) with its DSL configuration.
- Calling skills pass config via **`--configuration @/path/to/dsl-profile.json`** (or inline JSON). There is no separate preset mechanism — only configuration.

This keeps `dsl-cli.py` decoupled from skill-specific logic. Adding a new skill integration never requires modifying `dsl-cli.py`.

```
wolf-strategy/
  dsl-profile.json          # wolf's DSL config — owned by wolf-strategy
  scripts/open-position.py  # passes --configuration @dsl-profile.json to dsl-cli.py

dsl-tight/
  dsl-profile.json          # tight's DSL config — owned by dsl-tight
  SKILL.md                  # documents using --configuration @dsl-profile.json

dsl-dynamic-stop-loss/
  scripts/dsl-cli.py        # reads config from --configuration only
```

---

### Configuration Resolution Order (field-level precedence)

When building the final config for a position state file, fields are resolved in this order (highest wins):

| Priority | Source | How supplied |
|----------|--------|--------------|
| 1 (highest) | `--configuration` fields | Inline JSON or `@filepath.json` |
| 2 | Strategy `defaultConfig` | Already in strategy config (on subsequent `add-dsl` calls) |
| 3 (lowest) | DSL hardcoded defaults | Fallback in `dsl-cli.py` |

**Tiers are atomic**: the `tiers` array is replaced entirely, never merged element-by-element. If `--configuration` includes `tiers`, that array fully replaces the default tiers. There is no partial tier override.

**`absoluteFloor` is always recalculated** from `entryPrice`, `leverage`, and the resolved `phase1.retraceThreshold` — unless `absoluteFloor` is explicitly set in `--configuration`, which locks it to that value.

---

### Two-Phase Model (Optional Phases)

There are **at most two phases**. Both are optional; the user can enable one or both, but never more than two.

| Phase | Purpose | When enabled |
|-------|---------|--------------|
| **Phase 1** | Capital protection — set SL to avoid losing original money (absolute floor + trailing from high water). | User sets `phase1.enabled: true`. |
| **Phase 2** | Tier-based profit locking. Tiers (triggerPct, lockPct, retrace, breachesRequired) **exist only in Phase 2**. | User sets `phase2.enabled: true`. Phase 2 config includes `tiers` (required when Phase 2 is enabled). |

**Valid combinations**

- **Both phases:** Start in Phase 1 (capital protection). When ROE reaches the tier at index `phase2TriggerTier` (default 0), transition to Phase 2 (tier-based). Tiers are used only in Phase 2.
- **Phase 1 only:** `phase1.enabled: true`, `phase2.enabled: false`. No tiers. SL is purely capital protection (absolute floor + trailing); no tier upgrades.
- **Phase 2 only:** `phase1.enabled: false`, `phase2.enabled: true`. Start in Phase 2 from the first tick. Tiers must be non-empty. Before any tier triggers, effective floor is trailing from high water using `phase2.retraceThreshold` (no Phase 1 absolute floor).

**Rules**

- At least one of `phase1.enabled` or `phase2.enabled` must be true.
- If only Phase 2 is enabled, `tiers` must be provided and non-empty.
- **breachDecay is not a user input.** The runner uses a fixed internal behavior (e.g. reset breach count to 0 on recovery). Do not expose or require `breachDecay` in configuration or state.

---

### Configuration Schema (`--configuration`)

Passed as a JSON string inline (`--configuration '{"phase1": ...}'`) or via file reference (`--configuration @/path/to/profile.json`). All fields are optional — omitted fields fall through to lower-priority sources.

```json
{
  "phase1": {
    "enabled": true,
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": null
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

- **phase1.enabled** / **phase2.enabled**: Optional; default `true` when omitted for backward compatibility. At least one must be true. Tiers are used only when Phase 2 is enabled; put `tiers` inside `phase2` or at top level (CLI merges into state).
- **breachDecay** and **stagnation** are not configuration parameters; do not expose them in config.

#### Per-Tier Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `triggerPct` | float | Yes | ROE % that activates this tier |
| `lockPct` | float | Yes | % of (entry → high water) range to lock as floor |
| `retrace` | float | No | Per-tier trailing retrace override (ROE fraction). Uses `phase2.retraceThreshold` if omitted. |
| `breachesRequired` | int | No | Per-tier consecutive breach count. Uses `phase2.consecutiveBreachesRequired` if omitted. Allows tightening as profit grows (e.g. 3→2→2→1). |

The `breachesRequired` field on each tier is the mechanism that lets skills like `dsl-tight` and `wolf-strategy` tighten their breach requirements tier-by-tier without needing a separate global override.

---

Use **`--configuration @path`** (or inline JSON) only. Different styles = different config files (e.g. `conservative.json`, `aggressive.json`).



### Skill Config Profiles

#### `wolf-strategy/dsl-profile.json`

Wolf uses a 4-tier config with tighter phase 1 (10% ROE absolute floor = wide initial stop), and per-tier breach counts that tighten at the top tier.

```json
{
  "phase1": {
    "retraceThreshold": 0.10,
    "consecutiveBreachesRequired": 3
  },
  "phase2TriggerTier": 0,
  "phase2": {
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2
  },
  "tiers": [
    {"triggerPct": 5,  "lockPct": 50, "breachesRequired": 2},
    {"triggerPct": 10, "lockPct": 65, "breachesRequired": 2},
    {"triggerPct": 15, "lockPct": 75, "breachesRequired": 2},
    {"triggerPct": 20, "lockPct": 85, "breachesRequired": 1}
  ]
}
```

> Note: `phase1.retraceThreshold: 0.10` = 10% ROE absolute floor. Combined with `dsl-cli.py` auto-calc: `absoluteFloor = entry × (1 - 0.10/leverage)` — matches the WOLF SKILL.md formula exactly (`entry × (1 - 10%/leverage)`).

#### `dsl-tight/dsl-profile.json`

DSL-Tight uses 4 tiers with earlier triggers (starts at 10% ROE, not 5%) and per-tier breach counts (3→2→2→1). Wider phase 1 retrace (5% ROE) than wolf.

```json
{
  "phase1": {
    "retraceThreshold": 0.05,
    "consecutiveBreachesRequired": 3
  },
  "phase2TriggerTier": 0,
  "phase2": {
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2
  },
  "tiers": [
    {"triggerPct": 10, "lockPct": 50, "retrace": 0.015, "breachesRequired": 3},
    {"triggerPct": 20, "lockPct": 65, "retrace": 0.012, "breachesRequired": 2},
    {"triggerPct": 40, "lockPct": 75, "retrace": 0.010, "breachesRequired": 2},
    {"triggerPct": 75, "lockPct": 85, "retrace": 0.006, "breachesRequired": 1}
  ]
}
```

---

### Config Comparison Across Skills

| Dimension | default DSL | wolf-strategy | dsl-tight |
|-----------|-------------|---------------|-----------|
| Tier count | 6 | 4 | 4 |
| First trigger ROE | 10% | 5% | 10% |
| Phase 1 retrace | 3% ROE | 10% ROE (wide absolute floor) | 5% ROE |
| Phase 2 global breaches | 1 | 2 | 2 |
| Per-tier breach tightening | No | 2/2/2/1 | 3/2/2/1 |
| Per-tier retrace tightening | Yes (tiers 3-6) | No | Yes (all tiers) |

---

## Inter-Skill Integration Pattern

Other skills invoke DSL by calling `dsl-cli.py` directly, passing their own `dsl-profile.json` via `--configuration @path`. Both the script and the profile file are resolved on disk at runtime.

### Step-by-step for a calling skill

1. **Locate `dsl-cli.py`** — search workspace/skills directory for `dsl-dynamic-stop-loss/scripts/dsl-cli.py` (same pattern as resolving `dsl-v5.py` in dsl-tight today).
2. **Locate own `dsl-profile.json`** — it lives in the calling skill's own directory (e.g. `wolf-strategy/dsl-profile.json`). Resolve the absolute path at runtime.
3. **Call `dsl-cli.py`** with `--configuration @<absolute-path-to-dsl-profile.json>`.
4. **Pass `--skill <own-name>`** for audit trail in the strategy config.
5. **Read stdout JSON** — check `status: "ok"` and handle `cron_needed` if present.
6. **Do not manage cron inside the skill** — CLI generates and stores the cron job ID when needed; emit intent to the agent; agent creates the **OpenClaw** cron using that ID.

### wolf-strategy example (single position, after open-position.py runs)

```bash
DSL_CLI=/path/to/dsl-dynamic-stop-loss/scripts/dsl-cli.py
WOLF_PROFILE=/path/to/wolf-strategy/dsl-profile.json

# CLI fetches direction, leverage, size, entryPrice from clearinghouse automatically.
python3 $DSL_CLI add-dsl abc-strategy-123 ETH main \
  --skill wolf-strategy \
  --configuration @$WOLF_PROFILE
```

If the position was filled across multiple orders and the calling skill has a more precise average price:

```bash
python3 $DSL_CLI add-dsl abc-strategy-123 ETH main \
  --skill wolf-strategy \
  --entry-price 3012.45 \
  --configuration @$WOLF_PROFILE
```

### wolf-strategy example (all positions in a strategy — no asset arg)

```bash
# CLI resolves wallet and all positions from MCP automatically.
# wolf's dsl-profile.json applied as defaultConfig and to all position files.
python3 $DSL_CLI add-dsl abc-strategy-123 \
  --skill wolf-strategy \
  --configuration @$WOLF_PROFILE
```

### dsl-tight example

```bash
DSL_CLI=/path/to/dsl-dynamic-stop-loss/scripts/dsl-cli.py
TIGHT_PROFILE=/path/to/dsl-tight/dsl-profile.json

python3 $DSL_CLI add-dsl abc-strategy-123 \
  --skill dsl-tight \
  --configuration @$TIGHT_PROFILE
```

### Partial override on top of a profile

A calling skill can combine a profile file with an inline JSON override. The second `--configuration` is merged in at field level, overriding matching keys from the profile:

```bash
# Use wolf profile but tighten phase1 retrace to 5% ROE for this strategy
python3 $DSL_CLI add-dsl abc-strategy-123 ETH main \
  --skill wolf-strategy \
  --configuration @$WOLF_PROFILE \
  --configuration '{"phase1": {"retraceThreshold": 0.05}}'
```

`tiers` from the profile are preserved unless the inline override also includes `tiers`.

---

## Paused State — Impact on `dsl-v5.py`

`dsl-v5.py` already skips position files with `active: false`. Pause sets `active: false` on position files, so the cron naturally skips paused positions with no changes to `dsl-v5.py`.

On resume, `active` is set back to `true`. The cron picks it up on the next tick.

> This means `dsl-v5.py` needs **no changes** for pause/resume to work. The behavior is already correct.

---

## State File Changes

The only change to existing per-position state files is one new optional field:

| Field | Type | Description |
|-------|------|-------------|
| `pausedAt` | string \| null | ISO timestamp when position was paused. Set by `pause-dsl`, removed by `resume-dsl`. |

All other fields remain unchanged. `dsl-v5.py` is unaffected by this field.

---

## Cron Management

**All DSL crons are OpenClaw crons.** This skill handles the full cron lifecycle: the CLI generates and stores the cron job ID when needed, and outputs when to create or remove a cron; the agent performs the create/remove **in OpenClaw** using the ID from the CLI. MCP (Senpi) is used for strategy/clearinghouse/prices/close; **OpenClaw is the scheduler**.

- **Create cron**: When add-dsl needs a cron, the CLI generates a cron job ID, stores it in the strategy config, and outputs `cron_needed`, `cron_job_id`, `cron_env`, `cron_schedule`. Agent creates the OpenClaw cron with that ID and the output env/schedule.
- **Remove cron**: When CLI outputs `cron_to_remove`, agent removes that job in OpenClaw using the stored `cronJobId`.

The CLI does not call OpenClaw directly; it emits cron intent JSON and owns generation of the cron job ID.

### Revised Cron Flow for `add-dsl`

1. Agent calls `dsl-cli.py add-dsl ...` → CLI creates state files; when a cron is needed it generates and stores a cron job ID and outputs `{"cron_needed": true, "cron_job_id": "...", "cron_env": {...}, "cron_schedule": "*/3 * * * *"}`.
2. Agent creates the **OpenClaw** cron with that `cron_job_id` and the output env/schedule.

Similarly for delete:
1. `dsl-cli.py delete-dsl ...` archives state files, outputs `{"cron_to_remove": {"cron_job_id": "cron-job-id"}}`.
2. Agent removes that cron in OpenClaw using `cron_to_remove.cron_job_id`.

---

## Implementation Plan

### Phase 1 — Core CLI + State Files

**Files to create in `dsl-dynamic-stop-loss`:**
- `scripts/dsl-cli.py` — main CLI entry point with argparse subcommands

**Subcommands in scope for Phase 1:**
- `add-dsl` — create strategy config (`strategy-<id>.json`), per-position state files; when cron is needed, generate and store cron job ID, output cron intent (`cron_job_id`, `cron_env`, `cron_schedule`)
- `status-dsl` — read and report strategy + position status
- `count-dsl` — aggregate counts from state files

**Reference files to create:**
- `references/strategy-schema.md` — documents strategy config schema
- `references/cli-usage.md` — usage guide and inter-skill integration examples

### Phase 2 — Update, Pause, Resume, Delete

**Subcommands in scope:**
- `update-dsl` — patch config in strategy config AND all active position files; auto-recalc `absoluteFloor` when retrace changes
- `pause-dsl` — set `active: false` on position files, update strategy status, set `pausedAt`
- `resume-dsl` — set `active: true`, clear `pausedAt`
- `delete-dsl` — archive state files, output cron removal intent

**Changes to `dsl-v5.py`:**
- None required — pause/resume works via existing `active` field.

### Phase 3 — Skill Config Profiles + SKILL.md Updates

**Files to create in dependent skills:**
- `wolf-strategy/dsl-profile.json` — wolf's 4-tier config (as defined in Config Comparison above)
- `dsl-tight/dsl-profile.json` — tight's 4-tier config with per-tier breach counts

**SKILL.md updates:**

`dsl-dynamic-stop-loss/SKILL.md`:
- Document CLI commands as the preferred setup path.
- Keep manual state file creation as a fallback for advanced/edge cases.

`dsl-tight/SKILL.md`:
- Replace current manual state file setup with `dsl-cli.py add-dsl ... --configuration @dsl-profile.json`.
- Document how to locate both `dsl-cli.py` and `dsl-tight/dsl-profile.json` at runtime.

`wolf-strategy/SKILL.md`:
- Document that `open-position.py` calls `dsl-cli.py` with `@wolf-strategy/dsl-profile.json` after opening a position.
- Document `update-dsl`, `pause-dsl`, `delete-dsl` as the lifecycle management commands replacing direct state file manipulation.

---

## Resolved Design Decisions

All open questions are closed.

1. **Cron management**: CLI generates and stores the cron job ID when needed and emits cron intent JSON. Agent creates/removes the **OpenClaw** cron using that ID. Keeps `dsl-cli.py` dependency-free from OpenClaw.

2. **`add-dsl` without `asset`**: CLI calls mcporter directly — `strategy_get(strategy_id)` → wallet, then `strategy_get_clearinghouse_state(wallet)` → positions (size, entryPrice, direction, leverage all come from clearinghouse). No agent involvement needed for position discovery.

3. **Retroactive config updates**: `update-dsl strategy-id --configuration` patches both the strategy config's defaultConfig AND all existing active position state files. Config fields overwritten; runtime state preserved.

4. **`dsl-tight` integration**: Configuration-based. `dsl-tight` documents calling `dsl-cli.py` directly with `--configuration @dsl-profile.json`. No wrapper script.

5. **Absolute floor auto-calculation**: `dsl-cli.py` always auto-calculates `absoluteFloor` from all three inputs — `entryPrice`, `leverage`, and `phase1.retraceThreshold` — using the ROE formula (LONG: `entry × (1 - retrace/leverage)`, SHORT: `entry × (1 + retrace/leverage)`). An explicitly provided `absoluteFloor` in `--configuration` overrides the calculation. This applies on `add-dsl` (initial creation) and on `update-dsl` when `retraceThreshold` changes.
