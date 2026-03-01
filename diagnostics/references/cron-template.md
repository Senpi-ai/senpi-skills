# Diagnostics Cron Templates

## Periodic Diagnostics (every 6 hours)

```json
{
  "name": "Senpi Diagnostics (6h)",
  "enabled": true,
  "schedule": {
    "kind": "every",
    "everyMs": 21600000
  },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "Run the Senpi diagnostics suite: MCPORTER_CMD=/home/arnold/.openclaw/workspace/scripts/mcporter-senpi-wrapper.sh DIAG_PYTHON=/home/arnold/openclaw/venv/bin/python /home/arnold/openclaw/venv/bin/python /home/arnold/.openclaw/workspace/skills/senpi-trading/senpi-skills/diagnostics/scripts/run-diagnostics.py --workspace /home/arnold/.openclaw/workspace --suite tiger --json --cron-file /home/arnold/.openclaw/cron/jobs.json. Parse JSON output. If safe_to_trade=false: ALERT Robbie immediately in Discord with the full blockers list and set all TIGER trading crons to disabled. If safe_to_trade=true: HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "announce",
    "channel": "discord:channel:1477183852077383762"
  }
}
```

## Quick Pre-Trade Check (on-demand)

```json
{
  "name": "Senpi Pre-Trade Check",
  "enabled": false,
  "schedule": {
    "kind": "every",
    "everyMs": 3600000
  },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "Quick pre-trade diagnostic: MCPORTER_CMD=/home/arnold/.openclaw/workspace/scripts/mcporter-senpi-wrapper.sh DIAG_PYTHON=/home/arnold/openclaw/venv/bin/python /home/arnold/openclaw/venv/bin/python /home/arnold/.openclaw/workspace/skills/senpi-trading/senpi-skills/diagnostics/scripts/run-diagnostics.py --workspace /home/arnold/.openclaw/workspace --suite tiger --quick --json. Parse JSON output. If safe_to_trade=false: ALERT. If safe_to_trade=true: HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "none"
  }
}
```

## Close-Path Only (fastest critical check)

```json
{
  "name": "Senpi Close-Path Check",
  "enabled": false,
  "schedule": {
    "kind": "every",
    "everyMs": 1800000
  },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "Critical path check: MCPORTER_CMD=/home/arnold/.openclaw/workspace/scripts/mcporter-senpi-wrapper.sh DIAG_PYTHON=/home/arnold/openclaw/venv/bin/python /home/arnold/openclaw/venv/bin/python /home/arnold/.openclaw/workspace/skills/senpi-trading/senpi-skills/diagnostics/scripts/run-diagnostics.py --workspace /home/arnold/.openclaw/workspace --suite tiger --phase close-path --json. If safe_to_trade=false: ALERT IMMEDIATELY — close_position pipeline is broken, positions cannot be auto-closed.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "announce",
    "channel": "discord:channel:1477183852077383762"
  }
}
```
