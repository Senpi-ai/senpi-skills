# senpi-dsl-plugin — Integration Guide

Dynamic Stop-Loss (DSL) runs as an OpenClaw plugin. When developing outside the OpenClaw monorepo, set `devDependencies.openclaw` to a version (e.g. `"latest"`) in `package.json` so install succeeds. See [design/TESTING.md](design/TESTING.md) for how to test the plugin. The plugin is **100% TypeScript**; it uses **mcporter** (MCP) for Senpi/Hyperliquid and does not run any Python. Skills and agents interact with it **only via the CLI**; they do not manage crons directly.

## Config

In `~/.openclaw/config.yaml`:

```yaml
plugins:
  senpi-dsl:
    stateDir: /data/workspace/dsl
    schedule: "*/3 * * * *"
    alertChannelId: null   # optional channel ID for alerts
```

- **stateDir** — Directory for per-strategy state and archive. Default: `/data/workspace/dsl`.
- **schedule** — Cron expression for monitor ticks. Only `*/N * * * *` (every N minutes) is parsed; otherwise 3 minutes is used.
- **alertChannelId** — If set, key events (closed, breached, tier_changed, error) are sent to this channel.

## CLI

All commands are under `openclaw senpi-dsl`:

| Command | Required flags | Optional | Purpose |
|--------|----------------|----------|---------|
| `add-dsl [preset]` | `--strategy-id`, `--asset`, `--direction` (LONG or SHORT) | `--leverage`, `--margin`, `--dex`, `--config` | Add DSL for a position. Leverage defaults to 1; margin is derived from position (entry×size/leverage) if omitted. First position starts the cron. |
| `update-dsl` | `--strategy-id`, `--config` | `--asset`, `--dex` | Update DSL config for position(s). |
| `pause-dsl` | `--strategy-id` | `--asset`, `--dex` | Pause DSL for position(s). |
| `resume-dsl` | `--strategy-id` | `--asset`, `--dex` | Resume DSL for position(s). |
| `delete-dsl` | `--strategy-id` | `--asset`, `--dex` | Remove state for one position (`--asset`) or all positions in the strategy (omit `--asset`). Cron stops when no positions left. |
| `status-dsl` | `--strategy-id` | `--asset`, `--dex` | Print DSL status (JSON). |

**Note:** `add-dsl` requires only `--strategy-id`, `--asset`, and `--direction`; `--leverage` and `--margin` are optional (margin is derived from the position when omitted). `delete-dsl` only needs `--strategy-id` and optional `--asset`. For full option details see the DSL skill reference (e.g. `references/cli-options.md` in the skill).

## Example (skill/agent usage)

```bash
# Add DSL for a position — only strategy-id, asset, and direction are required
openclaw senpi-dsl add-dsl wolf \
  --strategy-id "$STRATEGY_ID" --asset "$ASSET" \
  --direction LONG

# Optional: leverage/margin (default: leverage=1, margin=entry×size/leverage from position)
openclaw senpi-dsl add-dsl wolf \
  --strategy-id "$STRATEGY_ID" --asset "$ASSET" \
  --direction LONG --leverage 10 --margin 100

# Optional: custom tiers via --config
openclaw senpi-dsl add-dsl wolf \
  --strategy-id "$STRATEGY_ID" --asset ETH \
  --direction LONG \
  --config '{"tiers":[{"triggerPct":10,"lockPct":5},{"triggerPct":20,"lockPct":14}]}'

# Check status
openclaw senpi-dsl status-dsl --strategy-id "$STRATEGY_ID" --asset "$ASSET"

# Remove DSL: only strategy-id required; use --asset to remove one position (omit to remove all for strategy)
openclaw senpi-dsl delete-dsl --strategy-id "$STRATEGY_ID" --asset "$ASSET"
```

## What the plugin does

- **On gateway start:** Scans `stateDir` for strategy dirs with at least one `.json` file, starts a cron per strategy.
- **Each tick:** Runs the DSL engine (TypeScript) for that strategy; logs NDJSON output; optionally sends alerts for closed/breached/tier_changed/error; stops the cron when the strategy is inactive.
- **On gateway stop:** Stops all crons.

Skills **no longer need to:**

- Call Python scripts directly
- Create or manage crons
- Parse NDJSON or send DSL alerts (unless they want custom handling in addition to the plugin)

## State and archive

- Active state: `{stateDir}/{strategyId}/{asset}.json` (or `xyz--SYMBOL.json` for xyz).
- When a position closes or is removed, the state file is **archived** to `{stateDir}/archive/{strategyId}/{asset}-{timestamp}.json` instead of deleted.
