# VIPER Cron Templates

All crons use OpenClaw `systemEvent` format targeting the `main` session.

Replace:
- `{SCRIPTS}` → full scripts path (default: `/data/workspace/recipes/viper/scripts`)
- `{TELEGRAM}` → Telegram chat ID

---

## Model Tier Reference

| Tier | Use | Models |
|------|-----|--------|
| Tier 1 (fast/cheap) | Binary checks, threshold parsing, data collection | claude-haiku-4-5, gpt-4o-mini |
| Tier 2 (capable) | Judgment calls, signal routing, multi-factor decisions | claude-sonnet-4-6, gpt-4o |

---

## Cron 1: Range Scanner — Tier 1

Every 15 minutes. Data collection + threshold scoring. No judgment needed.

```json
{
  "name": "VIPER — Range Scanner",
  "schedule": { "kind": "every", "everyMs": 900000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "VIPER RANGE SCANNER: Run `python3 {SCRIPTS}/range-scanner.py`, parse JSON.\nOn new candidates: update state, notify Telegram ({TELEGRAM}).\nApply range detection rules from SKILL.md.\nIf no changes → HEARTBEAT_OK."
  }
}
```

---

## Cron 2: Boundary Mapper — Tier 1

Every 1 hour. Computation-heavy but deterministic — no LLM judgment.

```json
{
  "name": "VIPER — Boundary Mapper",
  "schedule": { "kind": "every", "everyMs": 3600000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "VIPER BOUNDARY MAPPER: Run `timeout 120 python3 {SCRIPTS}/boundary-mapper.py`, parse JSON.\nAdvance validated ranges to TRADING. Refresh existing boundaries.\nNotify Telegram ({TELEGRAM}) on phase changes.\nIf no changes → HEARTBEAT_OK."
  }
}
```

---

## Cron 3: Bounce Trader — Tier 2

Every 2 minutes. Requires judgment: evaluate whether to enter, which asset, sizing.

```json
{
  "name": "VIPER — Bounce Trader",
  "schedule": { "kind": "every", "everyMs": 120000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "VIPER BOUNCE TRADER: Run `python3 {SCRIPTS}/bounce-trader.py`, parse JSON.\n\nPROCESSING ORDER:\n1. Read state ONCE. Map active ranges + slots.\n2. If signals: apply bounce trading rules from SKILL.md.\n3. Execute entries sequentially via create_position (LIMIT orders only).\n4. Send ONE consolidated Telegram ({TELEGRAM}) after all entries.\n\nIf price in dead zone or no ranges in TRADING → HEARTBEAT_OK."
  }
}
```

---

## Cron 4: Break Detector — Tier 1

Every 2 minutes. Binary threshold checks — no judgment needed.

**CRITICAL:** Break detector output takes precedence over bounce trader. If both fire in the same cycle, process break detector first.

```json
{
  "name": "VIPER — Break Detector",
  "schedule": { "kind": "every", "everyMs": 120000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "VIPER BREAK DETECTOR: Run `python3 {SCRIPTS}/break-detector.py`, parse JSON.\nIf break_confirmed: close ALL positions on that asset immediately, cancel pending orders, apply cooldown per SKILL.md. Notify Telegram ({TELEGRAM}).\nIf break_warning: tighten stops to breakeven.\nIf no breaks → HEARTBEAT_OK."
  }
}
```

---

## Cron 5: Risk & Exit — Tier 2

Every 5 minutes. Evaluates trailing stops, bounce aging, time stops.

```json
{
  "name": "VIPER — Risk & Exit",
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "VIPER RISK & EXIT: Run `python3 {SCRIPTS}/viper-exit.py`, parse JSON.\nProcess exit signals by priority (CRITICAL → HIGH → MEDIUM).\nApply trailing lock, bounce aging, time stop, and PnL guard rules from SKILL.md.\nExecute closes. Send ONE consolidated Telegram ({TELEGRAM}).\nIf no actions → HEARTBEAT_OK."
  }
}
```

---

## Cron 6: Health & Report — Tier 1

Every 10 minutes. Status checks + hourly consolidated reports.

```json
{
  "name": "VIPER — Health & Report",
  "schedule": { "kind": "every", "everyMs": 600000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "VIPER HEALTH: Run `python3 {SCRIPTS}/viper-health.py`, parse JSON.\nCheck margin, orphan positions, stale orders.\nIf hourly_report_due: send consolidated status to Telegram ({TELEGRAM}).\nIf daily_report_due: send daily summary.\nIf critical alerts → notify immediately.\nElse HEARTBEAT_OK."
  }
}
```

---

## Cron Creation Checklist

All 6 crons use:
- `sessionTarget: "main"`
- `payload.kind: "systemEvent"`
- `enabled: true`

| # | Name | Interval (ms) | Model Tier | Priority |
|---|------|---------------|------------|----------|
| 1 | viper-range-scanner | 900000 (15min) | Tier 1 | Scanner |
| 2 | viper-boundary-mapper | 3600000 (1h) | Tier 1 | Mapper |
| 3 | viper-bounce-trader | 120000 (2min) | Tier 2 | Execution |
| 4 | viper-break-detector | 120000 (2min) | Tier 1 | Critical |
| 5 | viper-risk-exit | 300000 (5min) | Tier 2 | Risk |
| 6 | viper-health | 600000 (10min) | Tier 1 | Monitoring |
