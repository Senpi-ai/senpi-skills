# SHARK Cron Templates

All 7 cron jobs for the SHARK strategy. Replace `{STRATEGY_ID}` with the actual strategy ID and `{SCRIPTS_DIR}` with the path to SHARK scripts.

## Stagger Offsets

Crons are staggered to avoid API rate limit collisions:

| Offset | Job | Interval |
|--------|-----|----------|
| `:00` | OI Tracker | every 5 min |
| `:01` | Liq Mapper | every 5 min (offset +1) |
| `:02` | Risk Guardian | every 5 min (offset +2) |
| `*/2` | Proximity Scanner | every 2 min |
| `*/2` | Cascade Entry | every 2 min |
| `*/3` | DSL v5.3.1 | every 3 min |
| `*/10` | Health Check | every 10 min |

## 1. OI Tracker (isolated, every 5 min)

```json
{
  "name": "shark-oi-tracker-{STRATEGY_ID_SHORT}",
  "schedule": { "kind": "cron", "expr": "*/5 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "python3 {SCRIPTS_DIR}/shark-oi-tracker.py"
  }
}
```

## 2. Liquidation Mapper (isolated, every 5 min offset +1)

```json
{
  "name": "shark-liq-mapper-{STRATEGY_ID_SHORT}",
  "schedule": { "kind": "cron", "expr": "1-59/5 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "python3 {SCRIPTS_DIR}/shark-liq-mapper.py"
  }
}
```

## 3. Proximity Scanner (isolated, every 2 min)

```json
{
  "name": "shark-proximity-{STRATEGY_ID_SHORT}",
  "schedule": { "kind": "cron", "expr": "*/2 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "python3 {SCRIPTS_DIR}/shark-proximity.py"
  }
}
```

## 4. Cascade Entry (main session, every 2 min)

**Runs on main session** for notification routing and position opening context.

```json
{
  "name": "shark-entry-{STRATEGY_ID_SHORT}",
  "schedule": { "kind": "cron", "expr": "*/2 * * * *", "tz": "UTC" },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "python3 {SCRIPTS_DIR}/shark-entry.py"
  }
}
```

## 5. Risk Guardian (isolated, every 5 min offset +2)

```json
{
  "name": "shark-risk-{STRATEGY_ID_SHORT}",
  "schedule": { "kind": "cron", "expr": "2-59/5 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "python3 {SCRIPTS_DIR}/shark-risk.py"
  }
}
```

## 6. DSL v5.3.1 Trailing Stops (isolated, every 3 min)

Uses the **shared** DSL v5.3.1 skill. One cron per strategy.

```json
{
  "name": "shark-dsl-{STRATEGY_ID_SHORT}",
  "schedule": { "kind": "cron", "expr": "*/3 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID={STRATEGY_ID} python3 ~/.agents/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py"
  }
}
```

## 7. Health Check (isolated, every 10 min)

```json
{
  "name": "shark-health-{STRATEGY_ID_SHORT}",
  "schedule": { "kind": "cron", "expr": "*/10 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "python3 {SCRIPTS_DIR}/shark-health.py"
  }
}
```

## Session Types

| Session | Why |
|---------|-----|
| `main` | Cascade Entry — needs agent context for position opening + user notifications |
| `isolated` | Everything else — stateless monitoring, no user interaction needed |

## API Budget Per Cycle

| Cron | Calls/cycle | Notes |
|------|------------|-------|
| OI Tracker | 1 | market_list_instruments |
| Liq Mapper | 1 + 8 = 9 | instruments + asset_data for top 8 |
| Proximity | 2-4 × 1 = 4 | asset_data per stalking asset |
| Entry | 1-2 × 2 + 1 + 1 = 6 | asset_data + SM markets + clearinghouse + create_position |
| Risk | 1 + 1 = 2 | clearinghouse + prices |
| DSL | 1 + N = 3 | strategy_get + clearinghouse + prices |
| Health | 1 + 1 = 2 | strategy_get + clearinghouse |

**Total worst case per 10 min: ~50 calls** (well within Senpi rate limits).
