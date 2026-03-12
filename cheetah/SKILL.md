---
name: cheetah-strategy
description: >-
  CHEETAH — HYPE alpha hunter. Single-asset focus at 5-10x leverage. Reads every HYPE signal
  (SM positioning, funding, OI, 4-timeframe trend, volume, BTC macro correlation) to build
  highest-conviction entries. BTC macro as confidence booster (not a gate — HYPE has independent catalysts). Thesis-based
  re-evaluation exits. DSL High Water Mode (mandatory) with HYPE-specific extra-wide tiers.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# CHEETAH — HYPE Alpha Hunter

One asset. Every signal. 5-10x leverage. HYPE runs on its own narrative.

CHEETAH stares at HYPE and nothing else. Every signal source — SM positioning, funding rate, OI, 4-timeframe trend, volume, BTC macro direction — feeds into a single thesis. BTC alignment boosts confidence but is not required — HYPE has independent catalysts (protocol revenue, vault flows, ecosystem growth) and can trend against BTC.

**maxPositions: 1.** One HYPE trade at a time.

## MANDATORY: DSL High Water Mode

**CHEETAH MUST use DSL High Water Mode. This is not optional. Do not substitute standard DSL tiers.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files for any CHEETAH position, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 8,
  "tiers": [
    {"triggerPct": 8,   "lockHwPct": 15, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15,  "lockHwPct": 35, "consecutiveBreachesRequired": 3},
    {"triggerPct": 25,  "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 40,  "lockHwPct": 70, "consecutiveBreachesRequired": 1},
    {"triggerPct": 60,  "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**If `tiers` or `lockMode` is missing from the state file, the DSL engine falls back to flat 1.5% retrace and High Water Mode is silently disabled. Always verify.**

**FALLBACK:** Use `tiersLegacyFallback` from config until engine supports `pct_of_high_water`.

## Why HYPE-Only

HYPE is Hyperliquid's native token — highest community attention, strongest narrative momentum, and extreme funding rate swings. Compared to BTC:

- **More volatile** — bigger moves (5-15% days are common), but sharper reversals
- **Lower max leverage** — 10x max vs BTC's 20x
- **Funding goes extreme faster** — crowded longs/shorts build quickly, reversals are violent
- **Independent narrative** — can trend with or against BTC. Protocol revenue, vault flows, ecosystem growth, and token burns drive HYPE-specific moves
- **Fewer whales, stronger SM signal** — each whale's positioning matters more

## How CHEETAH Trades

### Entry (score ≥ 9)

| Signal | Points | Required? |
|---|---|---|
| 4h trend structure | 3 | **Yes** |
| 1h trend agrees | 2 | **Yes** |
| BTC macro alignment | 0-2 | No (booster +2 if aligned, -1 if opposing) |
| 15m momentum confirms | 0-1 | **Yes** |
| 5m alignment (4TF agree) | 1 | No |
| SM aligned | 2-3 | **Hard block if opposing** |
| Funding pays to hold | 2 | No |
| Volume above average | 1-2 | No |
| OI growing | 1 | No |
| RSI has room | 1 | No |
| 4h momentum strength | 1 | No |

### Conviction-Scaled Leverage

| Score | Leverage |
|---|---|
| 9-11 | 7x |
| 12-13 | 8x |
| 14+ | 10x |

### Thesis Re-Evaluation (every 3 min)

Holds as long as: 4h trend intact, SM not flipped, funding not extreme, volume not dead. BTC opposing alone does not invalidate — HYPE has its own catalysts. ANY break = thesis exit.

### DSL Tiers (HYPE-specific — wider than GRIZZLY)

| Tier | Trigger ROE | Lock (% of HW) | Breaches |
|---|---|---|---|
| 1 | 8% | 15% | 3 |
| 2 | 15% | 35% | 3 |
| 3 | 25% | 55% | 2 |
| 4 | 40% | 70% | 1 |
| 5 | 60%+ | 85% | 1 |

Wider early tiers than GRIZZLY because HYPE wicks 5-10% ROE routinely during trends. Phase 1 floor: 5% notional. Stagnation TP: 15% ROE stale 60min.

## Notification Policy

**ONLY alert:** HYPE position OPENED or CLOSED, risk guardian triggered, critical error.
**NEVER alert:** Scanner found nothing, thesis re-eval passed, DSL routine, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Bootstrap Gate

Check `config/bootstrap-complete.json` every session. If missing: verify MCP, create scanner cron (3min isolated) + DSL cron (3min isolated), write completion file, send: "🐆 CHEETAH is online. Watching HYPE. DSL High Water active. Silence = no conviction."

## Files

| File | Purpose |
|---|---|
| `scripts/cheetah-scanner.py` | HYPE thesis builder + re-evaluator |
| `scripts/cheetah_config.py` | Shared config, MCP helpers |
| `config/cheetah-config.json` | All variables with HW tiers + legacy fallback |

## License

MIT — Built by Senpi (https://senpi.ai). 
Source: https://github.com/Senpi-ai/senpi-skills
