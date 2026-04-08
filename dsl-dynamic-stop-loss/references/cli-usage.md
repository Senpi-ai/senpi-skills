# DSL CLI Usage

`dsl-cli.py` provides lifecycle commands for Dynamic Stop Loss. Use it from the agent or from other skills (e.g. `wolf-strategy`, `dsl-tight`) to add, update, pause, resume, delete, and inspect DSL without editing state files by hand.

**Crons are OpenClaw crons.** This skill handles cron lifecycle: the CLI generates and stores the cron job ID when needed, and outputs when to create or remove a cron; the agent creates or removes that cron **in OpenClaw** using the ID from the CLI output. There is no separate "cron by MCP" — MCP (Senpi) is used for strategy/clearinghouse/prices/close; OpenClaw is the scheduler.

## Global option

| Option | Default | Description |
|--------|---------|-------------|
| `--state-dir` | `$DSL_STATE_DIR` or `/data/workspace/dsl` | Base directory for DSL state |

## Commands

### add-dsl

Set up DSL for a strategy. Creates strategy config `strategy-<strategy-id>.json` and per-position state files. Outputs cron intent when the strategy has no cron yet.

```bash
# All current positions (CLI fetches wallet + positions from MCP)
python3 scripts/dsl-cli.py add-dsl <strategy-id> --skill wolf-strategy --configuration @/path/to/dsl-profile.json

# Single position
python3 scripts/dsl-cli.py add-dsl <strategy-id> <asset> <dex> --skill wolf-strategy --configuration @/path/to/dsl-profile.json

# With entry price override (e.g. from multi-fill average)
python3 scripts/dsl-cli.py add-dsl <strategy-id> ETH main --skill wolf-strategy --entry-price 3012.45 --configuration @/path/to/dsl-profile.json

# With config file (recommended)
python3 scripts/dsl-cli.py add-dsl <strategy-id> --configuration @/path/to/dsl-profile.json
```

**Output when cron is needed:** `cron_needed: true`, `cron_job_id`, `cron_env`, `cron_schedule`, `cron_interval_minutes`. The schedule is derived from `cronIntervalMinutes` (default 3) in the configuration — this is a **general** setting for how often the DSL runs, not part of phase1. The agent must create the **OpenClaw** cron with that ID and the output env/schedule.

### update-dsl

Update DSL configuration. Strategy-level update patches `defaultConfig` and all active position state files (config fields only; runtime state preserved). Position-level update patches only that position's state file.

```bash
# Strategy-wide
python3 scripts/dsl-cli.py update-dsl <strategy-id> --configuration '{"phase1":{"retraceThreshold":0.05}}'

# Single position
python3 scripts/dsl-cli.py update-dsl <strategy-id> ETH main --configuration @/path/to/override.json
```

If `phase1.retraceThreshold` is changed and `absoluteFloor` is not in the patch, `absoluteFloor` is recalculated from entry price, leverage, and the new retrace.

**When `cronIntervalMinutes` is changed (strategy-level update):** The CLI outputs `cron_schedule_changed: true`, `cron_to_remove` (so the agent removes the existing OpenClaw cron), and `cron_needed: true` with the same `cron_job_id`, new `cron_schedule`, and `cron_env`. The agent must delete the old cron and create a new one with the new interval.

### pause-dsl / resume-dsl

Pause or resume DSL monitoring. The cron keeps running; `dsl-v5.py` skips positions with `active: false`. Runtime state (highWater, tier, etc.) is preserved. The SL order on Hyperliquid (and `slOrderId` in state) is set only by the cron runner when it processes the position — so if a position was paused right after add-dsl, the next cron run after resume (~within 3 min) will sync the floor to Hyperliquid and populate `slOrderId`.

```bash
python3 scripts/dsl-cli.py pause-dsl <strategy-id>
python3 scripts/dsl-cli.py pause-dsl <strategy-id> ETH main

python3 scripts/dsl-cli.py resume-dsl <strategy-id>
python3 scripts/dsl-cli.py resume-dsl <strategy-id> xyz:SILVER xyz
```

