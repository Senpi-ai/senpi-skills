# Output JSON Schema

## Full Example

```json
{
  "scanTime": "2026-02-20T14:40:00Z",
  "assetsScanned": 229,
  "passedStage1": 70,
  "passedStage2": 16,
  "deepDived": 16,
  "disqualified": 3,
  "btcContext": {
    "trend": "strong_down",
    "strength": 45,
    "chg1h": -1.8,
    "macroModifier": { "LONG": -40, "SHORT": 15 }
  },
  "opportunities": [
    {
      "asset": "HYPE",
      "direction": "SHORT",
      "leverage": 6,
      "finalScore": 253,
      "scoreDelta": 33,
      "scanStreak": 3,
      "hourlyTrend": "DOWN",
      "trendAligned": true,
      "pillarScores": {
        "smartMoney": 100,
        "marketStructure": 50,
        "technicals": 63,
        "funding": 40
      },
      "smartMoney": {
        "pnlPct": 20.7,
        "traders": 321,
        "accel": 15.0,
        "direction": "SHORT",
        "avgAtPeak": 75.0,
        "nearPeakPct": 11.0
      },
      "technicals": {
        "rsi1h": 38.8,
        "rsi15m": 27.1,
        "volRatio1h": 1.38,
        "volRatio15m": 2.46,
        "trend4h": "strong_down",
        "trendStrength": 45,
        "hourlyTrend": "DOWN",
        "patterns15m": ["three_crows"],
        "patterns1h": [],
        "momentum15m": -0.35,
        "divergence": null,
        "chg1h": -0.5,
        "chg4h": -1.8,
        "chg24h": -2.1,
        "support": 28.5,
        "resistance": 29.3,
        "atrPct": 1.85
      },
      "funding": {
        "rate": 0.0013,
        "annualized": 11.0,
        "favorable": true
      },
      "risks": ["oversold RSI"]
    }
  ],
  "disqualifiedAssets": [
    {
      "asset": "ETH",
      "direction": "LONG",
      "reason": "counter-trend on hourly (hourlyTrend=DOWN)",
      "wouldHaveScored": 195
    }
  ]
}
```

## v5 Output Additions

- `hourlyTrend` per opportunity (UP/DOWN/NEUTRAL)
- `trendAligned` boolean — quick check for agent
- `disqualified` count and `disqualifiedAssets` array
- `wouldHaveScored` on disqualified assets — useful for tuning
