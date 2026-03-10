# State File Schema

Complete JSON schema for DSL v4/v5 state files. One state file per position. v5 adds path conventions; the JSON schema is unchanged.

## Full Example

```json
{
  "active": true,
  "asset": "HYPE",
  "direction": "LONG",
  "leverage": 10,
  "entryPrice": 28.87,
  "size": 1890.28,
  "wallet": "0xYourStrategyWalletAddress",
  "strategyId": "uuid-of-strategy",
  "phase": 1,
  "phase1": {
    "enabled": true,
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": 28.00
  },
  "phase2TriggerTier": 1,
  "phase2": {
    "enabled": true,
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2
  },
  "tiers": [
    {"triggerPct": 10, "lockPct": 5},
    {"triggerPct": 20, "lockPct": 14},
    {"triggerPct": 30, "lockPct": 22, "retrace": 0.012},
    {"triggerPct": 50, "lockPct": 40, "retrace": 0.010},
    {"triggerPct": 75, "lockPct": 60, "retrace": 0.008},
    {"triggerPct": 100, "lockPct": 80, "retrace": 0.006}
  ],
  "closeRetries": 2,
  "closeRetryDelaySec": 3,
  "maxFetchFailures": 10,
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "highWaterPrice": 28.87,
  "floorPrice": 28.00,
  "currentBreachCount": 0,
  "consecutiveFetchFailures": 0,
  "pendingClose": false,
  "lastCheck": null,
  "lastPrice": null,
  "createdAt": "2026-02-20T15:22:00.000Z"
}
```

## Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `active` | bool | Must be `true` to monitor. Script sets to `false` on close. |
| `asset` | string | Ticker symbol (e.g., "HYPE", "BTC", "ETH"). For xyz dex use prefix: "xyz:SILVER", "xyz:AAPL". |
| `direction` | string | `"LONG"` or `"SHORT"` — controls all math |
| `leverage` | number | Position leverage (used for ROE calculation) |
| `entryPrice` | number | Average entry price |
| `size` | number | Position size in units |
| `wallet` | string | Strategy wallet address — required for auto-close |
| `strategyId` | string | Strategy UUID |
| `phase` | int | Current phase: `1` or `2` |
| `phase1` | object | Phase 1 configuration (see below) |
| `phase2TriggerTier` | int | Tier index that triggers Phase 2 transition (default: 0 = first tier) |
| `phase2` | object | Phase 2 configuration (see below) |
| `tiers` | array | Tier definitions (see below) |
| `currentTierIndex` | int | Current tier (-1 = no tier hit yet) |
| `highWaterPrice` | number | Initialize to entry price |
| `currentBreachCount` | int | Initialize to 0 |
| `createdAt` | string | ISO 8601 timestamp. **Required.** Set when the position state is created; used to compute elapsed time for Phase 1 time-based auto-cut. The cron runner backfills this from `lastCheck` if missing (e.g. legacy state). |

**Phase and runtime:** The cron runner updates `phase` (1 or 2) and other runtime fields every run and persists them to the state file so the current DSL phase is always stored for easy processing.

### phase1 Object (capital protection — avoid losing original money)

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | If true, Phase 1 is active. At least one of phase1.enabled or phase2.enabled must be true. |
| `retraceThreshold` | float | Retrace from HW in **ROE** terms (e.g. 0.03 = 3% ROE). Script converts to price via ÷ leverage. |
| `consecutiveBreachesRequired` | int | Checks below floor before close |
| `absoluteFloor` | number | Hard price floor — max loss cap |
| `hardTimeout` | object | Optional. Phase 1 **hard timeout**. `{ "enabled": bool, "intervalInMinutes": number }`. When enabled and elapsed ≥ intervalInMinutes, close. **If not present, treated as disabled** (no default object is written). intervalInMinutes must be ≥ cronIntervalMinutes when enabled. |
| `weakPeakCut` | object | Optional. **Weak peak early cut.** `{ "enabled": bool, "intervalInMinutes": number, "minValue": number }`. When enabled, after intervalInMinutes close if peak ROE &lt; minValue (ROE %) and current ROE &lt; peak ROE. **If not present, treated as disabled.** |
| `deadWeightCut` | object | Optional. **Dead weight cut:** if ROE was never positive for the whole life of the position and elapsed ≥ intervalInMinutes, close. `{ "enabled": bool, "intervalInMinutes": number }`. **If not present, treated as disabled.** intervalInMinutes must be ≥ cronIntervalMinutes when enabled and &gt; 0. |

