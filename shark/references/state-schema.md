# SHARK State File Schemas

All state files live under `state/{strategyId}/`. DSL state files live under `dsl/{strategyId}/`.

## Directory Layout

```
state/{strategyId}/
  shark-state.json         # Active positions, stalking/strike watchlists
  shark-oi-history.json    # OI + price time-series (24h of 5min snapshots)
  shark-liq-map.json       # Current estimated liquidation zones per asset
  trade-counter.json       # Daily trade counter + gate status
  peak-balance.json        # Peak account value for drawdown tracking

dsl/{strategyId}/
  {ASSET}.json             # DSL v5.3.1 state per position (main dex)
  xyz--{ASSET}.json        # DSL v5.3.1 state per position (xyz dex)
```

---

## shark-state.json

Primary state file tracking the SHARK pipeline.

```json
{
  "stalking": ["ETH", "SOL"],
  "strike": ["ETH"],
  "active_positions": {
    "ETH": {
      "direction": "SHORT",
      "entry_price": 2050.5,
      "opened_at": "2026-03-05T12:00:00Z",
      "cascade_oi_at_entry": 567606.3264,
      "target_liq_zone": 1980.0,
      "pattern": "LONG_LIQUIDATION_CASCADE",
      "triggers": [
        {"trigger": "oi_drop", "confidence": "HIGH", "value": "-3.5%"},
        {"trigger": "zone_break", "confidence": "HIGH", "value": "price 1985 <= zone 1990"}
      ],
      "margin": 900.0,
      "leverage": 8
    }
  },
  "updated_at": "2026-03-05T12:00:00Z"
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `stalking` | string[] | Assets with score >= 0.55 (set by liq-mapper) |
| `strike` | string[] | Assets with proximity >= 0.60 AND within 3% of zone (set by proximity scanner) |
| `active_positions` | object | Currently open SHARK positions |
| `active_positions.{asset}.direction` | "LONG" \| "SHORT" | Trade direction |
| `active_positions.{asset}.entry_price` | number | Price at entry |
| `active_positions.{asset}.opened_at` | ISO string | Entry timestamp |
| `active_positions.{asset}.cascade_oi_at_entry` | number | OI (units) at entry — used for cascade invalidation |
| `active_positions.{asset}.target_liq_zone` | number | Estimated liquidation zone price |
| `active_positions.{asset}.pattern` | string | "LONG_LIQUIDATION_CASCADE" or "SHORT_LIQUIDATION_CASCADE" |
| `active_positions.{asset}.triggers` | object[] | Triggers that fired at entry |
| `active_positions.{asset}.margin` | number | Margin allocated (USDC) |
| `active_positions.{asset}.leverage` | number | Leverage used |

---

## shark-oi-history.json

Time-series OI + price + funding snapshots. One array per asset.

```json
{
  "BTC": [
    {
      "ts": 1709571600,
      "oi": 23712.54,
      "price": 71120.5,
      "funding": 0.0000125,
      "oi_usd": 1686187163.7
    }
  ],
  "ETH": [
    {
      "ts": 1709571600,
      "oi": 567606.33,
      "price": 2079.55,
      "funding": 0.0000125,
      "oi_usd": 1180280523.61
    }
  ]
}
```

### Entry Fields

| Field | Type | Description |
|-------|------|-------------|
| `ts` | integer | Unix timestamp |
| `oi` | number | Open interest in asset units |
| `price` | number | Mid price at snapshot |
| `funding` | number | Hourly funding rate |
| `oi_usd` | number | OI × price (USD value) |

**Retention:** Max 288 entries per asset (~24h at 5min intervals). Top 60 assets by OI value tracked.

---

## shark-liq-map.json

Estimated liquidation zones and scores per asset.

```json
{
  "ETH": {
    "long_liq_zone": {
      "price": 1980.0,
      "estimated_oi": 15000000,
      "avg_leverage": 10,
      "avg_entry": 2200.0
    },
    "short_liq_zone": {
      "price": 2320.0,
      "estimated_oi": 8000000,
      "avg_leverage": 12,
      "avg_entry": 2100.0
    },
    "current_price": 2050.5,
    "proximity_to_long_liq": 0.034,
    "proximity_to_short_liq": 0.131,
    "stalking": true,
    "stalking_direction": "SHORT",
    "score": 0.72,
    "proximity_score": 0.65,
    "proximity_signals": {
      "distance_pct": 0.034,
      "momentum": 0.6,
      "oi_crack": 0.4,
      "volume": 0.3,
      "book_thin": 0.7
    },
    "updated_at": "2026-03-05T12:00:00Z"
  }
}
```

### Zone Fields

| Field | Type | Description |
|-------|------|-------------|
| `price` | number | Estimated liquidation zone price |
| `estimated_oi` | number | USD value of OI that built up in this zone |
| `avg_leverage` | number | Estimated average leverage (from funding) |
| `avg_entry` | number | Weighted average entry price for positions in zone |

### Scoring Fields

| Field | Type | Description |
|-------|------|-------------|
| `score` | number | Phase 1 mapper score (0-1). >= 0.55 → stalking |
| `proximity_score` | number | Phase 2 proximity score (0-1). >= 0.60 → strike |
| `proximity_signals` | object | Sub-signal breakdown for proximity scoring |
| `stalking` | boolean | Whether asset is in stalking watchlist |
| `stalking_direction` | string | Direction SHARK would trade ("LONG" or "SHORT") |

---

## trade-counter.json

Daily trade counter and gate status.

```json
{
  "date": "2026-03-05",
  "accountValueStart": 5000,
  "entries": 2,
  "realizedPnl": -150.5,
  "gate": "OPEN",
  "gateReason": null,
  "cooldownUntil": null,
  "lastResults": [
    {"asset": "ETH", "pnl": -75.2, "closedAt": "2026-03-05T10:00:00Z"},
    {"asset": "SOL", "pnl": -75.3, "closedAt": "2026-03-05T11:00:00Z"}
  ],
  "maxEntriesPerDay": 6,
  "maxConsecutiveLosses": 3,
  "cooldownMinutes": 45
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `date` | string | Current date (YYYY-MM-DD). Resets on new day. |
| `accountValueStart` | number | Account value at start of day |
| `entries` | integer | Number of entries today |
| `realizedPnl` | number | Realized PnL today (USDC) |
| `gate` | string | "OPEN", "CLOSED", or "COOLDOWN" |
| `gateReason` | string \| null | Why gate is closed |
| `cooldownUntil` | ISO string \| null | When cooldown expires |
| `lastResults` | object[] | Recent trade results for consecutive loss tracking |
| `maxEntriesPerDay` | integer | Max entries allowed (6) |
| `maxConsecutiveLosses` | integer | Losses before cooldown (3) |
| `cooldownMinutes` | integer | Cooldown duration (45 min) |

---

## peak-balance.json

Peak account value for drawdown tracking.

```json
{
  "peak": 5250.0,
  "updated_at": "2026-03-05T12:00:00Z"
}
```

---

## Strategy Registry Entry

Stored in `strategies/shark-strategies.json`.

```json
{
  "name": "Liquidation Cascade",
  "wallet": "0x...",
  "strategyId": "UUID",
  "budget": 5000,
  "maxSlots": 2,
  "marginPct": 0.18,
  "defaultLeverage": 8,
  "tradingRisk": "aggressive",
  "dailyLossLimit": 600,
  "drawdownCap": 1250,
  "autoDeleverThreshold": 4000,
  "maxSingleLossPct": 5,
  "maxEntriesPerDay": 6,
  "dsl": {
    "preset": "aggressive",
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
    ]
  },
  "enabled": true,
  "createdAt": "2026-03-05T12:00:00Z"
}
```

### Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `marginPct` | number | Fraction of budget per trade (0.18 = 18%) |
| `maxSlots` | integer | Max concurrent positions (2) |
| `dailyLossLimit` | number | 12% of budget |
| `drawdownCap` | number | 25% of budget |
| `dsl.tiers` | object[] | 9-tier DSL v5.3.1 configuration |
