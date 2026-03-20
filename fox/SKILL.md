---
name: fox-strategy
description: >-
  FOX v2.0 — Dual-mode emerging movers scanner. All live trading lessons from
  Fox v1.0 applied, plus one experimental tweak: Stalker minReasons = 3.
  Stalker entries must have at least 3 distinct scoring reasons, not just
  pass the score threshold. This forces breadth of confirmation beyond the
  auto-awarded base climb score + one bonus. Stalker minScore 7, minTotalClimb 8,
  tighter Phase 1 for low-score entries, consecutive-loss streak gate. XYZ banned.
  Leverage 7-10x. DSL state template in scanner output.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  config_source: fox-v1-trade-data-plus-min-reasons-experiment
---

# 🦊 FOX v2.0 — Dual-Mode Scanner + minReasons Experiment

Fox reborn. Every lesson from v1.0's 20 closed positions baked in, plus one experimental tweak to test whether requiring breadth of confirmation reduces the weak-peak bleed.

---

## ⛔ CRITICAL AGENT RULES — READ BEFORE ANYTHING ELSE

### RULE 1: Install path is `/data/workspace/skills/fox-strategy/`

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

## The FOX v2.0 Experiment

**Hypothesis:** Fox v1.0's weak Stalker trades didn't just have low scores — they had thin confirmation. A score-7 entry from STALKER_CLIMB(+3) + SM_ACTIVE(+1) + time_bonus(+1) has 2 reasons beyond base. That's technically passing but only confirms "it's climbing with traders during good hours." No contribution acceleration, no deep start, no momentum signal. It's noise dressed as signal.

**The tweak:** Stalker entries require `len(reasons) >= 3`. This is the ONLY difference from the base scanner. Everything else — minScore, minTotalClimb, DSL tiers, Striker logic, streak gate — is identical.

**What this filters:** A Stalker at score 7 with reasons = [STALKER_CLIMB +8, SM_ACTIVE 15 traders] has 2 reasons → BLOCKED. The same signal with reasons = [STALKER_CLIMB +8, CONTRIB_ACCEL +0.002%/scan, SM_ACTIVE 15 traders] has 3 reasons → PASSES. The difference is contribution acceleration, which is a real confirmation that SM money is accelerating, not just present.

**Expected effect:** Fewer Stalker entries, higher quality. The question is whether this filters out too many winners (like NEAR SHORT +$18.10 which had 3 reasons and would pass) or just the chop.

---

## Dual-Mode Entry

### MODE A — STALKER (Accumulation) — Score >= 7, min 3 reasons
- SM rank climbing steadily over 3+ consecutive scans
- Total climb >= 8 ranks
- Contribution building each scan
- 4H trend aligned
- **v2.0: At least 3 distinct scoring reasons required**
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
python3 /data/workspace/skills/fox-strategy/scripts/fox-scanner.py
```

DSL cron (3 minutes, isolated session):
```
python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py --state-dir /data/workspace/skills/fox-strategy/state
```

---

## Bootstrap Gate

On EVERY session start, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (90s, main) and DSL cron (3 min, isolated)
3. Verify BOTH crons `status: ok`
4. Write `config/bootstrap-complete.json`
5. Send: "🦊 FOX v2.0 online. minReasons experiment active. Score 7+, climb 8+, 3 reasons minimum. Silence = no conviction."

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
| Stalker min reasons | 3 (experiment) |
| Stalker streak gate | 3 losses → minScore 9 |

---

## Notification Policy

**ONLY alert:** Position OPENED (include reason count), position CLOSED (P&L + reason), streak gate activated/deactivated, critical error.

**NEVER alert:** Scanner found nothing, DSL routine, any reasoning.

---

## Files

| File | Purpose |
|---|---|
| `scripts/fox-scanner.py` | Dual-mode scanner with minReasons experiment |
| `scripts/fox_config.py` | Config helper with stalkerResults tracking |
| `config/fox-config.json` | Config with Fox v2.0 thresholds |

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
