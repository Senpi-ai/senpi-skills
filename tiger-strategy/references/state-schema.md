# TIGER State Schema

All state files use atomic writes (`os.replace()`). All percentage values are whole numbers unless noted.

---

## Directory Layout

```
{workspace}/
├── tiger-config.json                    # Skill config (single source of truth)
├── state/
│   └── {instanceKey}/                   # Per-strategy instance
│       ├── tiger-state.json             # Core state: positions, aggression, safety
│       ├── dsl-{ASSET}.json             # Per-position DSL trailing stop state
│       ├── oi-history.json              # 24h OI time-series (288 entries per asset)
│       ├── trade-log.json               # All trades with outcomes
│       └── scan-history/                # Scanner output snapshots
└── memory/
    └── tiger-YYYY-MM-DD.md             # Daily reports
```

---

## tiger-state.json

```json
{
  "version": 1,
  "active": true,
  "instanceKey": "strategy-abc123",
  "createdAt": "2026-02-25T10:00:00.000Z",
  "updatedAt": "2026-02-25T14:32:00.000Z",

  "currentBalance": 1200,
  "peakBalance": 1300,
  "dayStartBalance": 1100,
  "dailyPnl": 100,
  "totalPnl": 200,
  "tradesToday": 3,
  "winsToday": 2,
  "totalTrades": 15,
  "totalWins": 9,

  "aggression": "NORMAL",
  "dailyRateNeeded": 10,
  "daysRemaining": 5,
  "dayNumber": 3,

  "activePositions": {
    "ETH": {
      "direction": "LONG",
      "entryPrice": 3500.0,
      "leverage": 10,
      "sizeUsd": 300,
      "pattern": "COMPRESSION_BREAKOUT",
      "confluenceScore": 0.65,
      "openedAt": "2026-02-25T12:00:00.000Z",
      "dslStateFile": "dsl-ETH.json"
    }
  },

  "safety": {
    "halted": false,
    "haltReason": null,
    "dailyLossPct": 0,
    "tradesToday": 3
  },

  "lastGoalRecalc": "2026-02-25T14:00:00.000Z",
  "lastBtcPrice": 95000,
  "lastBtcCheck": "2026-02-25T14:30:00.000Z"
}
```

### Field Notes

| Field | Type | Unit | Notes |
|-------|------|------|-------|
| `currentBalance` | float | USD | From clearinghouse |
| `peakBalance` | float | USD | All-time high |
| `dailyRateNeeded` | int | % | Required daily return to hit target. Whole number. |
| `aggression` | string | — | CONSERVATIVE / NORMAL / ELEVATED / ABORT |
| `confluenceScore` | float | 0-1 | Weighted score, NOT a whole number |
| `sizeUsd` | float | USD | Notional position size |
| `leverage` | int | × | Leverage multiplier |

---

## dsl-{ASSET}.json

```json
{
  "version": 1,
  "active": true,
  "asset": "ETH",
  "direction": "LONG",
  "entryPrice": 3500.0,
  "size": 0.1,
  "leverage": 10,
  "wallet": "0x...",
  "highWaterPrice": 3550.0,
  "phase": 1,
  "currentBreachCount": 0,
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "phase1": {
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": 3430.0
  },
  "phase2": {
    "retraceThreshold": 0.012,
    "consecutiveBreachesRequired": 2
  },
  "phase2TriggerTier": 1,
  "tiers": [
    { "triggerPct": 5, "lockPct": 20, "retrace": 0.015 },
    { "triggerPct": 10, "lockPct": 50, "retrace": 0.012 },
    { "triggerPct": 20, "lockPct": 70, "retrace": 0.010 },
    { "triggerPct": 35, "lockPct": 80, "retrace": 0.008 }
  ],
  "breachDecay": "soft",
  "createdAt": "2026-02-25T12:00:00.000Z",
  "updatedAt": "2026-02-25T14:30:00.000Z"
}
```

### DSL Field Notes

| Field | Type | Notes |
|-------|------|-------|
| `triggerPct` | int | ROE % to activate tier. `5` = 5% ROE. |
| `lockPct` | int | % of high-water move to lock. `20` = lock 20%. |
| `retrace` | float | Fraction, NOT percent. `0.015` = 1.5% retrace. |
| `retraceThreshold` | float | Same as retrace — fraction. |
| `absoluteFloor` | float | Price level, not percentage. |
| `currentTierIndex` | int | -1 = no tier hit yet. 0-indexed. |
| `breachDecay` | string | `"soft"` = breach count decays. `"hard"` = no decay. |

---

## oi-history.json

```json
{
  "SOL": [
    { "ts": 1740492720, "oi": 45200000, "price": 142.50 }
  ]
}
```

Rolling 288 entries per asset (24h at 5min intervals). Oldest trimmed on write.

---

## trade-log.json

```json
[
  {
    "version": 1,
    "timestamp": "2026-02-25T16:45:00.000Z",
    "asset": "ETH",
    "pattern": "COMPRESSION_BREAKOUT",
    "direction": "LONG",
    "entryPrice": 3500.0,
    "exitPrice": 3675.0,
    "leverage": 10,
    "sizeUsd": 300,
    "pnlUsd": 150,
    "feesUsd": 5.4,
    "holdMinutes": 180,
    "exitReason": "DSL_TIER_2",
    "confluenceScore": 0.65,
    "aggression": "NORMAL"
  }
]
```
