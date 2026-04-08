---
name: rhino-strategy
description: >-
  RHINO — Momentum pyramider. Top 10 assets by OI + volume. Enters small (30% of max)
  on high-conviction convergence, then adds to winners at +10% ROE (40% more) and
  +20% ROE (final 30%). Thesis re-validated before every add — 4h trend intact, SM aligned,
  volume present. DSL High Water Mode trails the full position. The only skill in the zoo
  that builds into winners instead of entering full size and hoping.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# RHINO — Momentum Pyramider

Enter small. Add to winners. Build into conviction.

The only skill in the Senpi zoo that adds to winning positions. Every other skill enters full size once and lets DSL manage the exit. RHINO enters at 30% of max position, then pyramids in as the trade proves correct — adding at +10% ROE and +20% ROE if the thesis still holds.

## Why Pyramiding

Standard approach: risk 100% of position size at entry, when the thesis is unproven.

RHINO approach: risk 30% at entry, 70% only after the trade confirms.

If Stage 1 fails → you lose on 30% of max size (not 100%).
If it reaches Stage 3 → your full position is built across the move with a better average entry than all-in would give.

The math: on a $300 max position, RHINO risks $90 at entry. If it fails, you lose $90 × stop = ~$18. An all-in entry would lose $300 × stop = ~$60. Same signal, 3x less risk on the initial bet.

## MANDATORY: DSL High Water Mode

**RHINO MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 8,
  "tiers": [
    {"triggerPct": 8,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15, "lockHwPct": 45, "consecutiveBreachesRequired": 2},
    {"triggerPct": 25, "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 40, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 60, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from config until engine supports `pct_of_high_water`.

**DSL applies to the full position, not per-add.** Each pyramid add increases position size but the DSL state tracks one high-water mark for the entire position. After adding, the DSL floor recalculates based on the new total margin.

## The Three Stages

### Stage 1 — SCOUT (30% of max position)

**When:** Score 10+ convergence on a top-10 asset.

| Signal | Requirement |
|---|---|
| 4h trend structure | BULLISH or BEARISH (not neutral) |
| 1h trend | Must agree with 4h |
| 1h momentum | ≥ 0.3% in direction |
| SM alignment | Hard block if opposing |
| Min score | 10 |

**Size:** 30% of max margin. At $1,000 account with 30% max margin = $300 max position, scout enters with $90.

**Purpose:** Skin in the game. Prove the thesis with minimal risk.

### Stage 2 — CONFIRM (add 40% at +10% ROE)

**When:** Position is at +10% ROE AND thesis re-validated:
- 4h trend still intact (same direction)
- SM still aligned (not flipped)
- Volume still present (ratio ≥ 0.5x average)

**Size:** 40% of max margin. Adds $120 to the $90 scout. Position is now $210 (70% of max).

**Purpose:** The trade is working. Double down while the signal is confirmed.

### Stage 3 — CONVICTION (add final 30% at +20% ROE)

**When:** Position is at +20% ROE AND thesis re-validated (same checks as Stage 2).

**Size:** Final 30% of max margin. Adds $90. Position is now $300 (100% of max).

**Purpose:** The trade is running. Full conviction deployed. DSL High Water trails the full $300 from here.

### What If a Stage Doesn't Trigger?

Stage 2 and 3 are optional. If the trade reaches +10% ROE but the 4h trend has broken, the add doesn't happen — the scout position rides with DSL protection. If it never reaches +10%, the scout either hits its stop or trails out on DSL. No forced adds.

## Scan Priority

Every 3-minute scan, RHINO checks in this order:

1. **Add to existing winners** — do any positions qualify for Stage 2 or 3? If yes, add and return.
2. **Entry cap check** — any slots available?
3. **Scout new positions** — scan top 10 for new Stage 1 entries.

Adds take priority over new entries. A winning position that needs Stage 2 is more valuable than a new scout.

## Risk Management

| Rule | Value | Notes |
|---|---|---|
| Max positions | 3 | Each pyramids independently |
| Floor base | 2.5% notional | Wider — scout entries need room |
| Phase 2 trigger | +8% ROE | Before Stage 2 add happens |
| Stagnation TP | 15% ROE, 90 min | Patient — pyramided positions need time |
| Max single loss | 8% of account | At Stage 1, actual risk is 30% of this |
| Max consecutive losses | 2 → 90 min cooldown | |
| Drawdown halt | 20% | |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 3 min | isolated | Pyramid evaluation + new scouts |
| DSL v5 | 3 min | isolated | High Water Mode trailing |

## Notification Policy

**ONLY alert:**
- Stage 1 SCOUT opened (asset, direction, margin, score)
- Stage 2 CONFIRM added (asset, +ROE at add, new total margin)
- Stage 3 CONVICTION added (asset, +ROE at add, full position)
- Position CLOSED (DSL or structural, total P&L across all stages)
- Risk triggered, critical error

**NEVER alert:** Scanner found nothing, DSL routine, pyramid evaluation found no adds, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Bootstrap Gate

Check `config/bootstrap-complete.json` every session. If missing:
1. Verify Senpi MCP
2. Create scanner cron (3 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🦏 RHINO is online. Scouting top 10 by OI + volume. Enter small, add to winners. Silence = no conviction."

## Expected Behavior

| Metric | Expected |
|---|---|
| Scouts/day | 2-4 (score 10+ on top 10 assets) |
| Adds/day | 1-2 (only scouts that reach +10% ROE with intact thesis) |
| Full pyramids (Stage 3)/day | 0-1 (rare — the trade must run to +20% with thesis intact) |
| Win rate on scouts | ~50-55% |
| Win rate on full pyramids | ~70-80% (pre-selected by being up +20% with confirmed thesis) |
| Avg winner (scout only) | 10-20% ROE on 30% position |
| Avg winner (full pyramid) | 30-80%+ ROE on 100% position |
| Avg loser | -15 to -20% ROE on 30% position (scout stops) |
| Profit factor | Target 1.5-2.5 |

The edge: losers are small (30% position × stop). Winners are big (100% position × High Water trail). Asymmetric by design.

## Files

| File | Purpose |
|---|---|
| `scripts/rhino-scanner.py` | Thesis builder + pyramid evaluator + scout finder |
| `scripts/rhino_config.py` | Shared config, MCP helpers |
| `config/rhino-config.json` | All variables with pyramid stages + DSL High Water |

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
