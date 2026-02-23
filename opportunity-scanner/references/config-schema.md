# Scanner Config Schema

All fields optional with sensible defaults.

```json
{
  "minVolume24h": 500000,
  "topNDeep": 15,
  "maxWorkers": 8,
  "macroModifiers": {
    "strongDownLong": -40,
    "strongDownShort": 15,
    "downLong": -20,
    "downShort": 10,
    "upLong": 10,
    "upShort": -20,
    "strongUpLong": 15,
    "strongUpShort": -40
  },
  "disableMacroFilter": false,
  "volatilityLeverage": {
    "highVolThreshold": 3.0,
    "highVolPenalty": 3,
    "medVolThreshold": 1.5,
    "medVolPenalty": 1
  },
  "scanHistorySize": 12,
  "hourlyTrendGate": true,
  "counterTrendHourlyPenalty": -30
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `minVolume24h` | 500000 | Stage 1 volume filter threshold |
| `topNDeep` | 15 | Number of assets to deep-dive in Stage 3 |
| `maxWorkers` | 8 | ThreadPoolExecutor workers for parallel candle fetches |
| `macroModifiers` | (see above) | BTC macro score adjustments per state/direction |
| `disableMacroFilter` | false | Set true to skip BTC macro modifier entirely |
| `volatilityLeverage` | (see above) | ATR-based leverage adjustment thresholds |
| `scanHistorySize` | 12 | Number of past scans to keep for cross-scan tracking |
| `hourlyTrendGate` | true | Hard disqualify counter-trend-on-hourly. **Do not disable.** |
| `counterTrendHourlyPenalty` | -30 | Score penalty for counter-trend on hourly (defense in depth) |
