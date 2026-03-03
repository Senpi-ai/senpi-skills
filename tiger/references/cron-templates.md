# TIGER Cron Templates

11 crons. DSL trailing stops are managed by the Tiger plugin service (not a cron).

Crons use OpenClaw `systemEvent` or `agentTurn` format, with `main` or `isolated` session targets depending on task.

Replace:
- `{SCRIPTS}` → full scripts path (default: `$TIGER_WORKSPACE/scripts`)
- `{TELEGRAM}` → Telegram chat ID

---

## Model Tier Reference

| Tier | Use | Models |
|------|-----|--------|
| Tier 1 (fast/cheap) | Scanners, OI tracker | claude-haiku-4-5, gpt-4o-mini |
| Tier 2 (capable) | Goal engine, risk guardian, exit evaluation | claude-sonnet-4-6, gpt-4o |

---

## Cron 0: Prescreener — Tier 1

Every 5 minutes. Scores all ~230 assets in one API call, writes top 30 candidates to prescreened.json. Scanners read from this instead of each doing their own filtering. Must run before scanners in the stagger schedule.

```json
{
  "name": "TIGER — Prescreener",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER PRESCREENER: Run `timeout 30 python3 {SCRIPTS}/prescreener.py`, parse JSON.\nScores all instruments and writes top 30 to prescreened.json.\nOutput HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
  }
}
```

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
    "text": "TIGER COMPRESSION SCANNER: Run `timeout 55 python3 {SCRIPTS}/compression-scanner.py`, parse JSON.\nSLOT GUARD (MANDATORY): If strategySlots.anySlotsAvailable is false → HEARTBEAT_OK, do NOT enter.\nIf actionable > 0 + not halted: evaluate top signal per SKILL.md.\nIf confluence ≥ threshold for current aggression: enter via create_position.\nNotify Telegram ({TELEGRAM}). Else HEARTBEAT_OK."
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
    "text": "TIGER CORRELATION SCANNER: Run `timeout 55 python3 {SCRIPTS}/correlation-scanner.py`, parse JSON.\nSLOT GUARD (MANDATORY): If strategySlots.anySlotsAvailable is false → HEARTBEAT_OK, do NOT enter.\nIf actionable > 0 + BTC move confirmed + lag ratio ≥ 0.5:\nEnter via create_position. Notify Telegram ({TELEGRAM}). Else HEARTBEAT_OK."
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
    "text": "TIGER MOMENTUM SCANNER: Run `timeout 55 python3 {SCRIPTS}/momentum-scanner.py`, parse JSON.\nSLOT GUARD (MANDATORY): If strategySlots.anySlotsAvailable is false → HEARTBEAT_OK, do NOT enter.\nIf actionable > 0: evaluate per SKILL.md momentum rules.\nUse tighter Phase 1 retrace (0.012) for DSL on momentum positions.\nNotify Telegram ({TELEGRAM}). Else HEARTBEAT_OK."
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
    "text": "TIGER REVERSION SCANNER: Run `timeout 55 python3 {SCRIPTS}/reversion-scanner.py`, parse JSON.\nSLOT GUARD (MANDATORY): If strategySlots.anySlotsAvailable is false → HEARTBEAT_OK, do NOT enter.\nIf actionable > 0 + 4h RSI extreme confirmed:\nEnter counter-trend per SKILL.md reversion rules.\nNotify Telegram ({TELEGRAM}). Else HEARTBEAT_OK."
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
    "text": "TIGER FUNDING SCANNER: Run `timeout 55 python3 {SCRIPTS}/funding-scanner.py`, parse JSON.\nSLOT GUARD (MANDATORY): If strategySlots.anySlotsAvailable is false → HEARTBEAT_OK, do NOT enter.\nIf actionable > 0 + extreme funding confirmed:\nEnter opposite crowd per SKILL.md funding rules. Use wider DSL retrace (0.02+).\nNotify Telegram ({TELEGRAM}). Else HEARTBEAT_OK."
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
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER OI TRACKER: Run `timeout 55 python3 {SCRIPTS}/oi-tracker.py`, parse JSON.\nData collection only — no trading actions.\nIf error → notify Telegram ({TELEGRAM}). Else HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
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
    "text": "TIGER GOAL ENGINE: Run `python3 {SCRIPTS}/goal-engine.py`, parse JSON.\nUpdate aggression level. If aggression changed → notify Telegram ({TELEGRAM}).\nIf ABORT → tighten all stops, stop new entries.\nElse HEARTBEAT_OK."
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
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER RISK GUARDIAN: Run `python3 {SCRIPTS}/risk-guardian.py`, parse JSON.\n\nPROCESSING ORDER:\n1. Read state ONCE.\n2. Check daily loss, drawdown, single position limits per SKILL.md.\n3. Check OI collapse, funding reversal for FUNDING_ARB positions.\n4. Script executes close actions directly and sets halted when critical.\n5. Call `tiger_deactivate_dsl` for each closed position.\n6. Send ONE Telegram ({TELEGRAM}).\n\nElse HEARTBEAT_OK.",
    "model": "anthropic/claude-sonnet-4-5-20250929"
  },
  "delivery": {
    "mode": "none"
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
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER EXIT CHECKER: Run `python3 {SCRIPTS}/tiger-exit.py`, parse JSON.\nScript executes CLOSE exits directly; PARTIAL actions remain advisory.\nCall `tiger_deactivate_dsl` for each closed position.\nPattern-specific exits per SKILL.md. Deadline proximity: tighten stops in final 24h.\nNotify Telegram ({TELEGRAM}). Else HEARTBEAT_OK.",
    "model": "anthropic/claude-sonnet-4-5-20250929"
  },
  "delivery": {
    "mode": "none"
  }
}
```

---

> **DSL trailing stops** are now managed by the Tiger plugin's `tiger-dsl-runner` service.
> No cron needed. The plugin runs `dsl-v4.py` automatically at the configured interval (default: 30s).
> Position closures are logged by the plugin. On-demand ticks available via `tiger_dsl_tick` tool.

---

## Cron 10: ROAR Analyst — Tier 2

Every 8 hours. Meta-optimizer that tunes TIGER's execution parameters. Isolated with announce — only delivers when changes are made.

```json
{
  "name": "TIGER — ROAR Analyst",
  "schedule": { "kind": "every", "everyMs": 28800000 },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER ROAR: Run `python3 {SCRIPTS}/roar-analyst.py`, parse JSON.\nROAR analyzes TIGER's trade log and adjusts execution thresholds within bounded ranges.\nIt NEVER touches user risk limits (budget, target, maxDrawdownPct, etc).\nIf changes_applied is true: send ONE Telegram message to {TELEGRAM_CHAT_ID} summarizing what changed and why.\nIf reverted_previous is true: mention the revert.\nIf no changes: output HEARTBEAT_OK. Do NOT send Telegram for routine analysis.",
    "model": "anthropic/claude-sonnet-4-5-20250929"
  },
  "delivery": {
    "mode": "announce",
    "channel": "telegram",
    "to": "{TELEGRAM_CHAT_ID}",
    "bestEffort": true
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

Correlation (3min) and Funding (30min) run on their own cadence. DSL is managed by the Tiger plugin service (not a cron).

---

## Cron Creation Checklist

| # | Name | Interval (ms) | Session | Payload | Delivery | Model Tier | Purpose |
|---|------|---------------|---------|---------|----------|------------|---------|
| 0 | tiger-prescreen | 300000 (5m) | isolated | agentTurn | none | Tier 1 | Asset prescreening |
| 1 | tiger-compression | 300000 (5m) | **main** | systemEvent | — | Tier 1 | BB squeeze breakout |
| 2 | tiger-correlation | 180000 (3m) | **main** | systemEvent | — | Tier 1 | BTC lag detection |
| 3 | tiger-momentum | 300000 (5m) | **main** | systemEvent | — | Tier 1 | Price move + volume |
| 4 | tiger-reversion | 300000 (5m) | **main** | systemEvent | — | Tier 1 | Overextension fade |
| 5 | tiger-funding | 1800000 (30m) | **main** | systemEvent | — | Tier 1 | Funding arb |
| 6 | tiger-oi | 300000 (5m) | isolated | agentTurn | none | Tier 1 | Data collection |
| 7 | tiger-goal | 3600000 (1h) | **main** | systemEvent | — | Tier 2 | Aggression |
| 8 | tiger-risk | 300000 (5m) | isolated | agentTurn | none | Tier 2 | Risk limits |
| 9 | tiger-exit | 300000 (5m) | isolated | agentTurn | none | Tier 2 | Pattern exits |
| — | DSL | configurable | — | — | — | **Plugin** | Trailing stops (tiger-dsl-runner service) |
| 10 | tiger-roar | 28800000 (8h) | isolated | agentTurn | announce | Tier 2 | Meta-optimizer |
