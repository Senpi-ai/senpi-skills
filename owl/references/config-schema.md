# OWL Config Schema

## owl-config.json

Created by `owl-setup.py`. All percentage values are decimals unless noted.

```json
{
  "version": 1,
  "strategyId": "UUID",
  "wallet": "0x...",
  "budget": 2000,
  "chatId": "12345",
  "workspace": "/data/workspace",

  "slots": {
    "maxSlots": 3,
    "maxBtcCorrelatedSlots": 1,
    "maxSameDirectionSlots": 2,
    "marginTiers": [
      { "slot": 1, "pct": 0.20 },
      { "slot": 2, "pct": 0.15 },
      { "slot": 3, "pct": 0.12 }
    ]
  },

  "leverage": {
    "default": 8,
    "max": 10,
    "min": 7
  },

  "crowding": {
    "minCrowdingScore": 0.60,
    "minPersistenceCount": 24,
    "snapshotIntervalMin": 5,
    "maxHistoryHours": 48,
    "weights": {
      "fundingExtremity": 0.25,
      "oiVsAvg": 0.20,
      "smConcentration": 0.20,
      "bookImbalance": 0.15,
      "fundingAcceleration": 0.10,
      "volumeDecline": 0.10
    },
    "thresholds": {
      "fundingAnnualizedMin": 30,
      "oiAboveAvgPct": 20,
      "smConcentrationMin": 0.70,
      "bookImbalanceMin": 3.0,
      "maxDeepScanAssets": 6
    }
  },

  "exhaustion": {
    "minExhaustionScore": 0.50,
    "readyExpiryHours": 4,
    "weights": {
      "fundingPlateau": 0.25,
      "oiDeclining": 0.20,
      "priceStalling": 0.20,
      "rsiDivergence": 0.15,
      "volumeExhaustion": 0.10,
      "smShifting": 0.10
    }
  },

  "entry": {
    "minTriggers": 2,
    "triggers": ["price_break", "oi_drop", "funding_flip", "book_reversal", "sm_flip"],
    "oiDropThresholdPct": 3,
    "bookReversalThreshold": 1.5
  },

  "dsl": {
    "phase1RetraceBase": 0.03,
    "phase1HardTimeoutMin": 75,
    "phase1WeakPeakCutMin": 40,
    "phase1DeadWeightCutMin": 25,
    "phase1GreenIn10TightenPct": 0,
    "phase1ConsecutiveBreaches": 3,
    "phase2RetraceThreshold": 0.015,
    "phase2ConsecutiveBreaches": 2,
    "tiers": [
      { "triggerPct": 8, "lockPct": 3 },
      { "triggerPct": 15, "lockPct": 8 },
      { "triggerPct": 25, "lockPct": 18 },
      { "triggerPct": 40, "lockPct": 30 },
      { "triggerPct": 60, "lockPct": 48 },
      { "triggerPct": 80, "lockPct": 68 }
    ]
  },

  "risk": {
    "maxSingleLossPct": 6,
    "maxDailyLossPct": 10,
    "maxDrawdownPct": 18,
    "reCrowdingExitEnabled": true,
    "oiRecoveryExitMin": 30,
    "fundingFloorAdjustEnabled": true,
    "fundingFloorMinPct": 0.10
  },

  "btcCorrelation": {
    "correlatedAlts": [
      "SOL", "ETH", "DOGE", "AVAX", "LINK", "ADA", "DOT",
      "NEAR", "ATOM", "SEI", "SUI", "PEPE", "WIF", "RENDER"
    ],
    "independenceScoreDropThreshold": 0.30
  }
}
```

## Config Field Notes

- `marginTiers[].pct`: Fraction of budget, not percentage. 0.20 = 20%.
- `crowding.weights`: Must sum to 1.0.
- `exhaustion.weights`: Must sum to 1.0.
- `dsl.phase1RetraceBase`: Divided by leverage for actual floor distance. 0.03 at 8x = 0.375% price distance = ~30% ROE max loss.
- `dsl.tiers[].triggerPct`: ROE percentage (8 = 8% ROE), not price percentage.
- `dsl.tiers[].lockPct`: Percentage of high-water ROE move to lock as floor.
- `risk.*Pct`: Whole numbers for max loss/drawdown (6 = 6%), decimals for funding floor (0.10 = 0.1% of margin).
- `btcCorrelation.independenceScoreDropThreshold`: If removing BTC's contribution drops an alt's crowding score by >30%, it's BTC-dependent.
