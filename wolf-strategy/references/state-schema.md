# WOLF v6 State & Config Schemas

## Strategy Registry (`wolf-strategies.json`)

The central config file. Holds multiple strategies, each with independent wallets, budgets, slots, DSL config, and leverage. Created by `wolf-setup.py`.

```json
{
  "version": 1,
  "defaultStrategy": "wolf-abc12345",
  "strategies": {
    "wolf-abc12345": {
      "name": "Aggressive Momentum",
      "wallet": "0xaaa...",
      "strategyId": "abc12345-...",
      "xyzWallet": "0xbbb...",
      "xyzStrategyId": "def67890-...",
      "budget": 6500,
      "slots": 3,
      "marginPerSlot": 1950,
      "defaultLeverage": 10,
      "dailyLossLimit": 975,
      "autoDeleverThreshold": 5200,
      "dsl": {
        "preset": "aggressive",
        "tiers": [
          { "triggerPct": 5, "lockPct": 50, "breaches": 3 },
          { "triggerPct": 10, "lockPct": 65, "breaches": 2 },
          { "triggerPct": 15, "lockPct": 75, "breaches": 2 },
          { "triggerPct": 20, "lockPct": 85, "breaches": 1 }
        ]
      },
      "enabled": true
    },
    "wolf-xyz78901": {
      "name": "Conservative XYZ",
      "wallet": "0xccc...",
      "strategyId": "xyz78901-...",
      "xyzWallet": null,
      "xyzStrategyId": null,
      "budget": 2000,
      "slots": 2,
      "marginPerSlot": 600,
      "defaultLeverage": 7,
      "dailyLossLimit": 300,
      "autoDeleverThreshold": 1600,
      "dsl": {
        "preset": "conservative",
        "tiers": [
          { "triggerPct": 3, "lockPct": 60, "breaches": 4 },
          { "triggerPct": 7, "lockPct": 75, "breaches": 3 },
          { "triggerPct": 12, "lockPct": 85, "breaches": 2 },
          { "triggerPct": 18, "lockPct": 90, "breaches": 1 }
        ]
      },
      "enabled": true
    }
  },
  "global": {
    "telegramChatId": "12345",
    "workspace": "/data/workspace",
    "notifications": {
      "provider": "telegram",
      "alertDedupeMinutes": 15
    }
  }
}
```

### Strategy Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Human-readable name |
| `wallet` | string | Strategy wallet address (0x...) |
| `strategyId` | string | Strategy UUID |
| `xyzWallet` | string\|null | XYZ DEX wallet (optional, can be same as wallet) |
| `xyzStrategyId` | string\|null | XYZ strategy UUID (optional) |
| `budget` | number | Total trading budget in USD |
| `slots` | number | Max concurrent positions for this strategy |
| `marginPerSlot` | number | USD margin per slot (budget * 0.30) |
| `defaultLeverage` | number | Default leverage for new positions |
| `dailyLossLimit` | number | Max daily loss before reducing exposure |
| `autoDeleverThreshold` | number | Account value below which to reduce slots by 1 |
| `dsl.preset` | string | "aggressive" or "conservative" |
| `dsl.tiers` | array | 4-tier DSL config |
| `enabled` | boolean | false pauses strategy without deleting config |

### Key Design Decisions

- **Strategy key format:** `wolf-{first 8 chars of strategyId}`
- **`defaultStrategy`:** Used when scripts called without `--strategy` flag or `WOLF_STRATEGY` env var
- **Global settings** (telegram, workspace) shared across all strategies
- **`enabled: false`** pauses a strategy without deleting config — all scripts skip disabled strategies

---

## Directory Structure