### delete-dsl

Tear down DSL. Strategy-level: archive all position state files and the strategy config (`strategy-<strategy-id>.json`), output `cron_to_remove`, run `dsl-cleanup.py`. Position-level: archive that position's state file; if no active positions remain, output `cron_to_remove` and run cleanup.

```bash
python3 scripts/dsl-cli.py delete-dsl <strategy-id>
python3 scripts/dsl-cli.py delete-dsl <strategy-id> ETH main
```

**Output:** `cron_to_remove` when the agent must remove the OpenClaw cron for this strategy.

### status-dsl / count-dsl

Report current status or aggregate counts.

```bash
python3 scripts/dsl-cli.py status-dsl <strategy-id>
python3 scripts/dsl-cli.py status-dsl <strategy-id> ETH main
python3 scripts/dsl-cli.py count-dsl <strategy-id>
```

## Inter-skill integration

1. **Locate `dsl-cli.py`** — e.g. `dsl-dynamic-stop-loss/scripts/dsl-cli.py` in the workspace/skills tree.
2. **Locate your `dsl-profile.json`** — in your skill directory (e.g. `wolf-strategy/dsl-profile.json`). Resolve absolute path at runtime.
3. **Call add-dsl** with `--configuration @<absolute-path>` and `--skill <your-skill-name>`.
4. **Read stdout JSON** — if `cron_needed: true`, the output includes `cron_job_id` (already stored by the CLI). Agent creates the OpenClaw cron with that ID and the output env/schedule.
5. **Cron lifecycle** — this skill generates the cron ID and emits when to create/remove a cron; the agent creates/removes the cron in OpenClaw using the ID from the CLI.

### Example (wolf-strategy, single position)

```bash
DSL_CLI=/path/to/dsl-dynamic-stop-loss/scripts/dsl-cli.py
WOLF_PROFILE=/path/to/wolf-strategy/dsl-profile.json

python3 $DSL_CLI add-dsl abc-strategy-123 ETH main \
  --skill wolf-strategy \
  --configuration @$WOLF_PROFILE
```

### Example (all positions)

```bash
python3 $DSL_CLI add-dsl abc-strategy-123 \
  --skill wolf-strategy \
  --configuration @$WOLF_PROFILE
```

### Partial override on top of profile

```bash
python3 $DSL_CLI add-dsl abc-strategy-123 ETH main \
  --skill wolf-strategy \
  --configuration @$WOLF_PROFILE \
  --configuration '{"phase1":{"retraceThreshold":0.05}}'
```

Multiple `--configuration` values are merged; later values override earlier (field-level). `tiers` is replaced entirely when present.

## Configuration resolution

Precedence (highest wins): `--configuration` (inline or `@file`) → strategy `defaultConfig` → hardcoded defaults. Tiers are replaced as a whole, not merged element-by-element. `absoluteFloor` is auto-calculated from `entryPrice`, `leverage`, and `phase1.retraceThreshold` unless explicitly set in config.

See [design/dsl-cli-commands.md](../design/dsl-cli-commands.md) for full configuration schema.

## Concurrency and consistency

- **Strategy config `positions`:** The CLI reconciles `positions` to the current position state files on disk whenever it saves the strategy config. So after dsl-v5 archives a file (breach, external close, or SL filled), the next CLI run that writes the strategy config will drop that asset from `positions`. No separate sync step is required.
- **Concurrent CLI and cron:** There is no file locking. If the cron runner (`dsl-v5.py`) and the CLI run at the same time, the last write wins. In practice, run CLI mutations (add/update/pause/resume/delete) from a single agent or process per strategy; the cron only reads state and archives files, so overlap is usually harmless.
- **add-dsl and clearinghouse:** Positions are taken from a single clearinghouse snapshot. If a position is closed (or added) after that snapshot but before the CLI writes files, the next cron run will reconcile: missing state files for open positions yield no processing; state files for closed positions get archived by dsl-v5.
