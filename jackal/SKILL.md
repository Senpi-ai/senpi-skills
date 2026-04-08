---
name: jackal-strategy
description: >-
  JACKAL — First Jump pyramider. FOX v1.6's exact five-layer entry gauntlet combined with
  RHINO's pyramiding mechanic. Enter at 30% on a qualifying First Jump, add 40% at +10% ROE
  and final 30% at +20% ROE — but only after re-validating 4h trend, SM alignment, and volume.
  Failed scouts cost $15 instead of FOX's $50. Full pyramids capture the same upside.
  DSL High Water Mode (mandatory).
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  base_skill: fox
  type: trading-strategy
---

# JACKAL — First Jump Pyramider

FOX's proven scanner + RHINO's pyramiding. Enter small, add as it confirms.

**Requires:** FOX v1.6 must be installed first. JACKAL applies `config.json` overrides on top.

## How to Deploy

1. Install FOX v1.6 from the senpi-skills repo
2. Apply JACKAL's `config.json` overrides to the FOX config
3. Agent uses FOX's scanner with JACKAL's pyramid logic, entry filters, and DSL tiers

## MANDATORY: DSL High Water Mode

**JACKAL MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "tiers": [
    {"triggerPct": 7,  "lockPct": 40, "breachesRequired": 3},
    {"triggerPct": 12, "lockPct": 55, "breachesRequired": 2},
    {"triggerPct": 15, "lockPct": 75, "breachesRequired": 1},
    {"triggerPct": 20, "lockPct": 85, "breachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from `config.json` until engine per-tick fix ships.

## Entry: FOX v1.6 Five-Layer Gauntlet (Identical)

No changes from FOX v1.6:

1. First Jump — 10+ ranks from outside Top 25. Top-10 blocked.
2. Score ≥ 9, reasons ≥ 4, velocity > 0.10
3. Asset 4h/1h trend alignment — hard block
4. Leverage ≥ 7x
5. Time-of-day modifier (+1 during 04:00-14:00 UTC, -2 during 18:00-02:00 UTC)

## Pyramid: Three Stages

**Stage 1 — SCOUT (30% of max position):** Signal passes five-layer gauntlet. Enter with 30% of normal FOX margin.

**Stage 2 — CONFIRM (add 40% at +10% ROE):** Before adding, re-validate:
- Asset's 4h trend still intact (same direction)
- SM has not flipped against the position
- Volume still present (ratio ≥ 0.5x average)
If any check fails, do NOT add. The scout rides with DSL protection.

**Stage 3 — CONVICTION (add final 30% at +20% ROE):** Same re-validation as Stage 2.

**If the scout fails:** DSL stops it at the conviction-scaled floor. Loss is on 30% of max size — roughly $15 on a $1,000 account instead of FOX's $50.

## Notification Policy

ONLY alert:
- Stage 1 SCOUT opened (asset, direction, margin)
- Stage 2 CONFIRM added (asset, new total margin)
- Stage 3 CONVICTION added (asset, full position)
- Position CLOSED (total P&L across all stages)
- Risk triggered, critical error

NEVER alert: Scanner found nothing, DSL routine, pyramid evaluation, any reasoning.
All crons isolated. NO_REPLY for idle cycles.

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