```
{workspace}/
├── wolf-strategies.json              # Strategy registry
├── max-leverage.json                 # Shared across strategies
├── state/
│   ├── wolf-abc12345/                # Strategy A state dir
│   │   ├── dsl-HYPE.json
│   │   ├── dsl-SOL.json
│   │   └── watchdog-last.json
│   └── wolf-xyz78901/                # Strategy B state dir
│       ├── dsl-HYPE.json             # Same asset, different strategy = OK
│       └── watchdog-last.json
├── history/
│   ├── emerging-movers.json          # Shared (market data)
│   ├── scan-history.json             # Shared (scanner cross-scan momentum)
│   └── scanner-config.json           # Shared (scanner thresholds)
├── memory/
│   └── MEMORY.md
└── logs/
    └── wolf-2026-02-24.log
```

**Why `state/` is per-strategy:** DSL state files must be scoped to prevent collision when the same asset is traded in multiple strategies simultaneously.

**Why `history/` is shared:** Emerging movers and scanner detect market-wide signals. The signal is the same regardless of which strategy acts on it.

---

## DSL State File (`state/{strategyKey}/dsl-{ASSET}.json`)

Created per position, scoped to its strategy. Read by `dsl-combined.py`.

```json
{
  "version": 2,
  "strategyKey": "wolf-abc12345",
  "active": true,
  "asset": "HYPE",
  "direction": "LONG",
  "entryPrice": 28.87,
  "leverage": 10,
  "size": 1890.28,
  "wallet": "0xaaa...",
  "strategyId": "abc12345-...",
  "dex": null,
  "highWaterPrice": 29.50,
  "phase": 1,
  "currentTierIndex": 0,
  "tierFloorPrice": null,
  "currentBreachCount": 0,
  "floorPrice": 29.353,
  "createdAt": "2026-02-24T10:00:00Z",
  "hwTimestamp": "2026-02-24T10:05:00Z",
  "lastCheck": "2026-02-24T10:06:00Z",
  "lastPrice": 29.45,
  "consecutiveFetchFailures": 0,
  "pendingClose": false,
  "phase1": {
    "retraceThreshold": 5,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": 28.726
  },
  "phase2": {
    "retraceFromHW": 5,
    "breachesRequired": 2
  },
  "tiers": [
    { "triggerPct": 5, "lockPct": 50, "breaches": 3 },
    { "triggerPct": 10, "lockPct": 65, "breaches": 2 },
    { "triggerPct": 15, "lockPct": 75, "breaches": 2 },
    { "triggerPct": 20, "lockPct": 85, "breaches": 1 }
  ],
  "stagnation": {
    "enabled": true,
    "thresholdHours": 1.0,
    "minROE": 8.0,
    "priceRangePct": 1.0
  }
}
```

### v6 Changes to DSL State

| Field | Change |
|---|---|
| `version` | New field. Set to `2` for v6 format. |
| `strategyKey` | **New.** Links back to the strategy in the registry. |
| `strategyId` | **New optional.** Copy of strategy UUID for redundancy. |
| File location | **Changed.** `state/{strategyKey}/dsl-{ASSET}.json` instead of `dsl-state-WOLF-{ASSET}.json` |

### Required Fields

| Field | Type | Description |
|---|---|---|
| `active` | boolean | `true` = DSL is running. Set to `false` on close. |
| `asset` | string | Asset name (e.g. "HYPE", "PAXG"). No `xyz:` prefix. |
| `direction` | string | "LONG" or "SHORT" |
| `entryPrice` | number | Position entry price |
| `leverage` | number | Position leverage |
| `size` | number | Absolute position size (units) |
| `wallet` | string | Strategy wallet address |
| `dex` | string\|null | "xyz" for XYZ assets, null for crypto |
| `highWaterPrice` | number | Best price seen (highest for LONG, lowest for SHORT) |
| `phase` | number | 1 = pre-tier, 2 = tier-based trailing |
| `currentTierIndex` | number | Current tier (0-3), or 0 if none |
| `tierFloorPrice` | number\|null | Locked tier floor price |
| `currentBreachCount` | number | Consecutive floor breaches |
| `floorPrice` | number | Effective floor (auto-calculated) |
| `tiers` | array | 4-tier config array |

