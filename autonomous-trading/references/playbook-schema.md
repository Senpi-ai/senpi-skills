## Step 4: Create the Playbook File

Save `auto-strategy.json` to track state:

```json
{
  "strategyId": "<from step 3>",
  "wallet": "<strategy wallet address>",
  "initialBudget": 3000,
  "target": 7000,
  "startDate": "2026-02-23",
  "deadline": "2026-02-26",
  "riskProfile": "moderate",
  "rules": {
    "maxConcurrentPositions": 3,
    "maxSingleAllocation": 1500,
    "maxLeverage": 7,
    "dailyLossLimit": -300,
    "totalDrawdownCap": -750,
    "minScannerScore": 175,
    "minRiskReward": 2,
    "cooldownAfterConsecutiveLosses": 2,
    "autonomousExecution": false,
    "maxNetExposurePct": 150,
    "concentrationMode": true,
    "maxLeverageFile": "max-leverage.json",
    "flipRules": {
      "enabled": true,
      "minConviction": 4,
      "minSmTraders": 200,
      "noReflipWithinMinutes": 30,
      "maxFlipCostPctOfMargin": 3,
      "requireHourlyTrendAlignment": true,
      "checkIntervalMin": 5,
      "convictionCollapseInstantCut": true
    },
    "schedule": {
      "highActivity": { "hours": [13,14,15,16,17,18,19,20], "scanInterval": 10 },
      "mediumActivity": { "hours": [6,7,8,9,10,11,12,21,22], "scanInterval": 15 },
      "lowActivity": { "hours": [0,1,2,3,4,5,23], "scanInterval": 30 }
    }
  },
  "trades": [],
  "activeTrades": [],
  "flipHistory": [],
  "dailyPnL": {},
  "totalRealizedPnL": 0
}
```

### Trade Journaling

When logging a trade to `trades[]`, include the full scanner snapshot that triggered it:

```json
{
  "tradeId": "trade-001",
  "asset": "ETH",
  "direction": "SHORT",
  "entry": 1955.2,
  "leverage": 7,
  "leverageType": "CROSS",
  "margin": 500,
  "notional": 3500,
  "openedAt": "2026-02-23T15:22:00Z",
  "hourlyTrend": "down",
  "maxLeverageAvailable": 25,
  "scannerSnapshot": {
    "finalScore": 213,
    "scoreDelta": 33,
    "scanStreak": 3,
    "pillarScores": {
      "smartMoney": 100,
      "marketStructure": 50,
      "technicals": 63,
      "funding": 40
    },
    "risks": ["oversold RSI"],
    "btcContext": { "trend": "down", "chg1h": -1.2 },
    "atrPct": 1.85
  },
  "closedAt": null,
  "exitPrice": null,
  "realizedPnl": null,
  "closeReason": null,
  "closedByJob": null,
  "holdDurationMin": null
}
```

When a trade closes, update the entry with exit data **and record which job closed it** (`closedByJob`). After 50+ trades, you can analyze what pillar scores, trend alignments, and risk flags predict winners vs losers.

---

