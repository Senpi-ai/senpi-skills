# Fortress Cron Templates

All mandates should be short and reference rules in `SKILL.md`.

## Oracle Heartbeat (Tier 1)

```json
{
  "name": "Fortress — Oracle",
  "schedule": { "kind": "every", "everyMs": 900000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "FORTRESS ORACLE: Run `PYTHONUNBUFFERED=1 timeout 90 python3 scripts/fortress-oracle.py`, parse JSON, apply Fortress rules from SKILL.md. If no actionable signal -> HEARTBEAT_OK."
  }
}
```

## TA Heartbeat (Tier 1)

```json
{
  "name": "Fortress — TA",
  "schedule": { "kind": "every", "everyMs": 900000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "FORTRESS TA: Run `PYTHONUNBUFFERED=1 timeout 90 python3 scripts/fortress-ta.py`, parse JSON, apply Fortress rules from SKILL.md. If no actionable signal -> HEARTBEAT_OK."
  }
}
```

## Volatility Heartbeat (Tier 1)

```json
{
  "name": "Fortress — Volatility",
  "schedule": { "kind": "every", "everyMs": 900000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "FORTRESS VOL: Run `PYTHONUNBUFFERED=1 timeout 90 python3 scripts/fortress-vol.py`, parse JSON, apply Fortress rules from SKILL.md. If no actionable signal -> HEARTBEAT_OK."
  }
}
```

## Risk Heartbeat (Tier 1)

```json
{
  "name": "Fortress — Risk",
  "schedule": { "kind": "every", "everyMs": 900000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "FORTRESS RISK: Run `PYTHONUNBUFFERED=1 timeout 90 python3 scripts/fortress-risk.py`, parse JSON, apply Fortress rules from SKILL.md. If no actionable signal -> HEARTBEAT_OK."
  }
}
```

## Consensus Gate (Tier 2)

```json
{
  "name": "Fortress — Consensus",
  "schedule": { "kind": "every", "everyMs": 900000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "FORTRESS CONSENSUS: Run `PYTHONUNBUFFERED=1 timeout 120 python3 scripts/fortress-consensus.py`, parse JSON, apply Fortress consensus + risk rules from SKILL.md, send one consolidated notification if actionable; otherwise HEARTBEAT_OK."
  }
}
```