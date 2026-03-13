# State File Schemas — Fox v0.1

> Per Senpi Skill Guide §4: all state files include `version`, `active`, `instanceKey`/`strategyKey`, `createdAt`, `updatedAt`.

## Strategy Registry (`fox-strategies.json`)

```json
{
  "version": 1,
  "defaultStrategy": "fox-abc12345",
  "strategies": {
    "fox-abc12345": {
      "name": "<user-provided>",
      "wallet": "<from strategy_create_custom_strategy>",
      "strategyId": "<UUID>",
      "xyzWallet": "",
      "xyzStrategyId": "",
      "embeddedWallet": "<from strategy response>",
      "senpiUserId": "<from auth>",
      "budget": "<user-provided>",
      "slots": "<calculated from budget>",
      "marginPerSlot": "<calculated: budget × 0.30>",
      "defaultLeverage": "<calculated from budget: 5/7/10>",
      "dailyLossLimit": "<calculated: budget × 0.15>",
      "autoDeleverThreshold": "<calculated: budget × 0.80>",
      "dsl": {
        "preset": "aggressive-v2",
        "tiers": [
          {"triggerPct": 5, "lockPct": 2, "breaches": 2},
          {"triggerPct": 10, "lockPct": 5, "breaches": 2},
          {"triggerPct": 20, "lockPct": 14, "breaches": 2},
          {"triggerPct": 30, "lockPct": 24, "breaches": 2},
          {"triggerPct": 40, "lockPct": 34, "breaches": 1},
          {"triggerPct": 50, "lockPct": 44, "breaches": 1},
          {"triggerPct": 65, "lockPct": 56, "breaches": 1},
          {"triggerPct": 80, "lockPct": 72, "breaches": 1},
          {"triggerPct": 100, "lockPct": 90, "breaches": 1}
        ]
      },
      "enabled": true
    }
  },
  "global": {
    "telegramChatId": "<from user config>",
    "workspace": "/data/workspace",
    "notifications": {
      "provider": "telegram",
      "alertDedupeMinutes": 15
    }
  }
}
```

## Trade Counter (`fox-trade-counter.json`)

```json
{
  "date": "<YYYY-MM-DD or null>",
  "accountValueStart": "<budget, set at setup>",
  "entries": 0,
  "cumulativeFees": 0,
  "cumulativeGrossPnl": 0,
  "cumulativeNetPnl": 0,
  "fdrPct": "0.00",
  "gate": "OPEN",
  "lastThreeResults": [],
  "trades": [],
  "cooldownUntil": null,
  "maxEntriesPerDay": 6,
  "marginTiers": [
    {"entries": [1, 2], "marginPct": 0.48, "budgetPct": 0.50, "margin": "<calculated>", "budget": "<calculated>"},
    {"entries": [3, 4], "marginPct": 0.32, "budgetPct": 0.33, "margin": "<calculated>", "budget": "<calculated>"},
    {"entries": [5, 6], "marginPct": 0.15, "budgetPct": 0.17, "margin": "<calculated>", "budget": "<calculated>"}
  ],
  "note": "margin/budget fields calculated from user's budget at setup. marginPct/budgetPct are the percentage of budget."
}
```

## DSL v5.3.1 State File (`dsl/{strategyId}/{ASSET}.json`)

Created when a position opens. See DSL v5.3.1 skill for full details.

```json
{
  "version": 3,
  "active": true,
  "asset": "HYPE",
  "direction": "LONG",
  "leverage": 10,
  "entryPrice": 28.87,
  "size": 1890.28,
  "wallet": "<strategy_wallet>",
  "strategyId": "<strategy_uuid>",
  "strategyKey": "fox-abc12345",
  "phase": 1,
  "phase1": {
    "retraceThreshold": 0.02,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": "<calculated: entry × (1 - 0.02/leverage) for LONG>",
    "hardTimeoutMin": 30,
    "weakPeakCutMin": 15,
    "deadWeightCutMin": 10,
    "greenIn10TightenPct": 50
  },
  "greenIn10": false,
  "score": 8,
  "isReentry": false,
  "reentryOf": null,
  "phase2TriggerTier": 0,
  "phase2": {
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2
  },
  "tiers": [
    {"triggerPct": 5, "lockPct": 2},
    {"triggerPct": 10, "lockPct": 5},
    {"triggerPct": 20, "lockPct": 14},
    {"triggerPct": 30, "lockPct": 24},
    {"triggerPct": 40, "lockPct": 34},
    {"triggerPct": 50, "lockPct": 44},
    {"triggerPct": 65, "lockPct": 56},
    {"triggerPct": 80, "lockPct": 72},
    {"triggerPct": 100, "lockPct": 90}
  ],
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "highWaterPrice": 28.87,
  "floorPrice": "<absoluteFloor>",
  "currentBreachCount": 0,
  "createdAt": "2026-02-20T15:22:00.000Z",
  "updatedAt": "2026-02-20T15:22:00.000Z",
  "lastCheck": "2026-02-20T15:22:00.000Z",
  "createdBy": "entry_flow"
}
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | Schema version for migration |
| `active` | bool | Whether monitoring is active |
| `asset` | string | Coin symbol (e.g. "HYPE") |
| `direction` | string | "LONG" or "SHORT" |
| `leverage` | number | Position leverage |
| `entryPrice` | number | Entry price |
| `size` | number | Position size |
| `wallet` | string | Strategy wallet address |
| `strategyId` | string | Strategy UUID |
| `strategyKey` | string | Registry key (e.g. "fox-abc12345") |
| `phase` | int | 1 = pre-tier, 2 = trailing |
| `phase1` | object | Phase 1 config (retrace, breaches, floor) |
| `tiers` | array | Tier definitions [{triggerPct, lockPct}] |
| `currentTierIndex` | int | Current tier (-1 = none) |
| `highWaterPrice` | number | Best price seen (highest for LONG, lowest for SHORT) |
| `floorPrice` | number | Current effective floor |
| `currentBreachCount` | int | Consecutive breaches |
| `score` | int\|null | v7.2: entry score — determines conviction-scaled Phase 1 tolerance |
| `isReentry` | bool | v7.2: true if this is a re-entry after a Phase 1 exit |
| `reentryOf` | string\|null | v7.2: trade ID of original entry (for re-entry tracking) |
| `greenIn10` | bool | v0.1: whether position was green within first 10min |
| `phase1.hardTimeoutMin` | int | v0.1: max minutes in Phase 1 before forced close (default: 30) |
| `phase1.weakPeakCutMin` | int | v0.1: close if peak ROE < 3% after this many min (default: 15) |
| `phase1.deadWeightCutMin` | int | v0.1: close if never positive ROE after this many min (default: 10) |
| `phase1.greenIn10TightenPct` | int | v0.1: tighten floor to N% of original distance if not green in 10min (default: 50) |
| `createdAt` | string | ISO 8601 creation time |
| `updatedAt` | string | ISO 8601 last modification |

### Filename Convention
- Main dex: `{ASSET}.json` (e.g. `HYPE.json`)
- XYZ dex: `xyz--SYMBOL.json` (colon → double-dash, e.g. `xyz--SILVER.json`)

### Percentage Convention
All percentage fields use **whole numbers**: `triggerPct: 5` means 5%, `lockPct: 2` means 2%. Code divides by 100 internally. Never use decimals (0.05).

Exception: `retraceThreshold` in phase1/phase2 uses decimals (0.02 = 2% ROE in v0.1, was 0.03 in v7) for backward compatibility with DSL v5.3.1.
