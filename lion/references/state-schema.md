# LION State Schema

All state files use atomic writes (`os.replace()`). All percentage values are whole numbers (5 = 5%).

---

## Directory Layout

```
{workspace}/
├── lion-config.json                     # Skill config (single source of truth)
├── state/
│   └── {instanceKey}/                   # Per-strategy instance
│       ├── lion-state.json              # Core state: positions, watchlist, safety
│       └── trade-log.json               # All trades with outcomes
├── history/
│   └── oi-history.json                  # Market-wide OI time-series (shared)
└── memory/
    └── lion-YYYY-MM-DD.md               # Daily reports
```

**Key principle:** OI history is market-wide (shared across instances). Position state is instance-scoped.

---

## lion-state.json

```json
{
  "version": 1,
  "active": true,
  "instanceKey": "strategy-abc123",
  "createdAt": "2026-02-25T10:00:00.000Z",
  "updatedAt": "2026-02-25T14:32:00.000Z",

  "budget": 5000,
  "startingEquity": 5000,
  "strategyId": "STRATEGY_ID",
  "strategyWallet": "0x...",

  "activePositions": {
    "SOL": {
      "pattern": "CASCADE_REVERSAL",
      "direction": "LONG",
      "entryPrice": 142.50,
      "leverage": 6,
      "sizeUsd": 800,
      "openedAt": "2026-02-25T14:32:00.000Z",
      "cascadeMagnitudePct": 4,
      "targetPrice": 145.10,
      "stopPrice": 140.80,
      "highWaterRoe": 3,
      "trailingLock": 70,
      "cascadePhaseAtEntry": "ENTRY_WINDOW"
    }
  },

  "watchlist": {
    "squeeze": {
      "DOGE": {
        "fundingExtremeSince": "2026-02-25T08:00:00.000Z",
        "periodsExtreme": 3,
        "crowdDirection": "LONG",
        "squeezeDirection": "SHORT"
      }
    },
    "preCascade": {
      "HYPE": {
        "oiVelocity": -0.8,
        "flaggedAt": "2026-02-25T14:28:00.000Z"
      }
    }
  },

  "dailyStats": {
    "date": "2026-02-25",
    "trades": 1,
    "wins": 1,
    "cascadesDetected": 2,
    "cascadesTraded": 1,
    "squeezesDetected": 1,
    "squeezesTraded": 0,
    "imbalancesDetected": 3,
    "imbalancesTraded": 0,
    "grossPnl": 156,
    "fees": 14,
    "netPnl": 142
  },

  "safety": {
    "halted": false,
    "haltReason": null,
    "tradesToday": 1,
    "thresholdMultiplier": 100
  }
}
```

### Field Notes

| Field | Type | Unit | Notes |
|-------|------|------|-------|
| `cascadeMagnitudePct` | int | % | How much OI dropped. Whole number: `4` = 4%. |
| `trailingLock` | int | % | Lock percentage. `70` = lock 70% of gains. |
| `highWaterRoe` | int | % | Peak ROE. Whole number. |
| `thresholdMultiplier` | int | % | `100` = normal. `125` = thresholds raised 25% after max daily trades. |
| `sizeUsd` | float | USD | Notional position size. |
| `leverage` | int | × | Leverage multiplier. |

---

## oi-history.json (shared signal)

Stored in `history/`, not `state/{instance}/`. Market-wide data used by all instances.

```json
{
  "SOL": [
    {
      "ts": 1740492720,
      "oi": 45200000,
      "price": 142.50,
      "volume15m": 8500000,
      "funding": 0.0003
    }
  ],
  "BTC": [
    ...
  ]
}
```

Rolling 240 entries per asset (4 hours at 60s intervals). Oldest entries trimmed on write.

### Derived Metrics (computed at read time, not stored)

| Metric | Calculation | Purpose |
|--------|------------|---------|
| `oiChange5mPct` | (current - 5min ago) / 5min ago × 100 | Short-term velocity |
| `oiChange15mPct` | (current - 15min ago) / 15min ago × 100 | Cascade detection window |
| `oiChange1hPct` | (current - 1h ago) / 1h ago × 100 | Larger trend |
| `oiVelocity` | delta of oiChange5mPct over last 3 samples | Rate of change of change |
| `oiAcceleration` | delta of oiVelocity over last 3 samples | Phase detection |

---

## trade-log.json

```json
[
  {
    "version": 1,
    "timestamp": "2026-02-25T16:45:00.000Z",
    "asset": "SOL",
    "pattern": "CASCADE_REVERSAL",
    "direction": "LONG",
    "entryPrice": 142.50,
    "exitPrice": 145.10,
    "leverage": 6,
    "sizeUsd": 800,
    "pnlUsd": 156,
    "feesUsd": 14,
    "holdMinutes": 78,
    "exitReason": "SNAPBACK_TARGET",
    "cascadeMagnitudePct": 4,
    "snapbackPct": 52,
    "cascadePhaseAtEntry": "ENTRY_WINDOW"
  }
]
```
