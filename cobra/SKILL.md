---
name: cobra-strategy
description: >-
  COBRA v2.0 — Triple convergence filter. Only enters when price momentum (5m + 15m + 1h all agreeing),
  volume confirmation (1.8x+ above average), and open interest growth (new money entering) all
  converge simultaneously. Thesis re-evaluation on held positions — if any of the three signals break,
  exit. Score 10+. SM hard block. DSL High Water Mode (mandatory). 5-minute scan interval.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
---

# COBRA v2.0 — Triple Convergence

Strikes only when price, volume, and new money all agree. Exits when any of the three break.

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

| Signal | v1.0 | v2.0 | Why |
|---|---|---|---|
| 5m momentum | ≥ 0.3% | **≥ 0.5%** | Stronger confirmation required |
| 15m momentum | ≥ 0.15% | **≥ 0.25%** | |
| 1h momentum | Must agree | Must agree | Unchanged |
| Volume ratio (5m) | ≥ 1.3x | **≥ 1.8x** | Volume must be decisively above average, not marginal |
| OI trend | ≥ 2% | **≥ 5%** | New money must be clearly entering |
| SM direction | Hard block if opposing | Hard block if opposing | Unchanged |
| RSI | Not overbought/oversold | Not overbought/oversold | Unchanged |
| Min score | 8 | **10** | Only strong multi-signal convergence |

## Thesis Re-Evaluation (NEW in v2.0)

Every scan, COBRA checks held positions FIRST. The position holds as long as all three convergence signals are intact:

1. **Momentum convergence** — at least 2 of 3 timeframes (5m, 15m, 1h) must still agree with the direction. If 2+ flip against you, convergence is broken.
2. **Volume alive** — volume ratio must be ≥ 0.5x average. If volume dies below 0.5x, conviction has left.
3. **SM still aligned** — SM must not have flipped against the position.

If ANY of these break → thesis exit. The convergence that justified entry no longer exists.

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