### phase2 Object (tier-based only)

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | If true, Phase 2 (tiers) is active. Tiers are used only in Phase 2. |
| `retraceThreshold` | float | Default retrace (ROE fraction) for tiers without per-tier override |
| `consecutiveBreachesRequired` | int | Consecutive breach checks before close (default: 1 in Phase 2) |

### Tier Objects

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `triggerPct` | float | Yes | ROE % that activates this tier |
| `lockPct` | float | Yes | % of (entry→high water) range to lock as floor (e.g. 50 = lock half the gain) |
| `retrace` | float | No | Per-tier retrace override in ROE terms (uses `phase2.retraceThreshold` if omitted) |

## v4 Optional Fields (all have defaults)

| Field | Default | Description |
|-------|---------|-------------|
| `cronIntervalMinutes` | `3` | **General:** How frequently the DSL cron runs (in minutes). Not part of phase1 — it defines the run interval for the whole strategy. Skills can set this (e.g. 3 or 5); the CLI emits cron intent so the agent creates the OpenClaw cron with that schedule. **When this is changed**, the agent must remove the old cron and create a new one with the new interval. Also used as the **minimum** for `phase1MaxMinutes` and `weakPeakCutMinutes` (they cannot be less than the cron interval). |
| `closeRetries` | `2` | Close attempts before setting `pendingClose` |
| `closeRetryDelaySec` | `3` | Seconds between retry attempts |
| `maxFetchFailures` | `10` | Auto-deactivate after this many consecutive fetch failures |
| `pendingClose` | `false` | Set by script when close fails — next tick retries |
| `consecutiveFetchFailures` | `0` | Tracked by script — resets on successful fetch |
| `tierFloorPrice` | `null` | Current tier's locked price floor |
| `floorPrice` | (calculated) | Effective floor — set by script each run |
| `lastCheck` | `null` | Last check timestamp — set by script |
| `lastPrice` | `null` | Last fetched price — set by script |

## v5 Hyperliquid SL Fields (optional)

**Who sets these:** The **cron runner** (`dsl-v5.py`) syncs the floor to Hyperliquid via Senpi `edit_position` and populates/updates these fields. The **CLI only creates the state file** — it does not place the SL order. If a position was paused right after creation, the cron skips it until it is resumed; the next cron run (~within 3 min after resume) will pick it up, sync the floor to Hyperliquid, and set `slOrderId`.

When the script syncs the dynamic stop loss to Hyperliquid via Senpi `edit_position`, it stores and updates these fields:

| Field | Type | Description |
|-------|------|-------------|
| `slOrderId` | number \| null | Hyperliquid order ID of the current SL for this position. Set by the cron runner after a successful `edit_position` (or resolved via `strategy_get_open_orders`). |
| `lastSyncedFloorPrice` | number \| null | The effective floor price at which the current SL was last placed on Hyperliquid. Used to detect when the floor has changed and a new SL must be set. |
| `slOrderIdUpdatedAt` | string \| null | ISO 8601 timestamp when the SL was last synced (for debugging/audit). |
| `lastSlSyncError` | string \| null | Set by script when `edit_position` or SL order resolution fails; next sync will retry. |

Path and file naming stay the same; these fields are optional and backfilled by the script when syncing.

## v5 Path Conventions

State files are stored under a directory per strategy. Required env vars:

| Env var | Description |
|---------|-------------|
| `DSL_STATE_DIR` | Base directory (default: `/data/workspace/dsl`) |
| `DSL_STRATEGY_ID` | Strategy UUID (required) |
| `DSL_ASSET` | Asset symbol (required) |

**Path:** `{DSL_STATE_DIR}/{DSL_STRATEGY_ID}/{asset_filename}.json`

**Asset → filename:** xyz dex: `xyz:SILVER` → `xyz--SILVER.json` (colon → double-dash). Main dex: `ETH` → `ETH.json`.

**Example:** `DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 DSL_ASSET=xyz:SILVER` → `/data/workspace/dsl/strat-abc-123/xyz--SILVER.json`
