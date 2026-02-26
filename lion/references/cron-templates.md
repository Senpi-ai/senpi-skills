# LION Cron Templates

All crons use OpenClaw `systemEvent` format targeting the `main` session.

Replace:
- `{SCRIPTS}` → full scripts path (default: `/data/workspace/recipes/lion/scripts`)
- `{TELEGRAM}` → Telegram chat ID

---

## Model Tier Reference

| Tier | Use | Models |
|------|-----|--------|
| Tier 1 (fast/cheap) | Data collection, binary thresholds, status checks | claude-haiku-4-5, gpt-4o-mini |
| Tier 2 (capable) | Entry decisions, exit judgment, multi-factor evaluation | claude-sonnet-4-6, gpt-4o |

---

## Cron 1: OI Monitor — Tier 1

Every 60 seconds. Pure data collection — no judgment, no trading.

```json
{
  "name": "LION — OI Monitor",
  "schedule": { "kind": "every", "everyMs": 60000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "LION OI MONITOR: Run `python3 {SCRIPTS}/oi-monitor.py`, parse JSON.\nData collection only — no trading actions.\nIf error → notify Telegram ({TELEGRAM}).\nElse HEARTBEAT_OK."
  }
}
```

---

## Cron 2: Cascade Detector — Tier 2

Every 90 seconds. Requires judgment: evaluate cascade phase, decide entry timing.

```json
{
  "name": "LION — Cascade Detector",
  "schedule": { "kind": "every", "everyMs": 90000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "LION CASCADE DETECTOR: Run `python3 {SCRIPTS}/cascade-detector.py`, parse JSON.\n\nPROCESSING ORDER:\n1. Read state ONCE. Check slots + halt status.\n2. If cascade_detected + stabilization_confirmed: enter counter-trade per SKILL.md cascade rules.\n3. Execute via create_position (LIMIT order). Update state.\n4. Send ONE Telegram ({TELEGRAM}).\n\nCRITICAL: NEVER enter during ACTIVE phase. Only at ENTRY_WINDOW.\nIf no signals → HEARTBEAT_OK."
  }
}
```

---

## Cron 3: Book Scanner — Tier 1

Every 30 seconds. Binary threshold check on order book ratios.

```json
{
  "name": "LION — Book Scanner",
  "schedule": { "kind": "every", "everyMs": 30000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "LION BOOK SCANNER: Run `python3 {SCRIPTS}/book-scanner.py`, parse JSON.\nIf imbalance_detected + sweep_confirmed + slots available: enter fade per SKILL.md book rules.\nDo NOT enter during active cascade — cascade overrides book signals.\nNotify Telegram ({TELEGRAM}). Else HEARTBEAT_OK."
  }
}
```

---

## Cron 4: Squeeze Monitor — Tier 2

Every 15 minutes. Requires judgment: evaluate squeeze buildup quality, trigger events.

```json
{
  "name": "LION — Squeeze Monitor",
  "schedule": { "kind": "every", "everyMs": 900000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "LION SQUEEZE MONITOR: Run `python3 {SCRIPTS}/squeeze-monitor.py`, parse JSON.\nIf squeeze_building: update watchlist, notify Telegram ({TELEGRAM}).\nIf squeeze_triggered + slots available: enter per SKILL.md squeeze rules.\nNEVER enter on buildup alone — only on trigger events.\nIf no squeeze conditions → HEARTBEAT_OK."
  }
}
```

---

## Cron 5: Risk & Exit — Tier 2

Every 60 seconds. Evaluates pattern-specific exits, trailing locks, time stops.

```json
{
  "name": "LION — Risk & Exit",
  "schedule": { "kind": "every", "everyMs": 60000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "LION RISK & EXIT: Run `python3 {SCRIPTS}/lion-exit.py`, parse JSON.\n\nPROCESSING ORDER:\n1. Read state ONCE.\n2. Process exit signals by priority (CRITICAL → HIGH → MEDIUM).\n3. Apply pattern-specific rules from SKILL.md (cascade: 2h stop, book: 30min stop, squeeze: 12h stop).\n4. Execute closes. Send ONE Telegram ({TELEGRAM}).\n\nIf no actions → HEARTBEAT_OK."
  }
}
```

---

## Cron 6: Health & Report — Tier 1

Every 10 minutes. Status checks + consolidated reporting.

```json
{
  "name": "LION — Health & Report",
  "schedule": { "kind": "every", "everyMs": 600000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "LION HEALTH: Run `python3 {SCRIPTS}/lion-health.py`, parse JSON.\nCheck margin, orphan positions, PnL guards per SKILL.md.\nIf hourly_report_due: send consolidated status to Telegram ({TELEGRAM}).\nIf critical alerts → notify immediately.\nElse HEARTBEAT_OK."
  }
}
```

---

## Cron Creation Checklist

| # | Name | Interval (ms) | Model Tier | Priority |
|---|------|---------------|------------|----------|
| 1 | lion-oi-monitor | 60000 (60s) | Tier 1 | Data |
| 2 | lion-cascade-detector | 90000 (90s) | Tier 2 | Primary Signal |
| 3 | lion-book-scanner | 30000 (30s) | Tier 1 | Signal |
| 4 | lion-squeeze-monitor | 900000 (15min) | Tier 2 | Signal |
| 5 | lion-risk-exit | 60000 (60s) | Tier 2 | Critical |
| 6 | lion-health | 600000 (10min) | Tier 1 | Monitoring |
