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
| `defaultLeverage` | number | Fallback leverage when max-leverage data unavailable. Actual leverage is computed dynamically from `tradingRisk`. |
| `tradingRisk` | string | Risk tier for dynamic leverage: `"conservative"`, `"moderate"`, or `"aggressive"`. Defaults to `"moderate"` if absent. |
| `dailyLossLimit` | number | Max daily loss before reducing exposure |
| `autoDeleverThreshold` | number | Account value below which to reduce slots by 1 |
| `dsl.preset` | string | "aggressive" or "conservative" |
| `dsl.tiers` | array | 4-tier DSL config |
| `guardRails` | object | Guard rail overrides: `maxEntriesPerDay`, `bypassOnProfit`, `maxConsecutiveLosses`, `cooldownMinutes`. All optional; defaults used for missing keys. |
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
├── wolf-strategies.json              # Strategy registry (includes global.dslCliPath, global.dslScriptPath)
├── max-leverage.json                 # Shared across strategies
├── dsl/                              # DSL v5.2 state root (owned by dsl-dynamic-stop-loss skill)
│   ├── abc12345-.../                 # Strategy A UUID dir
│   │   ├── strategy-abc12345....json # DSL strategy config (created by dsl-cli add-dsl)
│   │   ├── HYPE.json                 # Position state file
│   │   └── xyz--SILVER.json          # XYZ position (xyz:SILVER → xyz--SILVER filename)
│   └── xyz78901-.../                 # Strategy B UUID dir
│       ├── strategy-xyz78901....json
│       └── HYPE.json                 # Same asset, different strategy UUID dir = no collision
├── state/
│   ├── wolf-abc12345/                # Strategy A wolf state (non-DSL only)
│   │   ├── trade-counter.json        # Daily trade counter (guard rails)
│   │   └── locks/                    # Strategy lock files
│   └── wolf-xyz78901/
│       └── trade-counter.json
├── history/
│   └── emerging-movers.json          # Shared (market data)
├── memory/
│   └── MEMORY.md
└── logs/
    └── wolf-2026-02-24.log
