# 🦅 EAGLE — Macro Event / Correlation Break Detector

The sky scanner. Monitors BTC + top alts simultaneously for correlation breaks and macro events. When BTC dumps but one asset doesn't follow — that's either an independent catalyst or a catch-up trade. Also detects sudden cross-asset volume explosions.

## Architecture

| Script | Freq | Purpose |
|--------|------|---------|
| `eagle-scanner.py` | 3 min | Correlation break detection + macro event detection |
| DSL v5 (shared) | 3 min | Trailing stops |

## Edge

Correlation breaks are structural — they resolve within hours. Either the divergent asset catches up (mean reversion) or it's signaling independent strength (momentum). Both are tradeable.

## Setup

1. Set `EAGLE_WALLET` and `EAGLE_STRATEGY_ID` env vars (or fill `eagle-config.json`)
2. Create cron: scanner every 3 min + DSL v5 every 3 min
