---
name: cobra-strategy
description: >-
  COBRA — Triple convergence filter. Only enters when price momentum (5m + 15m + 1h all agreeing),
  volume confirmation (real buying pressure), and open interest growth (new money entering) all
  converge simultaneously. Most scans produce zero signals. When COBRA strikes, all three confirm.
  Conviction-scaled sizing, dynamic slots, DSL High Water Mode (mandatory).
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# COBRA — Triple Convergence

Strikes only when price, volume, and new money all agree. Most scans produce nothing. That's the point.

## What COBRA Does

Every 3 minutes, COBRA scans the top 25 assets by OI and checks three independent signals simultaneously:

1. **Multi-timeframe momentum** — 5m, 15m, and 1h must ALL agree on direction. If any disagrees, skip.
2. **Volume confirmation** — current bar volume must be ≥ 1.3x the 10-bar average. A move without volume is noise.
3. **Open interest growth** — OI must be trending up, meaning new money is entering. OI declining means the move is closing/liquidations, which reverses faster.

All three must pass. No exceptions. Optional boosters: SM alignment, funding direction, RSI room.

## MANDATORY: DSL High Water Mode

**COBRA MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
    {"triggerPct": 30, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from config until engine supports `pct_of_high_water`.

## Entry Requirements

| Signal | Requirement |
|---|---|
| 5m momentum | Must agree with direction |
| 15m momentum | ≥ 0.15% in direction |
| 1h momentum | Must agree with direction |
| Volume ratio (5m) | ≥ 1.3x average |
| OI trend | Growing (not collapsing) |
| SM direction | Hard block if opposing |
| RSI | Not overbought (< 75 long) or oversold (> 25 short) |
| Min score | 8 |

## DSL: Phase 1 + High Water Phase 2

| Setting | Value |
|---|---|
| Floor base | 1.5% notional |
| Time exits | All disabled |
| Phase 2 trigger | +7% ROE |
| Stagnation TP | 8% ROE stale 30 min |

## Conviction-Scaled Margin

| Score | Margin |
|---|---|
| 8-9 | 20% of account |
| 10-11 | 25% |
| 12+ | 30% |

## Dynamic Slots

Base 4 entries/day, unlocking to 8 on profitable days.

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 4 |
| Daily loss limit | 8% |
| Max drawdown | 20% |
| Max single loss | 5% |
| Cooldown | 45 min after 3 consecutive losses |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 3 min | isolated | Triple convergence scan |
| DSL v5 | 3 min | isolated | High Water Mode trailing |

## Notification Policy

**ONLY alert:** Position OPENED or CLOSED, risk triggered, critical error.
**NEVER alert:** Scanner found nothing, DSL routine, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Bootstrap Gate

Check `config/bootstrap-complete.json` every session. If missing: verify MCP, create scanner + DSL crons, write completion file, send: "🐍 COBRA is online. Scanning for triple convergence. Silence = no convergence."

## Files

| File | Purpose |
|---|---|
| `scripts/cobra-scanner.py` | Triple convergence scanner |
| `scripts/cobra_config.py` | Shared config, MCP helpers |
| `config/cobra-config.json` | All configurable variables |

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
