---
name: orca-strategy
description: >-
  ORCA v1.2 — Hardened dual-mode emerging movers scanner with Fox's live
  trading lessons applied. Stalker minScore raised to 7, minTotalClimb to 8.
  Tighter Phase 1 timing for low-score entries (deadWeight 8 min, timeout 25 min).
  Stalker consecutive-loss streak gate: 3 losses in a row raises minScore to 9
  until a win resets. All v1.1.1 DSL audit fixes preserved. XYZ banned.
  Leverage 7-10x. DSL state template in scanner output.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.2"
  platform: senpi
  exchange: hyperliquid
  based_on: orca-v1.1.1
  config_source: orca-v1.1.1-plus-fox-trade-data-lessons
---

# 🐋 ORCA v1.2 — Hardened Dual-Mode Scanner + Fox's Lessons

Orca v1.1.1's proven core with targeted fixes from Fox's live trade data.

---

## ⛔ CRITICAL AGENT RULES — READ BEFORE ANYTHING ELSE

### RULE 1: Install path is `/data/workspace/skills/orca-strategy/`

The skill MUST be installed to exactly this path. A prior agent installed to `~/.openclaw/skills/` which broke scanner state tracking and led to 22 open positions.

### RULE 2: MAX 3 POSITIONS — check before EVERY entry

Before opening ANY position, call `strategy_get_clearinghouse_state` and count open positions. If positions >= 3, SKIP. No exceptions.

### RULE 3: Scanner output is AUTHORITATIVE — never override from memory

If the scanner says `minLeverage: 7`, you use 7. Not 5 from your MEMORY.md. The scanner is the single source of truth.

### RULE 4: Verify BOTH crons on every session start

Run `openclaw crons list`. Scanner cron and DSL cron must both be `status: ok`.

### RULE 5: Write dslState directly — do not construct manually

Write the scanner's `dslState` block DIRECTLY to `state/{TOKEN}.json`. Do not modify. Do not reconstruct.

### RULE 6: Never retry timed-out position creation

If `create_position` times out, check clearinghouse state. If position exists, create DSL state. If not, wait for next scan.

### RULE 7: Never modify your own configuration

Do not adjust leverage, margin, entry caps, scoring thresholds, or DSL parameters.

### RULE 8: Record Stalker results for streak tracking

After every Stalker position closes, call `record_stalker_result(tc, is_win)` from orca_config.py. The scanner uses this to detect losing streaks and temporarily raise the entry bar.

---

## What v1.2 Changes (Fox's Live Trade Data)

Fox v1.0 ran the Orca scanner for 5+ days. Analysis of 20 closed positions revealed:

**The problem:** 17 of 20 trades were Stalker entries at score 6-7. Win rate: 17.6%. Net P&L: -$91.32. The "weak peak bleed" pattern — trades open, bump +0.5% into profit, stall, then DSL cuts them for $3-$10 losses.

**The fixes:**

| Change | v1.1.1 | v1.2 | Why |
|---|---|---|---|
| Stalker minScore | 6 | 7 | Score 6 entries were 100% losers (AVAX, PAXG) |
| Stalker minTotalClimb | 5 | 8 | Weak +5/+6 climbs were noise, not accumulation |
| Low-score absoluteFloorRoe | -20% | -18% | Tighter floor gets out faster on weak entries |
| Low-score hardTimeoutMin | 30 | 25 | Don't wait as long for weak signals to prove |
| Low-score weakPeakCutMin | 15 | 12 | Cut the weak peak bleed earlier |
| Low-score deadWeightCutMin | 10 | 8 | Kill dead trades faster at low conviction |
| Stalker streak gate | (none) | 3 consecutive losses → minScore raised to 9 | Prevents "death by a thousand cuts" bleed |

**What's unchanged:** Striker logic, DSL tiers, stagnation TP, XYZ ban, leverage caps, max positions, cooldowns, all v1.1.1 DSL audit fixes.

---

## Dual-Mode Entry

### MODE A — STALKER (Accumulation) — Score >= 7
- SM rank climbing steadily over 3+ consecutive scans
- Total climb >= 8 ranks (was 5 in v1.1.1)
- Contribution building each scan
- 4H trend aligned
- **v1.2 streak gate:** If last 3 Stalker trades were all losses, minScore temporarily raised to 9

### MODE B — STRIKER (Explosion) — Score >= 9, min 4 reasons
- FIRST_JUMP or IMMEDIATE_MOVER (10+ rank jump from #25+)
- Rank jump >= 15 OR velocity > 15
- Raw volume >= 1.5x of 6h average
- Unchanged from v1.1.1

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

### Phase 1 (Conviction-Scaled) — v1.2 tightened for low scores

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
2. Verify positions < 3 (check clearinghouse state)
3. Verify exchange max leverage >= 7 for this asset
4. Call `create_position` with coin, direction, leverage, margin
5. Write `state/{TOKEN}.json` with exact `signal.dslState` plus `entryPrice`, `leverage`, `createdAt`
6. Send ONE notification: position opened
7. DSL cron picks up the state file

**On position close:**
8. Record result: call `record_stalker_result(tc, is_win)` if the position was a Stalker entry

---

## Cron Setup

**EXACT commands — copy-paste. Do not modify.**

Scanner cron (90 seconds, main session):
```
python3 /data/workspace/skills/orca-strategy/scripts/orca-scanner.py
```

DSL cron (3 minutes, isolated session):
```
python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py --state-dir /data/workspace/skills/orca-strategy/state
```

---

## Bootstrap Gate

On EVERY session start, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (90s, main) and DSL cron (3 min, isolated)
3. Verify BOTH crons `status: ok`
4. Write `config/bootstrap-complete.json`
5. Send: "🐋 ORCA v1.2 online. Fox's lessons applied. Stalker minScore 7, climb 8+, streak gate active. Silence = no conviction."

If bootstrap exists, still verify crons on every session start.

---

## Risk Management

| Rule | Value | Why |
|---|---|---|
| Max positions | 3 | Concentration > diversification |
| Max entries/day | 6 | Fewer trades wins |
| Leverage | 7-10x | Sub-7x can't overcome fees; 15x blows up |
| Daily loss limit | 10% | Proven across 30 agents |
| Per-asset cooldown | 2 hours | PAXG double-entry lesson |
| Stagnation TP | 10% ROE / 45 min | Captures peaks that flatline |
| XYZ equities | Banned | Net negative across every agent |
| Stalker streak gate | 3 consecutive Stalker losses → minScore 9 | Prevents weak-peak bleed |

---

## Notification Policy

**ONLY alert:** Position OPENED, position CLOSED (with P&L and reason), streak gate activated/deactivated, risk guardian triggered, critical error.

**NEVER alert:** Scanner ran with no signals, DSL routine check, any reasoning.

---

## Files

| File | Purpose |
|---|---|
| `scripts/orca-scanner.py` | Dual-mode scanner with Fox's lessons + streak gate |
| `scripts/orca_config.py` | Config helper with stalkerResults tracking |
| `config/orca-config.json` | Config with v1.2 Stalker thresholds |

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
