# DSL v5.1 — Examples

Practical examples for the CLI and monitor. Replace `strategy-id`, `asset`, and paths with your values. Position must already exist in clearinghouse before `add-dsl`.

## add-dsl

### Minimal (default preset, no config override)

```bash
python3 scripts/dsl-v5.py add-dsl default \
  --strategy-id strat-abc-123 \
  --asset ETH \
  --direction LONG \
  --leverage 10 \
  --margin 500
```

Uses 6 default tiers, phase1/phase2 breach=1, retrace 3%/1.5%. Entry/size/wallet come from clearinghouse.

### With preset and optional config

```bash
python3 scripts/dsl-v5.py add-dsl dsl-tight \
  --strategy-id strat-abc-123 \
  --asset HYPE \
  --direction LONG \
  --leverage 10 \
  --margin 500 \
  --config '{
    "phase1": {"retraceThreshold": 0.05, "consecutiveBreachesRequired": 3},
    "phase2": {"retraceThreshold": 0.02, "consecutiveBreachesRequired": 2},
    "tiers": [
      {"triggerPct": 10, "lockPct": 50, "retrace": 0.015, "breachesRequired": 3},
      {"triggerPct": 20, "lockPct": 65, "retrace": 0.012, "breachesRequired": 2},
      {"triggerPct": 40, "lockPct": 75, "retrace": 0.010, "breachesRequired": 2},
      {"triggerPct": 75, "lockPct": 85, "retrace": 0.006, "breachesRequired": 1}
    ]
  }'
```

### XYZ dex asset (two ways)

**Option 1 — prefix in asset:** DEX is inferred from `xyz:`.

```bash
python3 scripts/dsl-v5.py add-dsl default \
  --strategy-id strat-abc-123 \
  --asset xyz:SILVER \
  --direction SHORT \
  --leverage 5 \
  --margin 200
```

**Option 2 — separate `--dex`:** Use when the ticker has no prefix (e.g. `SILVER` on xyz). Canonical asset becomes `xyz:SILVER`; state file `xyz--SILVER.json`.

```bash
python3 scripts/dsl-v5.py add-dsl default \
  --strategy-id strat-abc-123 \
  --asset SILVER \
  --dex xyz \
  --direction SHORT \
  --leverage 5 \
  --margin 200
```

State file is always `xyz--SILVER.json` under the strategy dir for xyz SILVER. Main dex: `--asset ETH` (no `--dex` or `--dex main`/`''`).

### Example add-dsl output

```json
{
  "action": "add-dsl",
  "status": "ok",
  "preset": "dsl-tight",
  "asset": "HYPE",
  "strategy_id": "strat-abc-123",
  "state_file": "/data/workspace/dsl/strat-abc-123/HYPE.json",
  "is_first_position_for_strategy": true,
  "cron_needed": true,
  "cron_env": {
    "DSL_STATE_DIR": "/data/workspace/dsl",
    "DSL_STRATEGY_ID": "strat-abc-123",
    "DSL_PRESET": "dsl-tight"
  },
  "cron_schedule": "*/3 * * * *"
}
```

If `is_first_position_for_strategy` is true, create one cron with `cron_env`; otherwise reuse the existing strategy cron.

---

## update-dsl

### Tighten retrace for one asset

```bash
python3 scripts/dsl-v5.py update-dsl \
  --strategy-id strat-abc-123 \
  --asset HYPE \
  --config '{"phase2": {"retraceThreshold": 0.01}}'
```

For xyz asset use `--asset xyz:SILVER` or `--asset SILVER --dex xyz`.

### Update all positions in strategy (e.g. more breach tolerance)

```bash
python3 scripts/dsl-v5.py update-dsl \
  --strategy-id strat-abc-123 \
  --config '{"phase1": {"consecutiveBreachesRequired": 3}}'
```

Omit `--asset` to apply to every state file in the strategy dir.

---

## pause-dsl / resume-dsl

### Pause one position

```bash
python3 scripts/dsl-v5.py pause-dsl --strategy-id strat-abc-123 --asset ETH
```

For xyz: `--asset xyz:SILVER` or `--asset SILVER --dex xyz`.

### Resume all positions for a strategy

```bash
python3 scripts/dsl-v5.py resume-dsl --strategy-id strat-abc-123
```

---

## status-dsl

### One position (pretty-printed)

```bash
python3 scripts/dsl-v5.py status-dsl --strategy-id strat-abc-123 --asset HYPE
```

### All positions (one JSON line per position)

```bash
python3 scripts/dsl-v5.py status-dsl --strategy-id strat-abc-123
```

---

## delete-dsl

### Remove DSL for one asset

```bash
python3 scripts/dsl-v5.py delete-dsl --strategy-id strat-abc-123 --asset ETH
```

For xyz: `--asset xyz:SILVER` or `--asset SILVER --dex xyz`.

### Remove all DSL state for a strategy

```bash
python3 scripts/dsl-v5.py delete-dsl --strategy-id strat-abc-123
```

Deletes all state files in the strategy dir; removes the strategy dir if empty.

---

## Monitor (cron)

Cron runs the script with no subcommand. Env vars: `DSL_STATE_DIR`, `DSL_STRATEGY_ID`, optionally `DSL_PRESET`.

```bash
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 DSL_PRESET=dsl-tight python3 /path/to/dsl-dynamic-stop-loss/scripts/dsl-v5.py
```

Output is one JSON line per position (ndjson), or one strategy-level line (`strategy_inactive`, `no_positions`, or `error`).

---

## Using --state-dir and --strategy-id

All subcommands accept `--state-dir` and `--strategy-id`; if omitted they fall back to `DSL_STATE_DIR` and `DSL_STRATEGY_ID` env vars.

```bash
python3 scripts/dsl-v5.py add-dsl default \
  --state-dir /data/workspace/dsl \
  --strategy-id strat-abc-123 \
  --asset ETH --direction LONG --leverage 10 --margin 500
```
