# Tiger Config Schema

All options in `tiger-config.json`. Defaults shown.
Canonical keys are camelCase. Snake_case inputs are accepted for backward compatibility.

## Required

| Field | Type | Description |
|-------|------|-------------|
| `strategyWallet` | string | Hyperliquid strategy wallet address |
| `budget` | float | Starting capital in USD |
| `target` | float | Profit target in USD |
| `deadlineDays` | int | Timeframe in days |
| `startTime` | string | ISO timestamp of strategy start |
| `strategyId` | string | Strategy UUID (from Senpi) |

## Position Limits

| Field | Default | Description |
|-------|---------|-------------|
| `maxSlots` | 3 | Max concurrent positions |
| `maxLeverage` | 10 | Maximum leverage per position |
| `minLeverage` | 5 | Min leverage an asset must support to be scanned |

## Risk Limits

| Field | Default | Description |
|-------|---------|-------------|
| `maxSingleLossPct` | 5.0 | Max loss on one position as % of balance |
| `maxDailyLossPct` | 12.0 | Max daily loss as % of day-start balance |
| `maxDrawdownPct` | 20.0 | Max drawdown from peak balance |

## Scanner Thresholds

| Field | Default | Description |
|-------|---------|-------------|
| `bbSqueezePercentile` | 35 | BB width below this percentile = squeeze |
| `minOiChangePct` | 5.0 | OI increase % to confirm accumulation |
| `rsiOverbought` | 75 | RSI level for overbought (reversion scanner) |
| `rsiOversold` | 25 | RSI level for oversold (reversion scanner) |
| `minFundingAnnualizedPct` | 30 | Min annualized funding rate for funding arb |
| `btcCorrelationMovePct` | 2.0 | BTC move % to trigger correlation lag scan |

## Aggression-Dependent

### `minConfluenceScore`

Min weighted score for a signal to be actionable:

```json
{
  "CONSERVATIVE": 0.7,
  "NORMAL": 0.40,
  "ELEVATED": 0.4,
  "ABORT": 999
}
```

### `trailingLockPct`

Fraction of peak ROE to lock as trailing stop floor:

```json
{
  "CONSERVATIVE": 0.80,
  "NORMAL": 0.60,
  "ELEVATED": 0.40,
  "ABORT": 0.90
}
```

## Optional

| Field | Default | Description |
|-------|---------|-------------|
| `telegramChatId` | null | Telegram chat ID for notifications |
