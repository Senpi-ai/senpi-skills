# Tiger Config Schema

All options in `tiger-config.json`. Defaults shown.

## Required

| Field | Type | Description |
|-------|------|-------------|
| `strategy_wallet` | string | Hyperliquid strategy wallet address |
| `budget` | float | Starting capital in USD |
| `target` | float | Profit target in USD |
| `deadline_days` | int | Timeframe in days |
| `start_time` | string | ISO timestamp of strategy start |
| `strategy_id` | string | Strategy UUID (from Senpi) |

## Position Limits

| Field | Default | Description |
|-------|---------|-------------|
| `max_slots` | 3 | Max concurrent positions |
| `max_leverage` | 10 | Maximum leverage per position |
| `min_leverage` | 5 | Min leverage an asset must support to be scanned |

## Risk Limits

| Field | Default | Description |
|-------|---------|-------------|
| `max_single_loss_pct` | 5.0 | Max loss on one position as % of balance |
| `max_daily_loss_pct` | 12.0 | Max daily loss as % of day-start balance |
| `max_drawdown_pct` | 20.0 | Max drawdown from peak balance |

## Scanner Thresholds

| Field | Default | Description |
|-------|---------|-------------|
| `min_bb_squeeze_percentile` | 35 | BB width below this percentile = squeeze |
| `min_oi_change_pct` | 5.0 | OI increase % to confirm accumulation |
| `rsi_overbought` | 75 | RSI level for overbought (reversion scanner) |
| `rsi_oversold` | 25 | RSI level for oversold (reversion scanner) |
| `min_funding_annualized_pct` | 30 | Min annualized funding rate for funding arb |
| `btc_correlation_move_pct` | 2.0 | BTC move % to trigger correlation lag scan |

## Aggression-Dependent

### `min_confluence_score`

Min weighted score for a signal to be actionable:

```json
{
  "CONSERVATIVE": 0.7,
  "NORMAL": 0.40,
  "ELEVATED": 0.4,
  "ABORT": 999
}
```

### `trailing_lock_pct`

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
| `telegram_chat_id` | null | Telegram chat ID for notifications |
