# Copy Trading Setup Guide

## Overview

Copy trading mirrors positions of top Hyperliquid traders via Senpi's strategy engine. Senpi handles all position mirroring, entry/exit timing, and margin management.

## Finding Traders to Copy

### Discovery Tools

```bash
# Top traders by weekly PnL
mcporter call senpi.discovery_get_top_traders time_frame=WEEKLY sort_by=PROFIT_AND_LOSS

# Top traders by consistency (gain-to-pain ratio)
mcporter call senpi.discovery_get_top_traders time_frame=WEEKLY sort_by=GAIN_TO_PAIN_RATIO

# Top performing strategies on the platform
mcporter call senpi.discovery_get_top_strategies limit=20 force_fetch=true

# Check a trader's current positions
mcporter call senpi.discovery_get_trader_state trader_address=0x... latest=true

# Check a trader's trade history
mcporter call senpi.discovery_get_trader_history trader_address=0x... limit=20
```

### Selection Criteria

| Metric | Good | Great |
|--------|------|-------|
| Win Rate | > 70% | > 90% |
| Max Drawdown | < -20% | < -5% |
| Gain-to-Pain Ratio | > 2.0 | > 10.0 |
| Weekly ROI | > 5% | > 20% |
| Active Positions | > 0 | 3-10 |

**Red flags:**
- 100% win rate with high drawdown (holding losers, never closing)
- Tiny funded amount with insane ROI (survivorship bias)
- Very high leverage (40x+) — one bad trade wipes the copy

## Deploying a Copy Strategy

```bash
# Create copy strategy
mcporter call senpi.strategy_create \
  traderAddress=0x... \
  initialBudget=500 \
  mirrorMultiplier=1 \
  slippage=1 \
  stopLossPercentage=25  # Optional: hard SL as % of budget

# Check strategy status (poll until ACTIVE)
mcporter call senpi.strategy_get strategy_id=UUID

# Check if positions are mirrored
mcporter call senpi.strategy_get_clearinghouse_state strategy_wallet=0x...
```

### Parameters

- **initialBudget**: USDC to allocate (auto-bridged from embedded wallet)
- **mirrorMultiplier**: 1.0 = proportional mirroring, 0.5 = half size, 2.0 = double
- **slippage**: Max slippage % for mirror entries (1-2 recommended)
- **stopLossPercentage**: Hard stop as % of budget (optional safety net)

## Current Deployment (as of 2026-03-08)

| Strategy | Trader | Budget | WR | GP Ratio | Stop Loss |
|----------|--------|--------|-----|----------|-----------|
| Copy#1 "The Consistent" | 0xaea8...e59b | $500 | 99.3% | 147.6 | None |
| Copy#2 "The Diversified" | 0xd6e5...5b42 | $400 | 91% | 100.2 | None |
| Copy#3 "The Bear" | 0x418a...8888 | $300 | 100% | — | 25% |

Total deployed: $1,200 / $2,540 (47%)

## Monitoring

Use the Copy Trading Monitor cron (15min) to track all copy strategies. See `cron-architecture.md` for the full cron definition.

## Managing Strategies

```bash
# Pause a strategy (stops new mirrors, keeps existing positions)
mcporter call senpi.strategy_pause strategyId=UUID

# Close all positions in a strategy
mcporter call senpi.strategy_close_positions strategyId=UUID

# Close strategy entirely (returns funds to embedded wallet)
mcporter call senpi.strategy_close strategyId=UUID

# Top up a strategy with more funds
mcporter call senpi.strategy_top_up strategyId=UUID amount=200
```
