# TIGER Cron Templates

All crons use OpenClaw cron system. Templates follow [OpenClaw cron best practices](https://docs.openclaw.ai/automation/cron-jobs).

Replace:
- `{SCRIPTS}` → full scripts path (default: `$TIGER_WORKSPACE/scripts`)
- `{TELEGRAM_CHAT_ID}` → Telegram chat ID (e.g., `-1001234567890` or `-1001234567890:topic:123`)

## Key Design Decisions

**Isolated sessions for scanners (Tier 1)**: Scanners and data collection run in `sessionTarget: "isolated"` with `delivery.mode: "none"`. This prevents main session pollution and avoids session lock contention. Each run gets a fresh session (`cron:<jobId>`) — no context carry-over.

**Isolated sessions with announce for decision-makers (Tier 2)**: Goal engine, risk guardian, and exit checker run isolated with `delivery.mode: "announce"`. OpenClaw auto-suppresses `HEARTBEAT_OK` — only real content (trades, closures, aggression changes) gets delivered.

**DSL stays main session**: DSL needs main context for position state awareness. Uses `systemEvent` payload with `wakeMode: "now"`.

**Model overrides**: Each job specifies its model directly. Tier 1 uses a fast/cheap model, Tier 2 uses a capable model. Change these to match your provider.

## Notification Policy

OpenClaw's announce delivery handles this automatically:
- `HEARTBEAT_OK` responses are **never delivered** (auto-suppressed by OpenClaw)
- Only real content (trades, closures, risk alerts) gets announced
- `delivery.mode: "none"` jobs produce no output at all

---

## Model Tier Reference

| Tier | Use | Example Models |
|------|-----|----------------|
| Tier 1 (fast/cheap) | Scanners, OI tracker, DSL math | `anthropic/claude-haiku-4-5`, `openai/gpt-4o-mini` |
| Tier 2 (capable) | Goal engine, risk guardian, exit evaluation | `anthropic/claude-sonnet-4-5-20250929`, `openai/gpt-4o` |

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

Every 5 minutes. Isolated, no delivery (agent acts on signals internally).

```json
{
  "name": "TIGER — Compression Scanner",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER COMPRESSION SCANNER: Run `timeout 55 python3 {SCRIPTS}/compression-scanner.py`, parse JSON.\nIf actionable > 0 + slots available + not halted: evaluate top signal per SKILL.md.\nIf confluence ≥ threshold for current aggression: enter via create_position.\nIf entry made, send ONE Telegram message to {TELEGRAM_CHAT_ID} with asset, direction, score, leverage.\nIf no actionable signals or no entry made: output HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
  }
}
```

---

## Cron 2: Correlation Scanner — Tier 1

Every 3 minutes. Isolated, no delivery.

```json
{
  "name": "TIGER — Correlation Scanner",
  "schedule": { "kind": "every", "everyMs": 180000 },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER CORRELATION SCANNER: Run `timeout 55 python3 {SCRIPTS}/correlation-scanner.py`, parse JSON.\nIf actionable > 0 + BTC move confirmed + lag ratio ≥ 0.5 + slots available:\nEnter via create_position.\nIf entry made, send ONE Telegram message to {TELEGRAM_CHAT_ID} with asset, direction, lag ratio, window quality.\nIf no actionable signals or no entry made: output HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
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
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER MOMENTUM SCANNER: Run `timeout 55 python3 {SCRIPTS}/momentum-scanner.py`, parse JSON.\nIf actionable > 0 + slots available: evaluate per SKILL.md momentum rules.\nUse tighter Phase 1 retrace (0.012) for DSL on momentum positions.\nIf entry made, send ONE Telegram message to {TELEGRAM_CHAT_ID}.\nIf no actionable signals or no entry made: output HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
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
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER REVERSION SCANNER: Run `timeout 55 python3 {SCRIPTS}/reversion-scanner.py`, parse JSON.\nIf actionable > 0 + 4h RSI extreme confirmed + slots available:\nEnter counter-trend per SKILL.md reversion rules.\nIf entry made, send ONE Telegram message to {TELEGRAM_CHAT_ID}.\nIf no actionable signals or no entry made: output HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
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
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER FUNDING SCANNER: Run `timeout 55 python3 {SCRIPTS}/funding-scanner.py`, parse JSON.\nIf actionable > 0 + extreme funding confirmed + slots available:\nEnter opposite crowd per SKILL.md funding rules. Use wider DSL retrace (0.02+).\nIf entry made, send ONE Telegram message to {TELEGRAM_CHAT_ID}.\nIf no actionable signals or no entry made: output HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
  }
}
```

---

## Cron 6: OI Tracker — Tier 1

Every 5 minutes (offset 3 min). Data collection only — never notifies.

```json
{
  "name": "TIGER — OI Tracker",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER OI TRACKER: Run `timeout 55 python3 {SCRIPTS}/oi-tracker.py`, parse JSON.\nData collection only — no trading actions. Do NOT send any Telegram messages.\nOutput HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
  }
}
```

---

## Cron 7: Goal Engine — Tier 2

Every 1 hour. Isolated with announce — only delivers when aggression changes, target reached, or ABORT.

```json
{
  "name": "TIGER — Goal Engine",
  "schedule": { "kind": "every", "everyMs": 3600000 },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER GOAL ENGINE: Run `python3 {SCRIPTS}/goal-engine.py`, parse JSON.\nUpdate aggression level.\nOnly send Telegram message to {TELEGRAM_CHAT_ID} if: aggression changed, ABORT triggered, or target reached.\nIf ABORT → tighten all stops, stop new entries.\nIf no change: output HEARTBEAT_OK. Do NOT send Telegram for routine recalculations.",
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

## Cron 8: Risk Guardian — Tier 2

Every 5 minutes (offset 4 min). Isolated with announce — only delivers on closures, halts, or critical alerts.

```json
{
  "name": "TIGER — Risk Guardian",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER RISK GUARDIAN: Run `python3 {SCRIPTS}/risk-guardian.py`, parse JSON.\n\nPROCESSING ORDER:\n1. Read state ONCE.\n2. Check daily loss, drawdown, single position limits per SKILL.md.\n3. Check OI collapse, funding reversal for FUNDING_ARB positions.\n4. If critical → close via close_position. Set halted if needed.\n\nOnly send Telegram message to {TELEGRAM_CHAT_ID} if: position closed, position resized, halt triggered, or critical alert raised.\nIf all clear: output HEARTBEAT_OK. Do NOT send Telegram for routine checks.",
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

## Cron 9: Exit Checker — Tier 2

Every 5 minutes (runs with risk guardian). Isolated with announce.

```json
{
  "name": "TIGER — Exit Checker",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "TIGER EXIT CHECKER: Run `python3 {SCRIPTS}/tiger-exit.py`, parse JSON.\nProcess exit signals by priority. Pattern-specific exits per SKILL.md.\nDeadline proximity: tighten stops in final 24h.\nOnly send Telegram message to {TELEGRAM_CHAT_ID} if: position closed, stop tightened, or deadline action taken.\nIf no exits triggered: output HEARTBEAT_OK. Do NOT send Telegram.",
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

## Cron 10: DSL Combined — Tier 1 (Main Session)

Every 30 seconds. Runs in **main session** (needs position state context). Uses `systemEvent`.

```json
{
  "name": "TIGER — DSL Trailing Stops",
  "schedule": { "kind": "every", "everyMs": 30000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "TIGER DSL: First check TIGER state file for activePositions. If activePositions is empty (no open positions), output HEARTBEAT_OK immediately and STOP — do NOT run dsl-v4.py. Do NOT send any Telegram messages.\nOnly if positions exist: for each active position's DSL state file, run `python3 {SCRIPTS}/dsl-v4.py` with DSL_STATE_FILE pointed at that file, parse JSON.\nDSL is self-contained — auto-closes via close_position on breach.\nOnly send Telegram message to {TELEGRAM_CHAT_ID} if: position closed by DSL breach or tier upgrade occurred.\nRoutine trailing (no close, no tier change): output HEARTBEAT_OK. Do NOT send Telegram."
  }
}
```

---

## Cron 11: ROAR Analyst — Tier 2

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

Correlation (3min) and Funding (30min) run on their own cadence. DSL runs every 30s in main session.

---

## Why Isolated Sessions?

Per [OpenClaw docs](https://docs.openclaw.ai/automation/cron-vs-heartbeat):

- **No main session pollution**: Scanners run 100+ times/day. Running in main would bloat context and cause compaction thrashing.
- **No session lock contention**: The `session file locked (timeout 10000ms)` error happens when multiple main-session crons overlap. Isolated sessions can't conflict.
- **HEARTBEAT_OK auto-suppressed**: OpenClaw's announce delivery automatically drops `HEARTBEAT_OK` responses — no notification spam.
- **Model per job**: Tier 1 jobs use cheap models, Tier 2 use capable models. No model switching on the main session.
- **Fresh context**: Each isolated run starts clean. No risk of stale context from previous scanner runs affecting decisions.

DSL stays in main session because it's the only cron that needs awareness of the agent's current conversation context for position management.

---

## Cron Creation Checklist

| # | Name | Interval (ms) | Session | Delivery | Model Tier | Purpose |
|---|------|---------------|---------|----------|------------|---------|
| 0 | tiger-prescreen | 300000 (5m) | isolated | none | Tier 1 | Asset prescreening |
| 1 | tiger-compression | 300000 (5m) | isolated | none | Tier 1 | BB squeeze breakout |
| 2 | tiger-correlation | 180000 (3m) | isolated | none | Tier 1 | BTC lag detection |
| 3 | tiger-momentum | 300000 (5m) | isolated | none | Tier 1 | Price move + volume |
| 4 | tiger-reversion | 300000 (5m) | isolated | none | Tier 1 | Overextension fade |
| 5 | tiger-funding | 1800000 (30m) | isolated | none | Tier 1 | Funding arb |
| 6 | tiger-oi | 300000 (5m) | isolated | none | Tier 1 | Data collection |
| 7 | tiger-goal | 3600000 (1h) | isolated | announce | Tier 2 | Aggression |
| 8 | tiger-risk | 300000 (5m) | isolated | announce | Tier 2 | Risk limits |
| 9 | tiger-exit | 300000 (5m) | isolated | announce | Tier 2 | Pattern exits |
| 10 | tiger-dsl | 30000 (30s) | **main** | — | Tier 1 | Trailing stops |
| 11 | tiger-roar | 28800000 (8h) | isolated | announce | Tier 2 | Meta-optimizer |
