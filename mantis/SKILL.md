---
name: mantis-strategy
description: >-
  MANTIS v3.0 — Dual-mode emerging movers scanner. All live trading lessons
  applied, plus one experimental tweak: contribution acceleration threshold
  raised from 0.001 to 0.003 and the weak +1 tier (CONTRIB_POSITIVE) eliminated.
  Only genuine SM acceleration earns contribution points. Stalker minScore 7,
  minTotalClimb 8, tighter Phase 1 for low-score entries, consecutive-loss
  streak gate. XYZ banned. Leverage 7-10x. DSL state template in scanner output.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  config_source: mantis-v2-plus-contrib-threshold-experiment
---

# 🦗 MANTIS v3.0 — Dual-Mode Scanner + Contribution Threshold Experiment

Patient. Precise. Only strikes when SM acceleration is real.

---

## ⛔ CRITICAL AGENT RULES — READ BEFORE ANYTHING ELSE

### RULE 1: Install path is `/data/workspace/skills/mantis-strategy/`

The skill MUST be installed to exactly this path.

### RULE 2: MAX 3 POSITIONS — check before EVERY entry

Before opening ANY position, call `strategy_get_clearinghouse_state` and count open positions. If positions >= 3, SKIP.

### RULE 3: Scanner output is AUTHORITATIVE — never override from memory

The scanner is the single source of truth for all trading parameters.

### RULE 4: Verify BOTH crons on every session start

Run `openclaw crons list`. Scanner cron and DSL cron must both be `status: ok`.

### RULE 5: Write dslState directly — do not construct manually

Write the scanner's `dslState` block DIRECTLY to `state/{TOKEN}.json`. Do not modify.

### RULE 6: Never retry timed-out position creation

If `create_position` times out, check clearinghouse state first.

### RULE 7: Never modify your own configuration

No adjustments to leverage, margin, scoring, DSL, or any parameter.

### RULE 8: Record Stalker results for streak tracking

After every Stalker position closes, call `record_stalker_result(tc, is_win)`.

---

## The MANTIS v3.0 Experiment

**Hypothesis:** Fox v1.0's weak Stalker trades often carried the CONTRIB_POSITIVE reason — a +1 score bonus for contribution velocity between 0 and 0.001 per scan. This is technically "positive" but so weak it's effectively noise. A contribution delta of 0.0005 means SM interest grew by 0.05% per scan — statistically indistinguishable from random fluctuation. Meanwhile, the +2 CONTRIB_ACCEL signal (delta > 0.001) correlated with actual winning trades where SM was genuinely building.

**The tweak:** Contribution acceleration threshold raised from 0.001 to 0.003. The +1 tier (CONTRIB_POSITIVE: delta between 0 and threshold) is eliminated entirely. This is the ONLY difference from the base scanner.

**What this changes in scoring:**
- Old: delta 0.0005 → +1 (CONTRIB_POSITIVE). New: delta 0.0005 → +0 (ignored)
- Old: delta 0.0015 → +2 (CONTRIB_ACCEL). New: delta 0.0015 → +0 (below new threshold)
- Old: delta 0.004 → +2 (CONTRIB_ACCEL). New: delta 0.004 → +2 (CONTRIB_ACCEL, passes)

**Expected effect:** Stalker max theoretical score drops from +8 to +7 for assets with weak acceleration. Only assets with strong SM momentum (delta > 0.003) get the +2 contribution bonus. This should eliminate the "barely climbing with barely growing interest" chop trades while preserving entries where SM is genuinely accelerating.

---

## Dual-Mode Entry

### MODE A — STALKER (Accumulation) — Score >= 7
- SM rank climbing steadily over 3+ consecutive scans
- Total climb >= 8 ranks
- Contribution building each scan
- 4H trend aligned
- **v3.0: Contribution acceleration must exceed 0.003 for +2 bonus. No +1 tier.**
- **Streak gate:** 3 consecutive Stalker losses → minScore raised to 9

### MODE B — STRIKER (Explosion) — Score >= 9, min 4 reasons
- FIRST_JUMP or IMMEDIATE_MOVER (10+ rank jump from #25+)
- Rank jump >= 15 OR velocity > 15
- Raw volume >= 1.5x of 6h average
- Unchanged

---

## MANDATORY: DSL High Water Mode

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

### Phase 1 (Conviction-Scaled)

| Score | Absolute Floor | Hard Timeout | Weak Peak | Dead Weight |
|---|---|---|---|---|
| 6-7 | -18% ROE | 25 min | 12 min | 8 min |
| 8-9 | -25% ROE | 45 min | 20 min | 15 min |
| 10+ | -30% ROE | 60 min | 30 min | 20 min |

### Stagnation TP (MANDATORY)

If ROE >= 10% and high water hasn't moved for 45 minutes, take profit.

---

## Scanner Output — DSL State Template

Each signal includes a `dslState` block. Write this directly as the state file.

**Entry flow:**
1. Scanner outputs signal with `dslState` block
2. Verify positions < 3
3. Verify exchange max leverage >= 7
4. Call `create_position`
5. Write `state/{TOKEN}.json` with exact `signal.dslState` plus `entryPrice`, `leverage`, `createdAt`
6. Send ONE notification: position opened
7. DSL cron picks up the state file

**On position close:**
8. Call `record_stalker_result(tc, is_win)` if Stalker entry

---

## Cron Setup

**EXACT commands — copy-paste. Do not modify.**

Scanner cron (90 seconds, main session):
```
python3 /data/workspace/skills/mantis-strategy/scripts/mantis-scanner.py
```

DSL cron (3 minutes, isolated session):
```
python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py --state-dir /data/workspace/skills/mantis-strategy/state
```

---

## Bootstrap Gate

On EVERY session start, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (90s, main) and DSL cron (3 min, isolated)
3. Verify BOTH crons `status: ok`
4. Write `config/bootstrap-complete.json`
5. Send: "🦗 MANTIS v3.0 online. Contrib threshold 0.003, no weak tier. Silence = no conviction."

---

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 6 |
| Leverage | 7-10x |
| Daily loss limit | 10% |
| Per-asset cooldown | 2 hours |
| Stagnation TP | 10% ROE / 45 min |
| XYZ equities | Banned |
| Contrib accel threshold | 0.003 (experiment, was 0.001) |
| Stalker streak gate | 3 losses → minScore 9 |

---

## Notification Policy

**ONLY alert:** Position OPENED, position CLOSED (P&L + reason), streak gate activated/deactivated, critical error.

**NEVER alert:** Scanner found nothing, DSL routine, any reasoning.

---

## Files

| File | Purpose |
|---|---|
| `scripts/mantis-scanner.py` | Dual-mode scanner with contrib threshold experiment |
| `scripts/mantis_config.py` | Config helper with stalkerResults tracking |
| `config/mantis-config.json` | Config with Mantis v3.0 thresholds |

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
