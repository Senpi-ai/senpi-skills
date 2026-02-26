# VIPER State Schema

All state files use atomic writes (`os.replace()`). All percentage values are whole numbers (5 = 5%).

---

## Directory Layout

```
{workspace}/
├── viper-config.json                    # Skill config (single source of truth)
├── state/
│   └── {instanceKey}/                   # Per-strategy instance
│       ├── viper-state.json             # Core state: ranges, safety, daily stats
│       ├── range-{ASSET}.json           # Per-range boundary data + bounce history
│       └── trade-log.json               # All trades with outcomes
├── history/
│   └── scan-history.json                # Cross-run range candidate tracking
└── memory/
    └── viper-YYYY-MM-DD.md              # Daily reports
```

---

## viper-state.json

```json
{
  "version": 1,
  "active": true,
  "instanceKey": "strategy-abc123",
  "createdAt": "2026-02-25T10:00:00.000Z",
  "updatedAt": "2026-02-25T14:30:00.000Z",

  "budget": 5000,
  "startingEquity": 5000,
  "strategyId": "STRATEGY_ID",
  "strategyWallet": "0x...",

  "ranges": {
    "ETH": {
      "phase": "TRADING",
      "rangeScore": 0.78,
      "confirmedAt": "2026-02-25T10:00:00.000Z",
      "rangeHealth": "HEALTHY",
      "breakWarning": false,
      "bouncesTraded": 3,
      "bouncesWon": 2,
      "adxCurrent": 16.2,
      "bbWidthPercentile": 22
    }
  },

  "activePositions": {
    "SOL": {
      "direction": "LONG",
      "entryPrice": 142.20,
      "leverage": 7,
      "sizeUsd": 600,
      "openedAt": "2026-02-25T12:15:00.000Z",
      "stopPrice": 140.80,
      "tpPrice": 146.50,
      "trailingLock": 0,
      "highWaterPnl": 45,
      "bounceNumber": 2,
      "rangeAsset": "SOL"
    }
  },

  "pendingOrders": {
    "ETH": {
      "side": "SHORT",
      "price": 2558.00,
      "postedAt": "2026-02-25T13:48:00.000Z",
      "orderId": null
    }
  },

  "cooldown": {
    "HYPE": {
      "brokeAt": "2026-02-25T11:30:00.000Z",
      "breakDirection": "LONG",
      "cooldownUntil": "2026-02-25T15:30:00.000Z"
    }
  },

  "dailyStats": {
    "date": "2026-02-25",
    "bouncesTraded": 4,
    "bouncesWon": 3,
    "bouncesLost": 0,
    "breakExits": 1,
    "grossPnl": 175,
    "fees": 28,
    "netPnl": 147,
    "makerFillRate": 89,
    "avgHoldHours": 5.8
  },

  "safety": {
    "halted": false,
    "haltReason": null,
    "consecutiveStopsPerRange": {},
    "dailyLossPct": 0,
    "tradesToday": 4
  }
}
```

### Field Notes

| Field | Type | Unit | Notes |
|-------|------|------|-------|
| `rangeScore` | float | 0-1 | Composite score from range detection |
| `phase` | string | — | `SCANNING`, `VALIDATING`, `TRADING`, `BROKEN`, `COOLDOWN` |
| `rangeHealth` | string | — | `HEALTHY`, `WEAKENING`, `AGING` |
| `trailingLock` | int | % | 0 = no lock. 60 = lock 60% of gains. Whole numbers. |
| `makerFillRate` | int | % | Percentage of entries that filled as maker. Whole number. |
| `dailyLossPct` | int | % | Current day's loss as % of balance. Whole number. |
| `sizeUsd` | float | USD | Notional position size |
| `leverage` | int | x | Leverage multiplier |

---

## range-{ASSET}.json

```json
{
  "version": 1,
  "active": true,
  "instanceKey": "strategy-abc123",
  "asset": "ETH",
  "createdAt": "2026-02-25T10:00:00.000Z",
  "updatedAt": "2026-02-25T14:30:00.000Z",

  "supportLevel": 2480.00,
  "supportZone": [2475.00, 2485.00],
  "resistanceLevel": 2560.00,
  "resistanceZone": [2555.00, 2565.00],
  "rangeWidthPct": 3.2,
  "rangeMidpoint": 2520.00,
  "deadZone": [2505.00, 2535.00],
  "touchesSupport": 4,
  "touchesResistance": 3,

  "bounceHistory": [
    {
      "bounceNumber": 1,
      "direction": "LONG",
      "entryPrice": 2481.50,
      "exitPrice": 2554.20,
      "entryAt": "2026-02-25T10:30:00.000Z",
      "exitAt": "2026-02-25T16:45:00.000Z",
      "pnlUsd": 52.30,
      "exitReason": "TP_HIT",
      "makerEntry": true
    }
  ]
}
```

---

## trade-log.json

```json
[
  {
    "version": 1,
    "timestamp": "2026-02-25T16:45:00.000Z",
    "asset": "ETH",
    "direction": "LONG",
    "entryPrice": 2481.50,
    "exitPrice": 2554.20,
    "leverage": 7,
    "sizeUsd": 600,
    "pnlUsd": 52.30,
    "feesUsd": 1.72,
    "holdMinutes": 375,
    "exitReason": "TP_HIT",
    "bounceNumber": 1,
    "rangeScore": 0.78,
    "rangeWidthPct": 3.2,
    "makerEntry": true,
    "makerExit": true
  }
]
```
