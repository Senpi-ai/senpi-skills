# TIGER Cron Templates

All crons use OpenClaw `systemEvent` format targeting the `main` session.

Replace:
- `{SCRIPTS}` → full scripts path (default: `$TIGER_WORKSPACE/scripts`)
- `{TELEGRAM}` → Telegram chat ID

## Notification Policy

**ONLY notify Telegram when something actionable happens:**
- Trade opened or closed
- Aggression level changed
- Risk limit breached or halt triggered
- Position resized or stop adjusted by risk guardian
- Errors that need human attention

**NEVER notify Telegram for:**
- HEARTBEAT_OK (idle cycles)
- NO_POSITIONS (DSL has nothing to trail)
- Scanner ran but found no signals
- Data collection completed normally
- Any routine cycle with no state change

When in doubt: if the output is HEARTBEAT_OK or signals_found is 0, do NOT notify.

---

## Model Tier Reference

| Tier | Use | Models |
|------|-----|--------|
| Tier 1 (fast/cheap) | Scanners, OI tracker, DSL math | claude-haiku-4-5, gpt-4o-mini |
| Tier 2 (capable) | Goal engine, risk guardian, exit evaluation | claude-sonnet-4-6, gpt-4o |

---

## Cron 1: Compression Scanner — Tier 1

Every 5 minutes. BB squeeze + OI breakout detection.

```json
{
  "name": "TIGER — Compression Scanner",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER COMPRESSION SCANNER: Run `timeout 55 python3 {SCRIPTS}/compression-scanner.py`, parse JSON.\nIf actionable > 0 + slots available + not halted: evaluate top signal per SKILL.md.\nIf confluence ≥ threshold for current aggression: enter via create_position → notify Telegram ({TELEGRAM}).\nIf no actionable signals or no entry made: HEARTBEAT_OK. Do NOT notify Telegram."
  }
}
```

---

## Cron 2: Correlation Scanner — Tier 1

Every 3 minutes. BTC correlation lag detection.

```json
{
  "name": "TIGER — Correlation Scanner",
  "schedule": { "kind": "every", "everyMs": 180000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER CORRELATION SCANNER: Run `timeout 55 python3 {SCRIPTS}/correlation-scanner.py`, parse JSON.\nIf actionable > 0 + BTC move confirmed + lag ratio ≥ 0.5 + slots available:\nEnter via create_position → notify Telegram ({TELEGRAM}).\nIf no actionable signals or no entry made: HEARTBEAT_OK. Do NOT notify Telegram."
  }
}
```

---

## Cron 3: Momentum Scanner — Tier 1

Every 5 minutes (offset 1 min from compression).

```json
{
  "name": "TIGER — Momentum Scanner",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER MOMENTUM SCANNER: Run `timeout 55 python3 {SCRIPTS}/momentum-scanner.py`, parse JSON.\nIf actionable > 0 + slots available: evaluate per SKILL.md momentum rules.\nUse tighter Phase 1 retrace (0.012) for DSL on momentum positions.\nIf entry made → notify Telegram ({TELEGRAM}).\nIf no actionable signals or no entry made: HEARTBEAT_OK. Do NOT notify Telegram."
  }
}
```

---

## Cron 4: Reversion Scanner — Tier 1

Every 5 minutes (offset 2 min from compression).

```json
{
  "name": "TIGER — Reversion Scanner",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER REVERSION SCANNER: Run `timeout 55 python3 {SCRIPTS}/reversion-scanner.py`, parse JSON.\nIf actionable > 0 + 4h RSI extreme confirmed + slots available:\nEnter counter-trend per SKILL.md reversion rules.\nIf entry made → notify Telegram ({TELEGRAM}).\nIf no actionable signals or no entry made: HEARTBEAT_OK. Do NOT notify Telegram."
  }
}
```

---

## Cron 5: Funding Scanner — Tier 1

Every 30 minutes.

```json
{
  "name": "TIGER — Funding Scanner",
  "schedule": { "kind": "every", "everyMs": 1800000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER FUNDING SCANNER: Run `timeout 55 python3 {SCRIPTS}/funding-scanner.py`, parse JSON.\nIf actionable > 0 + extreme funding confirmed + slots available:\nEnter opposite crowd per SKILL.md funding rules. Use wider DSL retrace (0.02+).\nIf entry made → notify Telegram ({TELEGRAM}).\nIf no actionable signals or no entry made: HEARTBEAT_OK. Do NOT notify Telegram."
  }
}
```

---

## Cron 6: OI Tracker — Tier 1

Every 5 minutes (offset 3 min). Data collection only.

