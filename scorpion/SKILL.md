---
name: scorpion-strategy
description: >-
  SCORPION — Whale wallet tracker. Discovers top traders from the leaderboard, monitors their
  positions, mirrors entries when 2+ whales align on the same trade. 10-minute persistence filter.
  Per-whale performance tracking with automatic demotion. The sting: when the whale exits,
  SCORPION exits immediately. Conviction-scaled margin. DSL High Water Mode (mandatory).
license: MIT
metadata:
  author: jason-goldberg
  version: "1.1"
  platform: senpi
  exchange: hyperliquid
---

# SCORPION — Whale Wallet Tracker

Tracks the best traders. Mirrors their entries. Exits the instant they do. The sting.

## What SCORPION Does

Every 5 minutes, SCORPION:
1. Discovers top-performing traders from Hyperliquid's leaderboard (min 55% WR, 10+ trades)
2. Monitors their actual positions
3. Mirrors entries when 2+ whales align on the same asset and direction
4. Waits 10 minutes before mirroring (filters noise and hedges)
5. When the primary whale exits → SCORPION exits immediately regardless of P&L

The only skill in the zoo that outsources its trading thesis to someone else's edge.

## MANDATORY: DSL High Water Mode

**SCORPION MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 35, "consecutiveBreachesRequired": 3},
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
| Whale count aligned | ≥ 2 on same asset + direction |
| Hold time | ≥ 10 min (persistence filter) |
| Whale min WR | 55% |
| Whale min trades | 10 |
| Max tracked whales | 10 |
| OI (USD-weighted) | ≥ $5M |
| Banned prefixes | xyz: |

## Whale Performance Tracking

SCORPION tracks per-whale results and automatically demotes underperformers:

| Trigger | Action |
|---|---|
| 3 consecutive losses from a whale | Demote — stop following for 48h |
| Net negative P&L after 5 trades | Demote |
| Win rate < 40% | Demote |

## Profit Taking

| Trigger | Action |
|---|---|
| +15% ROE | Close 25% (FEE_OPTIMIZED_LIMIT) |
| +40% ROE | Close 50% |
| Whale still holding after 8h | Close 50% regardless |
| Whale exits | **Close 100% immediately** (the sting) |

## DSL Configuration

| Setting | Value |
|---|---|
| Floor base | 2% notional |
| Time exits | All disabled |
| Phase 2 trigger | +7% ROE |
| Stagnation TP | 8% ROE stale 45 min |

## Conviction-Scaled Margin

| Whale count | Margin |
|---|---|
| 2 | 25% of account (base) |
| 3 | 31% |
| 4+ | 37% |

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 8 (base 4, dynamic to 8) |
| Daily loss limit | 8% |
| Max drawdown | 20% |
| Max single loss | 5% |
| Drawdown halt | 25% from peak |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 5 min | isolated | Whale discovery + consensus + persistence + exit tracking |
| DSL v5 | 3 min | isolated | High Water Mode trailing |

## Notification Policy

**ONLY alert:** Position OPENED (asset, direction, whale count, hold time), position CLOSED (whale exit or DSL), risk triggered, critical error.
**NEVER alert:** Scanner found nothing, whale tracking updates, DSL routine, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Bootstrap Gate

Check `config/bootstrap-complete.json` every session. If missing: verify MCP, create scanner + DSL crons, write completion file, send: "🦂 SCORPION is online. Tracking whales. Silence = no consensus."

## Optional: Trading Strategy Variant

| Strategy | What Changes | When To Consider |
|---|---|---|
| **MANTIS** | 4+ whales, 30-min aging, whale quality scoring, volume confirmation, regime awareness | After seeing SCORPION enter noise trades — MANTIS is the high-conviction evolution |

Start with vanilla SCORPION. MANTIS is for users who want fewer, higher-quality whale mirrors.

## Files

| File | Purpose |
|---|---|
| `scripts/scorpion-scanner.py` | Whale discovery + consensus + persistence + exit tracking |
| `scripts/scorpion_config.py` | Shared config, MCP helpers |
| `config/scorpion-config.json` | All configurable variables with DSL High Water + legacy fallback |

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
