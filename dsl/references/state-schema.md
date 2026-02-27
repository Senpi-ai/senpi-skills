# State File Schema

Complete JSON schema for DSL v4 state files. One state file per position.

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
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": 28.00
  },
  "phase2TriggerTier": 1,
  "phase2": {
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
  "breachDecay": "hard",
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
| `asset` | string | Ticker symbol (e.g., "HYPE", "BTC", "ETH") |
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
| `createdAt` | string | ISO 8601 timestamp |

### phase1 Object

| Field | Type | Description |
|-------|------|-------------|
| `retraceThreshold` | float | Retrace from HW (e.g., 0.03 = 3%) |
| `consecutiveBreachesRequired` | int | Checks below floor before close |
| `absoluteFloor` | number | Hard price floor — max loss cap |

### phase2 Object

| Field | Type | Description |
|-------|------|-------------|
| `retraceThreshold` | float | Default retrace for tiers without per-tier override |
| `consecutiveBreachesRequired` | int | Checks below floor before close |

### Tier Objects

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `triggerPct` | float | Yes | ROE % that activates this tier |
| `lockPct` | float | Yes | ROE % to lock as floor |
| `retrace` | float | No | Per-tier retrace override (uses `phase2.retraceThreshold` if omitted) |

## v4 Optional Fields (all have defaults)

| Field | Default | Description |
|-------|---------|-------------|
| `breachDecay` | `"hard"` | `"hard"` resets breach count to 0 on recovery; `"soft"` decays by 1 |
| `closeRetries` | `2` | Close attempts before setting `pendingClose` |
| `closeRetryDelaySec` | `3` | Seconds between retry attempts |
| `maxFetchFailures` | `10` | Auto-deactivate after this many consecutive fetch failures |
| `pendingClose` | `false` | Set by script when close fails — next tick retries |
| `consecutiveFetchFailures` | `0` | Tracked by script — resets on successful fetch |
| `tierFloorPrice` | `null` | Current tier's locked price floor |
| `floorPrice` | (calculated) | Effective floor — set by script each run |
| `lastCheck` | `null` | Last check timestamp — set by script |
| `lastPrice` | `null` | Last fetched price — set by script |
