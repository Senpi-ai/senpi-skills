---
name: roach-strategy
description: >-
  ROACH v1.0 — Striker-only scanner. Stalker mode disabled entirely. Tests
  whether Stalker adds any value or is pure drag. Only trades violent FIRST_JUMP
  explosions backed by 1.5x volume. Will have long stretches of silence — that
  patience is the edge. All hardened gates preserved. XYZ banned. Leverage 7-10x.
  DSL state template in scanner output.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
  config_source: striker-only-experiment
---

# 🪳 ROACH v1.0 — Striker Only. Stalker Disabled.

Cockroaches survive anything. ROACH survives by not trading when there's no explosion.

---

## ⛔ CRITICAL AGENT RULES — READ BEFORE ANYTHING ELSE

### RULE 1: Install path is `/data/workspace/skills/roach-strategy/`

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

### RULE 8: Do NOT enable Stalker mode

Stalker is disabled by design. This is an experiment. Do not re-enable it, do not build your own accumulation detection, do not invent alternative entry logic. If the scanner outputs `"stalkerDisabled": true`, that means it's working correctly. Long periods of silence with zero trades are EXPECTED and CORRECT.

---

## The ROACH Experiment

**Hypothesis:** Stalker mode is pure drag. Fox v1.0 produced 17 Stalker trades at score 6-7 with a 17.6% win rate and -$91.32 net P&L. The one Striker signal (ZEC LONG, score 11) was the only explosive entry worth taking. If ROACH outperforms or matches the other variants with dramatically fewer trades, Stalker should be permanently demoted or removed from the scanner.

**What this means in practice:** ROACH will be quiet. Striker signals require FIRST_JUMP or IMMEDIATE_MOVER (10+ rank jump from #25+), rank jump >= 15 OR velocity > 15, volume >= 1.5x, score >= 9 with 4+ reasons. In choppy markets, this might fire 0-1 times per day. In trending markets with SM explosions, maybe 2-3.

**The question:** Does fewer + higher quality beat more + mixed quality? Polar proved it at the macro level (29 trades, +28.1% vs Ghost Fox's 1,078 trades, -58.5%). ROACH tests it at the signal-type level within the same scanner framework.

---

## Single Entry Mode

### STRIKER (Explosion) — Score >= 9, min 4 reasons
- FIRST_JUMP or IMMEDIATE_MOVER (10+ rank jump from #25+)
- Rank jump >= 15 OR contribution velocity > 15
- Raw volume >= 1.5x of 6h average
- Score >= 9 with at least 4 distinct reasons
- 4H trend aligned, XYZ banned, asset not in cooldown

### STALKER — DISABLED
- Scanner still builds scan history (needed for Striker's rank jump detection)
- Stalker signals are never generated or emitted
- Output always contains `"stalkerDisabled": true` and `"stalkerSignals": []`

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

Striker entries typically score 9-12, so they'll land in the middle or top tier:

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
1. Scanner outputs Striker signal with `dslState` block
2. Verify positions < 3
3. Verify exchange max leverage >= 7
4. Call `create_position`
5. Write `state/{TOKEN}.json` with exact `signal.dslState` plus `entryPrice`, `leverage`, `createdAt`
6. Send ONE notification: position opened
7. DSL cron picks up the state file

---

## Cron Setup

**EXACT commands — copy-paste. Do not modify.**

Scanner cron (90 seconds, main session):
```
python3 /data/workspace/skills/roach-strategy/scripts/roach-scanner.py
```

DSL cron (3 minutes, isolated session):
```
python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py --state-dir /data/workspace/skills/roach-strategy/state
```

---

## Bootstrap Gate

On EVERY session start, check `config/bootstrap-complete.json`. If missing:
1. Verify Senpi MCP
2. Create scanner cron (90s, main) and DSL cron (3 min, isolated)
3. Verify BOTH crons `status: ok`
4. Write `config/bootstrap-complete.json`
5. Send: "🪳 ROACH v1.0 online. Striker only. Stalker disabled. Waiting for explosions. Silence = no explosion."

---

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day (chop) | 0-1 |
| Trades/day (trending) | 1-3 |
| Days with zero trades | Common and correct |
| Win rate | Higher than Stalker variants (60-70% target) |
| Avg winner | Larger (explosive entries with 1.5x volume) |
| Avg loser | Similar to other variants (DSL-managed) |
| Total P&L | Hypothesis: matches or beats Stalker variants with far fewer trades |

**SILENCE IS CORRECT.** If ROACH goes 24-48 hours without a trade, that means there were no FIRST_JUMP explosions worth taking. That's the experiment working, not a bug.

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
| Stalker mode | DISABLED |

---

## Notification Policy

**ONLY alert:** Position OPENED (Striker signal with score + reasons), position CLOSED (P&L + reason), critical error.

**NEVER alert:** Scanner found no Striker signals (this is normal and expected), DSL routine, any reasoning about whether to enable Stalker, any analysis of market conditions.

---

## Files

| File | Purpose |
|---|---|
| `scripts/roach-scanner.py` | Striker-only scanner (Stalker disabled in run()) |
| `scripts/roach_config.py` | Config helper |
| `config/roach-config.json` | Config (Stalker settings present but unused) |

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
