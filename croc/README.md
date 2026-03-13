# 🐊 CROC — Funding Rate Arbitrage

The calmest predator in the zoo. Scans all assets for extreme funding rates, enters against the funding direction to collect the rate while positioning for the mean-reversion snap. Long when funding is deeply negative (shorts pay you to hold), short when funding deeply positive.

## Architecture

| Script | Freq | Purpose |
|--------|------|---------|
| `croc-scanner.py` | 15 min | Scan funding rates, score extremity, enter against funding |
| DSL v5.3.1 (shared) | 3 min | Trailing stops |

## Edge

Funding payments are guaranteed income. Extreme funding historically reverts within 4-12 hours. Low trade frequency (2-4/day), long hold times, funding income subsidizes the DSL floor.

## Setup

1. Set `CROC_WALLET` and `CROC_STRATEGY_ID` env vars (or fill `croc-config.json`)
2. Create cron: scanner every 15 min + DSL v5.3.1 every 3 min
3. Uses shared DSL v5.3.1 skill for trailing stops
