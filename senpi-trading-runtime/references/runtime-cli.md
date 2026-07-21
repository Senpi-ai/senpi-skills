# Runtime CLI — `openclaw senpi …` (rung-2 raw plumbing)

The complete raw command surface for the Senpi trading runtime (`@senpi-ai/runtime`). The plugin
registers a `senpi` command group on the OpenClaw gateway, so every command is invoked as
**`openclaw senpi <group> <subcommand>`**. Most read commands accept `--json` to print the raw
gateway payload, and `-r/--runtime <id>` / `-a/--address <wallet>` to filter to one runtime.

> **This is rung 2.** For any strategy-scoped question, the front door is the composer:
> `openclaw senpi composer status|review …` (via the `senpi_strategy` tool) — it renders the
> lifecycle chain, scanner health, protection, and risk gates and is relayed verbatim. Reach for the
> raw `senpi state|scanner|dsl|action|risk|status` commands below only when `composer status` flags
> trouble it cannot explain. The composer also owns **deploy / install / update / teardown**
> (`composer deploy|install|update|close`); the `senpi runtime` verbs below are the raw plumbing it
> drives, not the strategy-lifecycle path — never hand-deploy a hand-written `runtime.yaml` for new
> work. Raw outputs are relayed verbatim, never re-derived.

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
| `runtime create` | Raw: add a runtime from a YAML file (or pasted YAML); the gateway validates and runs it. Hot-loads — no gateway restart. **Composer `deploy`/`install` is the front door for strategies; use this only for low-level inspection/repair.** | `-p, --path <path>` (path to the runtime.yaml) · `-c, --content <yaml>` (paste YAML directly) · `--runtime-id <id>` (name; default derived from the file name/content) |
| `runtime list` | List all installed runtimes with their id, source, and status (running/stopped). | — |
| `runtime delete [runtime_id]` | Raw removal by id or wallet address. **For strategy teardown use `composer close`** (it stops the runtime, confirms it is gone, then flattens/returns funds); raw delete leaves the strategy record behind. | `--id <runtime_id>` (from `runtime list`) · `--address <wallet>` |

```bash
openclaw senpi runtime list                        # raw: is a runtime running?
openclaw senpi runtime create -p runtime.yaml      # raw hot-load (NOT the strategy-deploy path — that's composer deploy/install)
openclaw senpi runtime delete --id iguana-tracker  # raw teardown (strategy teardown = composer close)
```

## `senpi dsl` — inspect the DSL exit engine

| Command | What it does | Options |
|---|---|---|
| `dsl positions` | List active DSL-tracked positions across running runtimes. | `-r, --runtime <id>` · `-a, --address <addr>` · `--json` |
| `dsl inspect <asset>` | Show the full DSL state for an open position by asset symbol (e.g. `SOL`, `BTC`, `xyz:GOLD`). | `-r` · `-a` · `--json` |
| `dsl closes` | List archived (closed) DSL positions with close reason and ROE. | `-r` · `-a` · `-l, --limit <n>` (max rows after merge) · `--json` |

**"Are my open positions actually protected by DSL?"** → the step-by-step verdict (PROTECTED /
UNPROTECTED / STOP-NOT-ON-VENUE), incl. the open-vs-tracked reconciliation an unprotected position hides
behind: **`references/dsl-protection-check.md`**.

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
| `risk audit` | Per-wallet gate-check audit log — the decision *history* behind the live `risk` view, as a digest (time, source, `guardrail=result` with reasons). Renders "No risk audit." until the first gate check runs. | `-r, --runtime <id>` (required) · `-a, --address <addr>` · `--since <iso>` · `-l, --limit <n>` (default 100) · `--json` |

## `senpi scanner` — per-scanner supervisor health

| Command | What it does | Options |
|---|---|---|
| `scanner` | Per-scanner health: schedule mode, external-scanner cadence rendered explicitly (e.g. "checks every 300s (external)" — the legacy `interval=0s` render is gone), last-post time + quiet-check count (so "last check 2m ago, no signal" is expressible), run/error/consecutive-error counts incl. `errorCount`/`lastError` (an erroring `scan.py` is now distinguishable from a healthy-quiet one), next-run time, in-flight, and cumulative `signals` produced. Flags a `BARREN` scanner — alive and has run but produced no signals. Reuses the `state` RPC. | `-r, --runtime <id>` · `--json` |

## `senpi audit` — backend trade-audit trail

| Command | What it does | Options |
|---|---|---|
| `audit` | Backend trade trail: MCP tool calls with success, duration, and AI reasoning. Compact table by default. | `-r, --runtime <id>` (required) · `--tool <name>` · `--action-type <read\|create\|update\|delete>` · `--success <bool>` · `--since <iso>` · `--until <iso>` · `-l, --limit <n>` (default 50) · `--json` |

## `senpi events` / `senpi explain` — local domain-event log

The trade narrative (position/dsl/order/signal/runtime events) is persisted to a per-strategy on-disk ring, queryable locally without the collector. Every event is stored as its body + capped scalars; only the redacted free-text slot (LLM reasoning, venue errors) stays collector-only. Because signal processing is strictly serial, the time-ordered window already reflects causal order.

| Command | What it does | Options |
|---|---|---|
| `events` | The domain-event log as a table (time, level, event, asset, narrative). | `-r, --runtime <id>` (required) · `-a, --address <addr>` · `--name <event>` · `--asset <symbol>` · `--level <debug\|info\|warn\|error>` · `--since <iso>` · `--until <iso>` · `-l, --limit <n>` (default 200) · `--json` |
| `explain <asset>` | Stitch one asset's position lifecycle (opened → dsl transitions → close+reason) into a chronological narrative tagged by position id. | `-r, --runtime <id>` (required) · `-a, --address <addr>` · `-l, --limit <n>` (events to scan, default 500) · `--json` |

## `senpi status` / `senpi state` — raw runtime health (rung 2)

> The strategy-facing health front door is **`composer status`**, which absorbs the gateway system
> state plus `dsl positions/inspect` and renders a strategy-language summary (lifecycle chain,
> per-scanner cadence/last-check/quiet-check, protection in plain ROE, risk gates) that is relayed
> verbatim. The raw `senpi status`/`senpi state` below are the rung-2 escape hatch — reach for them
> only when `composer status` flags trouble it cannot explain, and do not re-interpret their fields
> (composer status is the authority on health/protection semantics).

| Command | What it does | Options |
|---|---|---|
| `status` | Raw runtime health digest for running runtimes: overall health, scanner summary (with a degraded-scanner count), DSL monitor liveness (running/stopped, tick-in-flight, next tick, last tick error), and — when risk is enabled — trade eligibility (OPEN/COOLDOWN/CLOSED) and the per-gate table. | `-r, --runtime <id>` · `--json` |
| `state` | Full raw runtime state for running runtimes — the lowest-level escape hatch when the `status` digest isn't enough. | `-r` · `--json` |

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
