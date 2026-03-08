# 🐆 PANTHER — Breakout Scalper

The speed predator. Pure breakout detection on 5-minute timeframes. Bollinger Band squeeze → expansion with volume confirmation. Tightest stops in the zoo (6% ROE floor), fastest timeout (20 min). If the breakout doesn't follow through immediately, cut.

## Architecture

| Script | Freq | Purpose |
|--------|------|---------|
| `panther-scanner.py` | 2 min | Scan 30 assets for BB squeeze → breakout + volume spike |
| DSL v5 (shared) | 3 min | Trailing stops |

## Edge

BB squeezes that resolve with volume have a 60%+ directional success rate on the first candle. High frequency (8-12 trades/day), small wins, very fast cuts on losers.

## Setup

1. Set `PANTHER_WALLET` and `PANTHER_STRATEGY_ID` env vars (or fill `panther-config.json`)
2. Create cron: scanner every 2 min + DSL v5 every 3 min