```json
{
  "name": "TIGER — OI Tracker",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER OI TRACKER: Run `timeout 55 python3 {SCRIPTS}/oi-tracker.py`, parse JSON.\nData collection only — no trading actions. NEVER notify Telegram.\nIf error → notify Telegram ({TELEGRAM}). Else HEARTBEAT_OK silently."
  }
}
```

---

## Cron 7: Goal Engine — Tier 2

Every 1 hour. Requires judgment: evaluate aggression level.

```json
{
  "name": "TIGER — Goal Engine",
  "schedule": { "kind": "every", "everyMs": 3600000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER GOAL ENGINE: Run `python3 {SCRIPTS}/goal-engine.py`, parse JSON.\nUpdate aggression level.\nOnly notify Telegram ({TELEGRAM}) if: aggression changed, ABORT triggered, or target reached.\nIf no change: HEARTBEAT_OK silently. Do NOT notify Telegram for routine recalculations."
  }
}
```

---

## Cron 8: Risk Guardian — Tier 2

Every 5 minutes (offset 4 min). Enforces all risk limits.

```json
{
  "name": "TIGER — Risk Guardian",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER RISK GUARDIAN: Run `python3 {SCRIPTS}/risk-guardian.py`, parse JSON.\n\nPROCESSING ORDER:\n1. Read state ONCE.\n2. Check daily loss, drawdown, single position limits per SKILL.md.\n3. Check OI collapse, funding reversal for FUNDING_ARB positions.\n4. If critical → close via close_position. Set halted if needed.\n\nOnly notify Telegram ({TELEGRAM}) if: position closed, position resized, halt triggered, or critical alert raised.\nIf all clear with no actions taken: HEARTBEAT_OK silently. Do NOT notify Telegram for routine checks."
  }
}
```

---

## Cron 9: Exit Checker — Tier 2

Every 5 minutes (runs with risk guardian).

```json
{
  "name": "TIGER — Exit Checker",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER EXIT CHECKER: Run `python3 {SCRIPTS}/tiger-exit.py`, parse JSON.\nProcess exit signals by priority. Pattern-specific exits per SKILL.md.\nDeadline proximity: tighten stops in final 24h.\nOnly notify Telegram ({TELEGRAM}) if: position closed, stop tightened, or deadline action taken.\nIf no exits triggered: HEARTBEAT_OK silently. Do NOT notify Telegram."
  }
}
```

---

## Cron 10: DSL Combined — Tier 1

Every 30 seconds. Iterates all active DSL state files.

```json
{
  "name": "TIGER — DSL Trailing Stops",
  "schedule": { "kind": "every", "everyMs": 30000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER DSL: First check TIGER state file for activePositions. If activePositions is empty (no open positions), output HEARTBEAT_OK immediately and STOP — do NOT run dsl-v4.py. Do NOT notify Telegram.\nOnly if positions exist: for each active position's DSL state file, run `python3 {SCRIPTS}/dsl-v4.py` with DSL_STATE_FILE pointed at that file, parse JSON.\nDSL is self-contained — auto-closes via close_position on breach.\nOnly notify Telegram ({TELEGRAM}) if: position closed by DSL breach or tier upgrade occurred.\nRoutine trailing (no close, no tier change): HEARTBEAT_OK silently. Do NOT notify Telegram."
  }
}
```

---

## Stagger Schedule

Scanners are offset to avoid simultaneous mcporter calls:

| Offset | Cron |
|--------|------|
| :00 | Compression Scanner |
| :01 | Momentum Scanner |
| :02 | Reversion Scanner |
| :03 | OI Tracker |
| :04 | Risk Guardian + Exit Checker |

Correlation (3min) and Funding (30min) run on their own cadence. DSL runs every 30s independently.

---

## Cron Creation Checklist

| # | Name | Interval (ms) | Model Tier | Purpose |
|---|------|---------------|------------|---------|
| 1 | tiger-compression | 300000 (5m) | Tier 1 | BB squeeze breakout |
| 2 | tiger-correlation | 180000 (3m) | Tier 1 | BTC lag detection |
| 3 | tiger-momentum | 300000 (5m) | Tier 1 | Price move + volume |
| 4 | tiger-reversion | 300000 (5m) | Tier 1 | Overextension fade |
| 5 | tiger-funding | 1800000 (30m) | Tier 1 | Funding arb |
| 6 | tiger-oi | 300000 (5m) | Tier 1 | Data collection |
| 7 | tiger-goal | 3600000 (1h) | Tier 2 | Aggression |
| 8 | tiger-risk | 300000 (5m) | Tier 2 | Risk limits |
| 9 | tiger-exit | 300000 (5m) | Tier 2 | Pattern exits |
| 10 | tiger-dsl | 30000 (30s) | Tier 1 | Trailing stops |
