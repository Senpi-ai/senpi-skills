# Pulse Cron Templates

Ready-to-use OpenClaw cron job templates for Pulse scanner deployment.

## Template Structure

All templates follow OpenClaw's cron.add format:
- `sessionTarget` and `schedule` at top level
- `payload.kind` and `payload.text` for content
- Use `deleteAfterRun: true` for one-shot jobs

## Standard Deployment

### Main Session (System Events)

Runs in the main agent session as system events. Output appears in chat.

```bash
openclaw cron add "Pulse Scanner" \
  --schedule "*/5 * * * *" \
  --session-target main \
  --payload '{"kind":"systemEvent","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py --config-path /data/workspace/pulse-config.json"}' \
  --wake-mode now
```

**Use when**: You want scanner output in your main chat session.

### Isolated Session (Dedicated Agent)

Runs in dedicated agent sessions with full context access.

```bash
openclaw cron add "Pulse Scanner" \
  --schedule "*/5 * * * *" \
  --session-target isolated \
  --payload '{"kind":"agentTurn","text":"Run pulse scanner: python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py --config-path /data/workspace/pulse-config.json"}' \
  --wake-mode now
```

**Use when**: You want scanner to run independently with agent intelligence.

## Multi-Instance Templates

### Alpha Instance (High-Velocity Scanner)
```bash
# Setup: Create alpha config
python3 /data/workspace/skills/pulse/scripts/pulse-setup.py \
  --config-path /data/workspace/pulse-alpha-config.json \
  --budget-model \
  --non-interactive

# Cron: Every 2 minutes
openclaw cron add "Pulse Alpha" \
  --schedule "*/2 * * * *" \
  --session-target isolated \
  --payload '{"kind":"agentTurn","text":"Run alpha pulse scanner: /data/workspace/skills/pulse/scripts/pulse-scanner.py --config-path /data/workspace/pulse-alpha-config.json"}' \
  --wake-mode now
```

### Beta Instance (Conservative Scanner)
```bash
# Setup: Create beta config  
python3 /data/workspace/skills/pulse/scripts/pulse-setup.py \
  --config-path /data/workspace/pulse-beta-config.json \
  --mid-model \
  --non-interactive

# Cron: Every 10 minutes
openclaw cron add "Pulse Beta" \
  --schedule "*/10 * * * *" \
  --session-target main \
  --payload '{"kind":"systemEvent","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py --config-path /data/workspace/pulse-beta-config.json"}' \
  --wake-mode now
```

## Custom Signal Source Templates

### WOLF Integration (Emerging Movers)
```bash
# Uses existing WOLF emerging-movers.py as signal source
openclaw cron add "Pulse WOLF" \
  --schedule "*/3 * * * *" \
  --session-target isolated \
  --payload '{"kind":"agentTurn","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py --config-path /data/workspace/pulse-wolf-config.json"}' \
  --wake-mode now
```

**Config requirements**: Set `signal_source: "/data/workspace/skills/wolf-strategy/scripts/emerging-movers.py"` in config.

### Senpi Smart Money Scanner
```bash
# Uses leaderboard_get_smart_money_token_inflows (default)
openclaw cron add "Pulse Smart Money" \
  --schedule "*/5 * * * *" \
  --session-target main \
  --payload '{"kind":"systemEvent","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py"}' \
  --wake-mode now
```

**No config needed**: Uses built-in defaults.

## One-Shot Templates

### Manual Scan (Immediate)
```bash
openclaw cron add "Pulse Manual Scan" \
  --schedule "at" \
  --at "$(date -u -d '+1 minute' '+%Y-%m-%dT%H:%M:%SZ')" \
  --session-target main \
  --payload '{"kind":"systemEvent","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py --dry-run"}' \
  --wake-mode now \
  --delete-after-run true
```

### Test Setup (Dry Run)
```bash
openclaw cron add "Pulse Test" \
  --schedule "at" \
  --at "$(date -u -d '+30 seconds' '+%Y-%m-%dT%H:%M:%SZ')" \
  --session-target isolated \
  --payload '{"kind":"agentTurn","text":"Test pulse scanner setup: /data/workspace/skills/pulse/scripts/pulse-scanner.py --dry-run --verbose"}' \
  --wake-mode now \
  --delete-after-run true
```

## Schedule Pattern Reference

| Schedule | Frequency | Use Case |
|----------|-----------|----------|
| `"*/1 * * * *"` | Every minute | Hot market periods |
| `"*/2 * * * *"` | Every 2 minutes | High-frequency scanning |
| `"*/3 * * * *"` | Every 3 minutes | Balanced scanning |
| `"*/5 * * * *"` | Every 5 minutes | Standard monitoring |
| `"*/10 * * * *"` | Every 10 minutes | Conservative scanning |
| `"0 */1 * * *"` | Every hour | Low-frequency monitoring |

## Advanced Templates

### Market Hours Only (9 AM - 4 PM UTC)
```bash
openclaw cron add "Pulse Market Hours" \
  --schedule "*/5 9-16 * * 1-5" \
  --session-target isolated \
  --payload '{"kind":"agentTurn","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py"}' \
  --wake-mode now
```

### After-Hours Monitoring (5 PM - 8 AM UTC)
```bash
openclaw cron add "Pulse After Hours" \
  --schedule "*/15 17-23,0-8 * * *" \
  --session-target main \
  --payload '{"kind":"systemEvent","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py --config-path /data/workspace/pulse-afterhours-config.json"}' \
  --wake-mode now
```

### Weekends Only
```bash
openclaw cron add "Pulse Weekends" \
  --schedule "*/10 * * * 0,6" \
  --session-target isolated \
  --payload '{"kind":"agentTurn","text":"python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py"}' \
  --wake-mode now
```

## Error Recovery Templates

### State Reset (Emergency)
```bash
# One-shot job to reset all heat states
openclaw cron add "Pulse Reset" \
  --schedule "at" \
  --at "$(date -u -d '+1 minute' '+%Y-%m-%dT%H:%M:%SZ')" \
  --session-target main \
  --payload '{"kind":"systemEvent","text":"rm -f /data/workspace/state/pulse/pulse-heat*.json && echo Heat states reset"}' \
  --wake-mode now \
  --delete-after-run true
```

### Config Validation
```bash
# Validate all Pulse configs before deployment
openclaw cron add "Pulse Validate" \
  --schedule "at" \
  --at "$(date -u -d '+1 minute' '+%Y-%m-%dT%H:%M:%SZ')" \
  --session-target isolated \
  --payload '{"kind":"agentTurn","text":"Validate pulse configs: find /data/workspace -name pulse*config.json -exec python3 -c \"import json; print(\"{}: OK\".format(\"{}\")) if json.load(open(\"{}\")) else None\" \\;"}' \
  --wake-mode now \
  --delete-after-run true
```

## Production Deployment Checklist

Before adding production cron jobs:

1. **Test configuration**:
   ```bash
   python3 /data/workspace/skills/pulse/scripts/pulse-scanner.py --dry-run --verbose
   ```

2. **Verify state directory exists**:
   ```bash
   mkdir -p /data/workspace/state/pulse
   ```

3. **Test MCP connectivity**:
   ```bash
   mcporter call senpi leaderboard_get_smart_money_token_inflows --args '{}'
   ```

4. **Start with longer intervals** (5-10 min) and tune down based on signal density

5. **Monitor for first few cycles** to ensure proper state transitions

6. **Set up alerts** if using `escalation_action: "alert"`