### Optional Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `version` | number | 2 | State file schema version |
| `strategyKey` | string | — | Strategy key for back-reference |
| `createdAt` | string | — | ISO timestamp for elapsed time calc |
| `hwTimestamp` | string | — | When HW was last updated (for stagnation) |
| `pendingClose` | boolean | false | Retry close on next run |
| `consecutiveFetchFailures` | number | 0 | Auto-deactivate at 10 |
| `breachDecay` | string | "hard" | "hard" = reset to 0, "soft" = decay by 1 |
| `maxFetchFailures` | number | 10 | Failures before auto-deactivate |
| `stagnation` | object | enabled | Stagnation take-profit config |

---

## Scanner Config (`history/scanner-config.json`)

Optional file to override opportunity scanner defaults.

```json
{
  "topNDeep": 15,
  "minVolume24h": 500000,
  "maxWorkers": 8,
  "pillarWeights": {
    "smartMoney": 0.25,
    "marketStructure": 0.25,
    "technicals": 0.25,
    "funding": 0.25
  },
  "macroModifiers": {
    "strong_downLong": -30,
    "strong_downShort": 15,
    "downLong": -15,
    "downShort": 8,
    "neutralLong": 0,
    "neutralShort": 0,
    "upLong": 8,
    "upShort": -15,
    "strong_upLong": 15,
    "strong_upShort": -30
  },
  "disqualifyThresholds": {
    "counterTrendStrength": 50,
    "extremeRsiLow": 20,
    "extremeRsiHigh": 80,
    "volumeDeadThreshold": 0.5,
    "heavyFundingAnnualized": 50,
    "btcHeadwindPoints": 30
  }
}
```

---

## Shared Config Loader (`scripts/wolf_config.py`)

All scripts import from `wolf_config.py`:

```python
from wolf_config import load_strategy, load_all_strategies, dsl_state_path

cfg = load_strategy("wolf-abc12345")   # Specific strategy
cfg = load_strategy()                  # Default strategy
strategies = load_all_strategies()     # All enabled strategies
path = dsl_state_path("wolf-abc12345", "HYPE")
```

**Legacy auto-migration:** If `wolf-strategies.json` doesn't exist but `wolf-strategy.json` does, `wolf_config.py` automatically wraps the legacy config into a registry with one strategy. Old `dsl-state-WOLF-*.json` files are migrated to `state/{key}/dsl-*.json`.

---

## Key Gotchas

1. **`triggerPct` not `threshold`** — Tiers use `triggerPct: 5` (percentage), NOT `threshold: 0.05` (decimal).

2. **`lockPct` not `retracePct`** — Tiers use `lockPct: 50` (lock 50% of HW profit), NOT `retracePct`.

3. **`active` is boolean** — Use `"active": true`, NOT `"status": "active"`.

4. **`absoluteFloor` is auto-calculated** — The DSL script recalculates it from entryPrice, retraceThreshold, and leverage on every run.

5. **XYZ assets** — Set `dex: "xyz"` in state file. Use `coin=xyz:ASSET` when closing. Use `leverageType: "ISOLATED"` when opening.

6. **4 tiers, not 6** — 5/10/15/20% ROE with 50/65/75/85% locks.

7. **`strategyKey` in state files** — v6 state files include `strategyKey` to link back to the registry. Scripts use it to load the correct strategy config.

8. **State dir per strategy** — `state/wolf-abc12345/dsl-HYPE.json`, NOT `dsl-state-WOLF-HYPE.json`. Same asset in two strategies = two files in different dirs, no collision.

9. **Atomic writes** — All state file updates use `atomic_write()` (write to .tmp, then `os.replace`) to prevent corruption from concurrent access.

10. **`phase2.retraceFromHW` is a percentage** — Use `5` for 5%, matching `phase1.retraceThreshold` convention. The code divides by 100 internally. Do NOT use `0.05`.

11. **`stagnation.thresholdHours` not `staleHours`** — The stagnation idle duration field is `thresholdHours`. Using `staleHours` will be silently ignored (defaults to 1.0h).
