# Runtime CLI — `openclaw senpi …`

The complete command surface for interacting with the Senpi trading runtime (`@senpi-ai/runtime`).
The plugin registers a `senpi` command group on the OpenClaw gateway, so every command is invoked as
**`openclaw senpi <group> <subcommand>`**. Most read commands accept `--json` to print the raw
gateway payload, and `-r/--runtime <id>` / `-a/--address <wallet>` to filter to one runtime.

## Install

```bash
openclaw plugins install @senpi-ai/runtime
```

## `senpi config` — manage CLI preferences

Stored in `~/.openclaw/senpi-cli.json`; changes require a gateway restart to apply.

| Command | What it does |
|---|---|
| `config set-chat-id <chatId>` | Store the Telegram chat id for lifecycle notifications |
| `config set-senpi-jwt-token <token>` | Store the Senpi MCP JWT (Bearer); falls back after `openclaw.json` apiKey |
| `config set-state-dir <dir>` | Set the base Senpi state directory (registry + per-runtime state) |
| `config get <key>` | Print one stored value (`telegram-chat-id`, `senpi-jwt-token`, `state-dir`) |
| `config list` | List all preferences (secrets masked) |
| `config unset <key>` | Remove a key |
| `config reset` | Clear all preferences |

## `senpi runtime` — manage runtimes

| Command | What it does | Options |
|---|---|---|
| `runtime create` | Add a runtime from a YAML file (or pasted YAML); the gateway validates and runs it. Hot-loads — no gateway restart. | `-p, --path <path>` (path to the runtime.yaml) · `-c, --content <yaml>` (paste YAML directly) · `--runtime-id <id>` (name; default derived from the file name/content) |
| `runtime list` | List all installed runtimes with their id, source, and status (running/stopped). | — |
| `runtime delete [runtime_id]` | Remove a runtime by id or wallet address. | `--id <runtime_id>` (from `runtime list`) · `--address <wallet>` |

```bash
openclaw senpi runtime create -p runtime.yaml     # deploy / hot-load
openclaw senpi runtime list                        # verify it is running
openclaw senpi runtime delete --id iguana-tracker  # tear down
```

## `senpi dsl` — inspect the DSL exit engine

| Command | What it does | Options |
|---|---|---|
| `dsl positions` | List active DSL-tracked positions across running runtimes. | `-r, --runtime <id>` · `-a, --address <addr>` · `--json` |
| `dsl inspect <asset>` | Show the full DSL state for an open position by asset symbol (e.g. `SOL`, `BTC`, `xyz:GOLD`). | `-r` · `-a` · `--json` |
| `dsl closes` | List archived (closed) DSL positions with close reason and ROE. | `-r` · `-a` · `-l, --limit <n>` (max rows after merge) · `--json` |

## `senpi action` — inspect the action layer

| Command | What it does | Options |
|---|---|---|
| `action list` | List registered actions with health and counters. | `-r` · `-a` · `--json` |
| `action inspect <actionName>` | Show the persisted latest state for one action (name from the runtime.yaml). | `-r` (required when multiple runtimes run) · `-a` (defaults to the runtime wallet) · `--json` |
| `action history [actionName]` | Rolling execution history with decision audit fields. Omit the name to merge all actions for the runtime. | `-r` · `-a` · `-l, --limit <n>` (default 50) · `--json` |
| `action decisions [actionName]` | History rows where the decision engine ran (reasoning in JSON). | `-r` · `-a` · `-l` (default 50) · `--json` |

## `senpi risk` — risk eligibility

| Command | What it does | Options |
|---|---|---|
| `risk` | Whether the runtime is allowed to trade (eligibility OPEN/COOLDOWN/CLOSED), gate totals, and per-gate status/reason — plus the evaluation faults (`failureKind`, fallback applied) the `status` summary hides. | `-r, --runtime <id>` · `--json` |

## `senpi scanner` — per-scanner supervisor health

| Command | What it does | Options |
|---|---|---|
| `scanner` | Per-scanner health: schedule mode, run/error/consecutive-error counts, next-run time, in-flight, cumulative `signals` produced, and external-scanner `alive` (heartbeat from the intake liveness clock; `n/a` for interval scanners). Flags a `BARREN` scanner — alive and has run but produced no signals. Reuses the `state` RPC. | `-r, --runtime <id>` · `--json` |

## `senpi audit` — backend trade-audit trail

| Command | What it does | Options |
|---|---|---|
| `audit` | Backend trade trail: MCP tool calls with success, duration, and AI reasoning. Compact table by default. | `-r, --runtime <id>` (required) · `--tool <name>` · `--action-type <read\|create\|update\|delete>` · `--success <bool>` · `--since <iso>` · `--until <iso>` · `-l, --limit <n>` (default 50) · `--json` |

## `senpi status` / `senpi state` — runtime health

| Command | What it does | Options |
|---|---|---|
| `status` | Lightweight runtime health for running runtimes. | `-r, --runtime <id>` · `--json` |
| `state` | Full runtime state for running runtimes. | `-r` · `--json` |

## `senpi skills` — manage Senpi skills

| Command | What it does | Options |
|---|---|---|
| `skills status` | Show installed skills, their versions, and any pending updates. | `--json` |
| `skills update <name>` | Force an immediate version check + apply for one skill by name. | `--json` |
| `skills update-all` | Force an immediate version check + apply for all managed skills. | `--json` |

## `senpi guide` — in-shell reference

Printed reference for `@senpi-ai/runtime`, available on the host without leaving the shell:

| Command | Prints |
|---|---|
| `guide scanners` | Scanner types and config fields |
| `guide actions` | Action types and decision modes |
| `guide dsl` | DSL exit engine: phases, lock modes, tiers, time-based cuts |
| `guide examples` | A minimal strategy YAML to stdout |
| `guide schema` | The full YAML schema field reference |
| `guide version` | Plugin version and changelog URL |

`openclaw senpi --cheatsheet` prints the whole command cheatsheet.

> Each CLI command wraps a gateway RPC (`openclaw gateway call senpi.<method>`). The CLI is the
> supported human-facing surface; reach for the raw RPCs only for programmatic automation.