```

**Why `dsl/` is separate from `state/`:** `dsl-v5.py` scans the strategy directory and treats every `.json` file (excluding `strategy-*.json` and `*_archived*`) as a position state file. Wolf's `state/{strategyKey}/` contains `trade-counter.json` — those would be misread as positions. Separate dirs eliminates the collision.

**Why DSL dirs use UUID:** `dsl-v5.py` uses `DSL_STRATEGY_ID` (UUID) to call `strategy_get` for wallet resolution. The directory name must match the UUID so the cron env and filesystem stay consistent.

**Why `history/` is shared:** Emerging movers and scanner detect market-wide signals. The signal is the same regardless of which strategy acts on it.

---

## DSL State File (`dsl/{strategyId_UUID}/{ASSET}.json`)

Created per position by `dsl-cli.py add-dsl`. Owned and updated by `dsl-v5.py`. Wolf scripts read these files but never write them directly — all writes go through `dsl-cli.py`.

**File naming:** `HYPE.json`, `xyz--SILVER.json` (`:` replaced with `--` for XYZ assets). No `dsl-` prefix.

**Archive on close:** File is renamed to `{ASSET}_archived_{epoch}.json` by `dsl-cli.py delete-dsl`. Never set `active: false` in place.

```json
{
  "active": true,
  "asset": "HYPE",
  "direction": "LONG",
  "leverage": 6.0,
  "entryPrice": 30.808,
  "size": 3.9,
  "wallet": "0xdca2c5c0f1b71c6404a87c771c57ac7c1b22219b",
  "strategyId": "6a23783a-12e6-415c-b59b-70ca5e5c3a1d",
  "phase": 1,
  "phase1": {
    "enabled": true,
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": 30.654
  },
  "phase2TriggerTier": 0,
  "phase2": {
    "enabled": true,
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2,
    "tiers": [
      { "triggerPct": 5,  "lockPct": 50 },
      { "triggerPct": 10, "lockPct": 65 },
      { "triggerPct": 15, "lockPct": 75 },
      { "triggerPct": 20, "lockPct": 85 }
    ]
  },
  "tiers": [ ... ],
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "highWaterPrice": 30.9245,
  "floorPrice": 30.7699,
  "currentBreachCount": 0,
  "createdAt": "2026-03-07T16:44:06.000Z",
  "consecutiveFetchFailures": 0,
  "lastPrice": 30.8275,
  "lastSyncedFloorPrice": 30.7699,
  "slOrderIdUpdatedAt": "2026-03-07T17:27:05Z",
  "slOrderId": 341460233650,
  "lastCheck": "2026-03-07T18:00:03Z"
}
```

### DSL v5.2 vs old wolf DSL format

| Field | Old wolf format | DSL v5.2 |
|---|---|---|
| `phase1.retraceThreshold` | `10` (percentage) | `0.03` (ROE fraction: 0–1) |
| Per-tier breach counts | `tiers[].breaches: 3/2/2/1` | Not supported — single `phase2.consecutiveBreachesRequired` |
| Tier structure | top-level `tiers[]` only | `phase2.tiers[]` **and** top-level `tiers[]` (mirrored) |
| HL SL tracking | absent | `slOrderId`, `lastSyncedFloorPrice`, `slOrderIdUpdatedAt` |
| `strategyKey` field | present | absent (`strategyId` is UUID) |
| File location | `state/{strategyKey}/dsl-{ASSET}.json` | `dsl/{strategyId_UUID}/{ASSET}.json` |
| File naming | `dsl-HYPE.json` | `HYPE.json` (no prefix) |
| Archive on close | `active: false` in place | renamed to `{ASSET}_archived_{epoch}.json` |

### Key fields

| Field | Type | Description |
|---|---|---|
| `active` | boolean | `true` = DSL is running. Never set directly — use `dsl-cli.py delete-dsl` to close. |
| `asset` | string | Asset name (e.g. "HYPE"). No `xyz:` prefix in state, but file uses `xyz--SILVER.json`. |
| `direction` | string | "LONG" or "SHORT" |
| `entryPrice` | number | Position entry price (fetched from clearinghouse by CLI) |
| `leverage` | number | Position leverage |
| `size` | number | Absolute position size |
| `wallet` | string | Strategy wallet address |
| `strategyId` | string | Strategy UUID (used by dsl-v5.py for `strategy_get`) |
| `phase` | number | 1 = pre-tier (Phase 1 floor active), 2 = tier-based trailing |
| `phase1.retraceThreshold` | number | ROE fraction (0–1) for phase 1 floor, e.g. `0.10` = 10% ROE |
| `phase2.tiers[]` | array | Tiers without `breaches` field (DSL v5.2 uses single breach count) |
| `highWaterPrice` | number | Best price seen — used by watchdog for peak ROE calculation |
| `floorPrice` | number | Current effective stop floor |
| `slOrderId` | number\|null | Native HL stop-loss order ID (set by dsl-v5.py via `edit_position`) |
| `lastCheck` | string | ISO timestamp of last dsl-v5.py run — used for staleness detection |

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

1. **Never write DSL state files directly** — All DSL state creation/deletion goes through `dsl-cli.py add-dsl` / `delete-dsl`. `dsl-v5.py` owns the state; wolf scripts are callers only.

2. **`phase1.retraceThreshold` is a fraction (0–1), not a percentage** — Use `0.10` for 10% ROE floor, NOT `10`. This changed from the old wolf v4 format.

3. **No per-tier breach counts in DSL v5.2** — `phase2.consecutiveBreachesRequired` is a single value for all tiers. `build_wolf_dsl_config()` derives this from the majority breach count in the wolf strategy's tier config.

4. **`active: false` is NOT how you close** — Call `dsl-cli.py delete-dsl`. It archives the file (rename to `{ASSET}_archived_{epoch}.json`) and emits `cron_to_remove` if the last position was closed. Setting `active: false` in place is the old behavior and breaks DSL v5.2.

5. **File naming** — `HYPE.json` (no `dsl-` prefix), `xyz--SILVER.json` (colon → double-dash for XYZ). `wolf_config.py`'s `asset_to_filename()` handles this conversion.

6. **UUID directory, not strategyKey** — DSL state files live under `dsl/{strategyId_UUID}/`, NOT `state/{strategyKey}/`. `dsl_state_path()` and `dsl_state_glob()` in `wolf_config.py` resolve this automatically.

7. **Filter archived/config files when globbing** — `dsl_position_state_files()` in `wolf_config.py` already filters out `strategy-*.json` and `*_archived*`. Use it instead of raw glob.

8. **XYZ assets** — Pass `dex="xyz"` to `dsl-cli.py add-dsl`/`delete-dsl`. Use `coin=xyz:ASSET` when calling `close_position`. Use `leverageType: "ISOLATED"` when opening. Use `xyzWallet` (not `wallet`) for `--wallet` arg.

9. **`--wallet` is mandatory for wolf** — Always pass `--wallet` to `dsl-cli.py add-dsl`. Wolf knows the wallet; this skips the `strategy_get` MCP call inside the CLI and avoids latency.

10. **Migration from v4 state** — Old files at `state/{strategyKey}/dsl-{ASSET}.json` must be migrated to `dsl/{UUID}/{ASSET}.json` before switching to DSL v5 crons. Run `python3 scripts/wolf-migrate-dsl.py` once.

11. **Atomic writes (wolf-side)** — Wolf scripts use `atomic_write()` for non-DSL state (trade counters, etc.). DSL state is exclusively managed by the DSL skill's CLI/cron.

---

## Trade Counter (`state/{strategyKey}/trade-counter.json`)

Created and maintained by `risk-guardian.py`. Read by `check_gate()` in `wolf_config.py` for gate checks in `open-position.py` and `emerging-movers.py`.

```json
{
  "date": "2026-03-05",
  "accountValueStart": 6500.00,
  "entries": 3,
  "closedTrades": 1,
  "realizedPnl": -45.20,
  "gate": "OPEN",
  "gateReason": null,
  "cooldownUntil": null,
  "lastResults": ["W", "L", "W"],
  "processedOrderIds": ["ord-abc123", "ord-def456"],
  "maxEntriesPerDay": 8,
  "bypassOnProfit": true,
  "maxConsecutiveLosses": 3,
  "cooldownMinutes": 60,
  "updatedAt": "2026-03-05T10:00:00Z"
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `date` | string | UTC date (YYYY-MM-DD). Triggers rollover when != today. |
| `accountValueStart` | number\|null | Account value at first run of the day. Set once, used by G1. |
| `entries` | number | Positions opened today. Incremented by `open-position.py`. |
| `closedTrades` | number | Trades closed today (recorded by risk-guardian). |
| `realizedPnl` | number | Sum of realized PnL from today's closed trades. |
| `gate` | string | `"OPEN"`, `"COOLDOWN"`, or `"CLOSED"`. |
| `gateReason` | string\|null | Human-readable reason for current gate state. |
| `cooldownUntil` | string\|null | ISO timestamp when COOLDOWN expires. |
| `lastResults` | array | Last 20 W/L/R results. Carries across days. "R" = cooldown reset marker. |
| `processedOrderIds` | array | `closedOrderId` strings already recorded (dedup). Reset on day rollover. |
| `maxEntriesPerDay` | number | Config: max entries before G3 fires. |
| `bypassOnProfit` | boolean | Config: skip G3 if day is profitable. |
| `maxConsecutiveLosses` | number | Config: consecutive "L" count before G4 cooldown. |
| `cooldownMinutes` | number | Config: minutes for G4 cooldown duration. |
| `updatedAt` | string | ISO timestamp of last save. |

### Day Rollover Rules (date != today UTC)

- **Reset:** `entries`, `closedTrades`, `realizedPnl` → 0; `accountValueStart` → null; `gate` → OPEN (unless active cooldown); `processedOrderIds` → []
- **Preserve:** `lastResults` (streak carries across days), active cooldown if `cooldownUntil` is still in the future
