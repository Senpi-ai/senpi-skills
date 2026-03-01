# Senpi Diagnostics Suite

Comprehensive E2E preflight checks for Senpi trading strategies. Run before enabling any strategy to verify that all Senpi MCP calls, state files, cron configs, and the mcporter gate pipeline work end-to-end.

## Why This Exists

The Senpi trading system has multiple failure modes that can silently break position management:

- **Auth failures**: Scripts calling bare `mcporter` instead of the gate adapter (no SENPI_AUTH_TOKEN)
- **State corruption**: DSL state files missing required fields (wallet, phase2, highWaterPrice)
- **Cron misconfiguration**: Payloads without MCPORTER_CMD or PATH shim
- **Script regressions**: New mcporter calls added without MCPORTER_CMD support

This suite catches all of these before they cause real losses.

## Test Phases

| #   | Phase             | Tests             | What It Catches                                               |
| --- | ----------------- | ----------------- | ------------------------------------------------------------- |
| 1   | Environment       | 6                 | Missing binaries, broken symlinks, missing gate adapter       |
| 2   | Cron Config       | per-cron          | Missing scripts, bare mcporter in payloads, wrong model tiers |
| 3   | Tool Connectivity | 7                 | Senpi API unreachable, auth broken, wallet misconfigured      |
| 4   | Source Audit      | per-script        | Bare mcporter subprocess calls, direct Hyperliquid curl       |
| 5   | Script Execution  | per-scanner + DSL | Scripts crash, produce invalid output, timeout                |
| 6   | Close Path        | 3                 | Gate auth chain broken, MCPORTER_CMD not routing through gate |
| 7   | State Lifecycle   | 5                 | DSL crashes on missing fields, state not updated correctly    |

## Usage

```bash
# Full suite (all 7 phases)
python3 diagnostics/scripts/run-diagnostics.py --workspace ~/.openclaw/workspace --suite tiger

# JSON output (for cron automation)
python3 diagnostics/scripts/run-diagnostics.py -w ~/.openclaw/workspace -s tiger --json

# Quick mode (skip script execution and state lifecycle — ~30s instead of ~3min)
python3 diagnostics/scripts/run-diagnostics.py -w ~/.openclaw/workspace -s tiger --quick

# Single phase (fastest critical check)
python3 diagnostics/scripts/run-diagnostics.py -w ~/.openclaw/workspace -s tiger --phase close-path

# With explicit cron config file
python3 diagnostics/scripts/run-diagnostics.py -w ~/.openclaw/workspace -s tiger --cron-file /path/to/jobs.json

# Specific animal (if multiple animals per suite)
python3 diagnostics/scripts/run-diagnostics.py -w ~/.openclaw/workspace -s tiger -a tiger-alpha
```

### Environment Variables

| Variable           | Default                                 | Purpose                                      |
| ------------------ | --------------------------------------- | -------------------------------------------- |
| `WORKSPACE`        | `/home/arnold/.openclaw/workspace`      | Workspace root (override with `--workspace`) |
| `DIAG_PYTHON`      | `/home/arnold/openclaw/venv/bin/python` | Python interpreter to use                    |
| `MCPORTER_CMD`     | `mcporter` (PATH)                       | mcporter binary/wrapper path                 |
| `NPM_CONFIG_CACHE` | `/tmp/npm-cache`                        | Prevents npm from writing to read-only home  |

### Exit Codes

- `0` — All critical tests passed. Safe to trade.
- `1` — One or more critical failures. **DO NOT enable trading.**

## Output

### Human-readable (default)

```
============================================================
  SENPI DIAGNOSTICS SUITE
  Suite: tiger  Animal: tiger-alpha
============================================================

--- 1-Environment ---

  [PASS] python_venv
         Python 3.11.2
  [PASS] gate_adapter
         Gate lists senpi server
  ...

--- 6-Close-Path ---

  [PASS] close_path_gate
         Gate close_position chain works (got expected API rejection)
  [FAIL [CRITICAL]] close_path_mcporter_cmd
         MCPORTER_CMD does NOT route through gate
         FIX: MCPORTER_CMD must point to wrapper that routes through gate

============================================================
  UNSAFE TO TRADE -- 1 critical failure(s)
    - close_path_mcporter_cmd: MCPORTER_CMD does NOT route through gate
  Total: 45230ms
============================================================
```

### JSON (with `--json`)

```json
{
  "suite": "tiger",
  "animal": "tiger-alpha",
  "timestamp": "2026-03-01T...",
  "safe_to_trade": false,
  "phases": [...],
  "summary": {
    "total": 30,
    "passed": 29,
    "failed": 1,
    "critical_failures": 1,
    "duration_ms": 45230,
    "blockers": ["MCPORTER_CMD does NOT route through gate"]
  }
}
```

## Cron Setup

Run diagnostics every 6 hours or before enabling a strategy:

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
    "message": "Run the Senpi diagnostics suite: MCPORTER_CMD=/home/arnold/.openclaw/workspace/scripts/mcporter-senpi-wrapper.sh DIAG_PYTHON=/home/arnold/openclaw/venv/bin/python /home/arnold/openclaw/venv/bin/python /home/arnold/.openclaw/workspace/skills/senpi-trading/senpi-skills/diagnostics/scripts/run-diagnostics.py --workspace /home/arnold/.openclaw/workspace --suite tiger --json --cron-file /home/arnold/.openclaw/cron/jobs.json. Parse JSON output. If safe_to_trade=false: ALERT Robbie immediately in Discord with the blockers list. If safe_to_trade=true: HEARTBEAT_OK.",
    "model": "anthropic/claude-haiku-4-5"
  },
  "delivery": {
    "mode": "announce",
    "channel": "discord:channel:1477183852077383762"
  }
}
```

## Files

```
diagnostics/
  SKILL.md                         # This file
  scripts/
    run-diagnostics.py             # Main entry point (~800 lines)
    diag_lib.py                    # Discovery engine, test registry, helpers
  references/
    cron-template.md               # Cron job setup (same as above)
```

## Adding Tests

Tests are organized by phase in `run-diagnostics.py`. Each phase is a function (`phase_environment`, `phase_cron_config`, etc.) that calls `diag.record()` for each test result.

To add a new test:

1. Find the appropriate phase function
2. Call `diag.record(name, phase, passed, critical=True/False, detail="...", remediation="...")`
3. For new scripts to audit, add them to the script collection in `phase_source_audit`
4. For new scanner expectations, add to `TIGER_SCRIPT_EXPECTATIONS` dict
