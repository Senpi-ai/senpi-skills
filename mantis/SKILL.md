---
name: mantis-strategy
description: >-
  MANTIS — High-conviction whale mirror. Evolved from SCORPION's live trading lessons.
  4+ whale consensus required (not 2). 30-minute aging filter (not 10). Volume confirmation.
  Whale quality weighting by win rate and P&L. BTC regime awareness. Wide Phase 1 DSL (5% retrace).
  Immediate DSL arming — no naked positions. DSL High Water Mode (mandatory).
  The praying mantis: perfectly still, strikes only when the kill is certain.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# MANTIS — High-Conviction Whale Mirror

Evolved from SCORPION. Same thesis (mirror the whales), completely different execution.

SCORPION's live trading revealed three failure modes: entering on 2-whale consensus (noise), mirroring after 10 minutes (scalp chasing), and tight Phase 1 stops (chopped out of correct trades). MANTIS fixes all three and adds volume confirmation, whale quality scoring, and regime awareness.

The praying mantis: perfectly still, watching everything, strikes only when the kill is certain.

## What Changed vs SCORPION

| Problem | SCORPION | MANTIS | Result |
|---|---|---|---|
| Too many low-conviction entries | 2 whales to enter | **4 whales minimum** | Eliminates noise trades |
| Mirroring scalps | 10 min aging filter | **30 min aging filter** | Only mirrors conviction holds |
| Chopped out of winners | 3% Phase 1 retrace | **5% Phase 1 retrace** | Survives normal volatility |
| No whale quality filter | All whales equal | **Quality scoring (WR, P&L, trade count)** | Better whales = higher score |
| No volume check | Mirror into anything | **Volume confirmation required** | Won't enter dead markets |
| No regime awareness | Ignores BTC macro | **Regime penalty** | Penalizes counter-trend mirrors |
| DSL sometimes missing | Manual DSL creation | **Immediate arming mandatory** | Zero naked positions |

## MANDATORY: DSL High Water Mode

**MANTIS MUST use DSL High Water Mode. This is not optional.**

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
    {"triggerPct": 40, "lockHwPct": 80, "consecutiveBreachesRequired": 1},
    {"triggerPct": 60, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from config until engine supports `pct_of_high_water`.

**CRITICAL: Run `dsl-cli.py add-dsl` IMMEDIATELY after every entry fill. No position is allowed to exist without an active DSL state file. Zero exceptions.**

## How MANTIS Trades

### Whale Discovery

Every scan, MANTIS discovers top 30 traders from the leaderboard and scores them by quality:

| Metric | Points |
|---|---|
| Win rate ≥ 70% | 3 |
| Win rate ≥ 60% | 2 |
| Win rate ≥ 55% | 1 |
| P&L > $50K | 3 |
| P&L > $10K | 2 |
| P&L > $1K | 1 |
| 100+ trades | 1 |

Minimum to track: 55% win rate, 15+ trades. Max 15 whales tracked.

### Entry (5-gate filter)

| Gate | Requirement | Why |
|---|---|---|
| 1. Consensus | 4+ whales aligned on same asset/direction | Eliminates noise |
| 2. Persistence | Consensus held 30+ minutes | Filters scalps |
| 3. Volume | Current volume ≥ 50% of 6h average | Don't mirror into dead markets |
| 4. Score | Combined score ≥ 12 (whales × 2 + quality + persistence + volume + regime) | High conviction only |
| 5. DSL arming | `dsl-cli.py add-dsl` IMMEDIATELY after fill | No naked positions |

### Scoring

| Component | Points |
|---|---|
| Whale count × 2 | 8-12+ (4-6 whales) |
| Total whale quality | 4-20+ (sum of individual scores) |
| Persistence bonus | +1 per 30min held, max +3 |
| Volume bonus | +1 if volume > 1.5x average |
| Regime penalty | -2 if counter-trend to BTC macro |

Minimum score to enter: 12. In practice, 4 high-quality whales holding for 30+ minutes with volume typically scores 16-20+.

### Hold

The sting: when the primary whale exits, MANTIS exits immediately — regardless of P&L or DSL state. This is SCORPION's best feature, preserved in MANTIS.

### DSL: Wide Phase 1, High Water Phase 2

| Setting | SCORPION | MANTIS |
|---|---|---|
| Phase 1 retrace | 3% | **5%** |
| Phase 2 trigger | 7% ROE | **8% ROE** |
| T1 lock | 35% HW | **25% HW** (wider — whale timing is uncertain) |
| 85% trail at | +30% ROE | **+60% ROE** |
| Stagnation TP | 5% ROE, 30min | **10% ROE, 60min** |
| Floor | 1.5% notional | **3% notional** |

## Notification Policy

**ONLY alert:** Position OPENED (asset, direction, whale count, quality, hold time), position CLOSED (DSL or whale exit with reason), risk triggered, critical error.
**NEVER alert:** Scanner found nothing, whale tracking updates, DSL routine, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Bootstrap Gate

Check `config/bootstrap-complete.json` every session. If missing:
1. Verify Senpi MCP
2. Create scanner cron (5 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🦗 MANTIS is online. Tracking whales. 4+ consensus at 30min+ to strike. Silence = no conviction."

## Expected Behavior

| Metric | SCORPION | MANTIS (expected) |
|---|---|---|
| Trades/day | 3-6 | **1-3** (4 whale gate filters most) |
| Win rate | ~40-45% | **~55-60%** (higher conviction entries) |
| Avg winner | 10-25% ROE | **20-50%+ ROE** (wider DSL, High Water) |
| Avg loser | -8 to -15% ROE | **-15 to -25% ROE** (wider floor) |
| Profit factor | < 1.0 (losing) | **Target 1.3-1.8** |

Fewer trades, higher win rate, bigger winners. The same math as every other upgrade in the zoo: fewer trades, higher conviction, wider stops.

## Files

| File | Purpose |
|---|---|
| `scripts/mantis-scanner.py` | Whale discovery + consensus + volume + regime + persistence |
| `scripts/mantis_config.py` | Shared config, MCP helpers |
| `config/mantis-config.json` | All variables with DSL High Water + legacy fallback |

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
Source: https://github.com/Senpi-ai/senpi-skills
