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

## `senpi deploy` — take a strategy package live (the deploy path)

One verb, run as a **detached job**: reconcile → funds preflight → wallet create+fund (carrying the
package's `skillName`/`skillVersion` attribution) → install → one **observed** scanner tick. It
returns in ~1s with a `deployId`; you poll `deploy status` until the job is terminal. Package-level
gates run **pre-money** — no DSL exit on an instance, an unsupported scanner-level `enabled` key, and
the live-universe check (`[E_UNIVERSE_NOT_LIVE]`, fail-closed when the instrument list is unreadable).

| Command | What it does | Options |
|---|---|---|
| `deploy -p <package-dir>` | Start the deploy job for a strategy package. One job per agent (a second refuses `[E_DEPLOY_IN_PROGRESS]`); re-running reconciles and adopts what exists, it never duplicates. There is no `cancel` — undeploying is closing the strategy. | `--budget <usd>` (split across instances by `funding_share`, min $10/wallet) · `--max-wait <s>` (wallet-ACTIVE budget, default 150) · `--tick-wait <s>` (observe budget, default 120; `0` skips and can never report `live`) · `--decision-model <model>` · `--json` |
| `deploy status [deployId]` | The job's phase while running, the full verified report once terminal. **Read-only** — it starts nothing. | `--json` |

Exit codes (`deploy status` and `deploy.py` alike): `0` live · `2` refused · `3` failed · `4`
installed-unobserved · `5` interrupted · `6` pending/still running · `1` internal/transport error.
`deploy status` sets the JOB's code and then prints the snapshot, on `--json` too — a non-zero code
there is a verdict about the deploy, not a failed read.

**Deploy through `senpi-strategy-ops/scripts/deploy.py create <id> --budget <usd>`**, which resolves
the package (a bare catalog id is fetched), runs the structural preflight and drives this verb.

## `senpi runtime` — manage runtimes

| Command | What it does | Options |
|---|---|---|
| `runtime create` | **Internal — not the deploy path.** Adds a runtime from a YAML file (or pasted YAML) and hot-loads it, skipping the funds preflight, the attribution and the verified tick that `senpi deploy` performs. Pasted as content (`-c`) with no `--runtime-yaml-dir` it also leaves a relative scanner path nothing to resolve against and the install gate refuses `[E_VALIDATE_UNRESOLVABLE_SCANNER_PATH]` — `-p` derives that directory from the file, so only the content form breaks. | `-p, --path <path>` (path to the runtime.yaml) · `-c, --content <yaml>` (paste YAML directly) · `--runtime-id <id>` (name; default derived from the file name/content) · `--runtime-yaml-dir <dir>` (directory to resolve relative scanner paths against — content installs only; `-p` derives it) |
| `runtime list` | List all installed runtimes with their id, source, and status (running/stopped). A runtime whose entry scanners failed to wire shows `running — NO ENTRY SCANNERS`: the runtime is up but cannot produce entry signals — `senpi status` names the failed phase; check `senpi events` for the failure. | — |
| `runtime delete [runtime_id]` | Remove a runtime by id or wallet address. | `--id <runtime_id>` (from `runtime list`) · `--address <wallet>` |

```bash
openclaw senpi deploy -p strategies/spider --budget 300   # the deploy path (detached; then: deploy status)
openclaw senpi runtime list                               # verify it is running
openclaw senpi runtime delete --id iguana-tracker         # tear down (packages: close.py <id>, which also closes the strategy)
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
| `scanner` | Per-scanner health: schedule mode, run/error/consecutive-error counts, next-run time, in-flight, cumulative `signals` produced, and external-scanner `alive` (heartbeat from the intake liveness clock; `n/a` for interval scanners). A scanner that is alive and has run but produced no signals is flagged `(no signals yet)`. Reuses the `state` RPC. | `-r, --runtime <id>` · `--json` |

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

## `senpi status` / `senpi state` — runtime health

| Command | What it does | Options |
|---|---|---|
| `status` | Lightweight runtime health digest for running runtimes: overall health, scanner summary (with a degraded-scanner count) followed by one line per non-healthy scanner carrying its restart count and crash-loop cause, DSL monitor liveness (running/stopped, tick-in-flight, next tick, last tick error), and — when risk is enabled — trade eligibility (OPEN/COOLDOWN/CLOSED) and the per-gate table. Scanner health is fail-closed: an external scanner never proven by a recent tick reads `unknown`, not `healthy`; when entry scanners never wired the scanner line reads `running — NO ENTRY SCANNERS (<phase> failed; see events)`. | `-r, --runtime <id>` · `--json` |
| `state` | Full runtime state for running runtimes — the escape hatch when the `status` digest isn't enough. | `-r` · `--json` |

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
