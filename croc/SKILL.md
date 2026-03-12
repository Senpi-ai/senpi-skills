---
name: croc-strategy
description: >-
  CROC — Funding rate arbitrage. Scans all assets for extreme funding rates and enters against
  the crowd to collect funding payments while positioning for the mean-reversion snap.
  Min 20% annualized funding. Trend confirmation optional. DSL High Water Mode (mandatory).
  15-minute scan interval. The patient ambush predator.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# CROC — Funding Rate Arbitrage

Still waters. Collects funding payments while waiting for the snap.

## What CROC Does

Every 15 minutes, CROC scans all assets for extreme funding rates. When funding is deeply positive (crowd is aggressively long), CROC goes short to collect the funding payments. When funding is deeply negative (crowd is aggressively short), CROC goes long. The edge is twofold: collect funding income hourly while positioning for the inevitable mean-reversion snap when the crowd unwinds.

## MANDATORY: DSL High Water Mode

**CROC MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 8,
  "tiers": [
    {"triggerPct": 8,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15, "lockHwPct": 45, "consecutiveBreachesRequired": 2},
    {"triggerPct": 25, "lockHwPct": 65, "consecutiveBreachesRequired": 2},
    {"triggerPct": 40, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from config until engine supports `pct_of_high_water`.

**IMPORTANT: Do NOT use momentum DSL rules on funding arb trades.** Momentum rules (15-min weak peak, 30-min hard timeout) will kill funding trades that need hours to play out. All time exits must be disabled. Funding trades exit on structural invalidation (funding flips) or DSL trailing, not clocks.

## Entry Requirements

| Signal | Requirement |
|---|---|
| Funding annualized | ≥ 20% (GATOR variant uses 120%) |
| Min score | 4 |
| OI (USD-weighted) | ≥ $5M |
| Trend confirmation | Optional (booster, not required) |

## DSL Configuration

| Setting | Value |
|---|---|
| Floor base | 2% notional |
| Time exits | **All disabled** — funding trades need hours |
| Phase 2 trigger | +8% ROE (slower than momentum) |
| Stagnation TP | 8% ROE stale 60 min |

Phase 1 wider than momentum skills because funding arb absorbs larger drawdowns subsidized by yield.

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 6 |
| Daily loss limit | 8% |
| Max drawdown | 20% |
| Max single loss | 5% |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 15 min | isolated | Funding rate scan |
| DSL v5 | 3 min | isolated | High Water Mode trailing |

## Notification Policy

**ONLY alert:** Position OPENED (asset, direction, funding rate, annualized %), position CLOSED, risk triggered, critical error.
**NEVER alert:** Scanner found nothing, DSL routine, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Bootstrap Gate

Check `config/bootstrap-complete.json` every session. If missing: verify MCP, create scanner + DSL crons, write completion file, send: "🐊 CROC is online. Scanning for extreme funding. Silence = no extremes."

## Optional: Trading Strategy Variant

| Strategy | What Changes | When To Consider |
|---|---|---|
| **GATOR** | 120% min funding, structural thesis exits (funding flip = exit), no time exits, funding income tracking | When you want only absolute extremes and structural exits instead of any time-based logic |

Start with vanilla CROC for broader funding opportunities. GATOR is for maximum patience on maximum extremes.

## Files

| File | Purpose |
|---|---|
| `scripts/croc-scanner.py` | Funding rate scanner |
| `scripts/croc_config.py` | Shared config, MCP helpers |
| `config/croc-config.json` | All configurable variables |

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
