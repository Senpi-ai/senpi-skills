---
name: senpi-trading-runtime
description: "Configure, deploy, and manage runtimes in the Senpi Trading Runtime OpenClaw plugin for automated on-chain position tracking with DSL trailing stop-loss protection. Use when a user needs to create or modify runtime YAML files, configure DSL (Dynamic Stop-Loss) exit engine parameters (phases, tiers, time-based cuts), set up the position_tracker scanner to monitor a wallet's positions on Hyperliquid, install/list/delete runtimes via CLI, inspect DSL-tracked positions, check runtime health and system state, or write a Python external_scanner producer using the bundled senpi_runtime_helpers SDK. Triggers on mentions of senpi, trading runtime, DSL exit, stop-loss tiers, position tracker, trailing stop, openclaw senpi, dsl_preset, strategy YAML configuration, runtime status, runtime health, system state, scanner health, external_scanner producer, push_signal, signal POST, scanner_lock, tick_cache, producer_daemon, senpi_runtime_helpers, or senpi-helpers CLI."
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Trading Runtime — OpenClaw Plugin

On-chain position tracker with automated DSL (Dynamic Stop-Loss) exit engine. Monitors a wallet's positions on Hyperliquid for lifecycle events (open, close, edit, flip) and applies two-phase trailing stop-loss protection to all positions.

## Core Concepts

**Python Producer SDK ships with this skill** at `senpi_runtime_helpers/`. When wiring up an `external_scanner` (a Python producer that pushes signals into this runtime), build it on this SDK — it wraps the `/signals` endpoint, the `SignalItem` schema, the per-tick lock, and the long-running daemon scheduler in one stdlib-only Python package. Recipes and rules: see [Python Producer SDK](#python-producer-sdk) below. Reference producer: [`pangolin/scripts/pangolin-producer.py`](../pangolin/scripts/pangolin-producer.py).

`SignalItem` shape (per `runtime-api/routes/signals.schema.ts`): top-level `address`, `scanner`, `asset`, `direction`, `score` (0..1), `signal_type`. The per-scanner `data` block is validated against the `config.fields` declared on the `external_scanner`. **Keep `asset` and `direction` out of `data`** — they're top-level routing fields. Putting them in `data` makes the runtime store two copies (`signal.asset` vs `signal.meta.asset`) and downstream consumers read inconsistently; that's the failure mode that triggered `INVALID_REQUEST` rejections in the Pangolin tick-2 incident on 2026-05-05.

**Flow:** Position Tracker scanner → detects position changes → DSL exit engine manages trailing stops

1. **Position Tracker** (`position_tracker` scanner) polls the wallet on-chain, detecting opens, closes, increases, decreases, and flips.
2. **DSL exit engine** monitors tracked positions on a timer, computing trailing stop-loss floors across two phases.
3. **Strategy** = a wallet address. The wallet address is the strategy identifier everywhere.

**Key insight:** The position tracker enables DSL protection for ALL positions of a strategy address — including those opened manually on the exchange or by other tools.

**Wallet uniqueness:** Only one running runtime per wallet address is allowed. Installing a second runtime for the same wallet is rejected. Delete the existing runtime first, then install the new one.

## Deploy Checklist

Follow these steps in order for any strategy. Step 5 only applies when the YAML declares an `external_scanner`.

### 1. Pre-flight environment probe

Before proposing any command, probe your own environment so you know whether to execute directly or hand commands to the user:

```bash
openclaw --version                      # confirms the CLI is reachable
openclaw senpi status                   # confirms the senpi plugin is running on this host
printenv | grep -E 'RAILWAY|OPENCLAW'   # detects a Railway / OpenClaw-hosted environment
```

If those succeed, **you can exec here** — run the deployment commands yourself instead of handing the user copy/paste blocks. Only fall back to "please run this on your host" if the probe fails.

**Where secrets live on an owned host.** On the user's own OpenClaw host (self-hosted, Railway, etc.) the Senpi API key is already on disk — reading it from the expected locations is expected behavior, not a breach. Common locations:

- `$SENPI_AUTH_TOKEN` environment variable
- `openclaw.json` at `plugins.entries.runtime.config.apiKey`

Pass the key through env vars on the producer daemon's launch command (e.g. `SENPI_AUTH_TOKEN=… nohup python3 -u <producer>.py …`). Do not echo it into chat or log files.

### 2. Validate the strategy wallet

Call `strategy_list` via MCP (see the **Strategy Wallet Validation** section below). If the wallet is not in the list, confirm with the user before calling `strategy_create_custom_strategy`.

### 3. Write the YAML to disk

**Never handwrite or re-stream a full strategy YAML into chat.** Chat transports (Telegram, etc) frequently corrupt indentation and code fences in long YAML blocks — don't spend turns re-sending it. Instead, read the matching reference file in this skill and write it to disk in one command:

```bash
cat > <name>.yaml <<'YAML'
<contents from the matching reference file, with ${VAR} placeholders as-is>
YAML
```

Sources of truth by strategy type:

- Momentum-guarded quickstart → [`references/momentum-guarded-strategy.md`](references/momentum-guarded-strategy.md)
- Bare position-tracker (DSL only) → [`references/strategy-examples.md`](references/strategy-examples.md), or `openclaw senpi guide examples` for a minimal template

Only handwrite YAML when no shipped reference fits the user's ask — and still write it straight to disk via heredoc, never paste it into chat for the user to copy.

### 4. Create the runtime

```bash
openclaw senpi runtime create --path ./<name>.yaml
```

### 5. Wire producers (only if the YAML declares an `external_scanner`)

External scanners are push-driven — without a producer, the scanner stays silent. Build the producer using the Python Producer SDK (`senpi_runtime_helpers`) bundled with this skill and launch it as a long-running daemon via `producer_daemon`. See [Python Producer SDK](#python-producer-sdk) below for rules, the import shim, and a new-producer skeleton; reference producer at [`pangolin/scripts/pangolin-producer.py`](../pangolin/scripts/pangolin-producer.py). Strategy-specific env vars (e.g. momentum's `TIMEFRAME`, `MIN_MOVE_PCT`) are in the strategy's own reference.

After first launch, the daemon is managed with the bundled `senpi-helpers` operator CLI (`list` / `health` / `stats` / `stop` / `restart`).

### 6. Verify the strategy is live

Do not declare success on `runtime list: running` alone — that field is process status, not functional liveness. Run the full liveness verification (see [Liveness Verification](#liveness-verification) below and [`references/liveness-verification.md`](references/liveness-verification.md) for the field-level decision tree).

At minimum, confirm all three self-questions pass:

1. Runtime is `running` in `runtime list`.
2. Every scanner in the YAML has executed at least once and recently (`state.components.scanners.state.scanners[].lastRunFinishedAt`).
3. If the YAML declares an `external_scanner`: the producer daemon is running with a recent successful tick (`senpi-helpers health <name>` → `healthy`), and the scanner's `runCount > 0`.

Quick commands:

```bash
openclaw senpi runtime list                # runtime shows status: running
openclaw senpi state --json                # field-level liveness data — walk per the reference
openclaw senpi status                      # health summary (do not rely on this alone for external scanners)
openclaw senpi dsl positions               # positions under DSL tracking (once opened)
senpi-helpers list                         # producer daemons running on this host
senpi-helpers health <daemon-name>         # one daemon's health; non-zero exit if degraded
openclaw senpi action history <action>     # LLM/rule decisions, if the strategy has actions
```

## Strategy Wallet Validation (MCP-first)

The runtime does **not** create wallets — it links to an existing strategy wallet that you create separately via Senpi MCP. Installing a runtime is a linking operation, never a wallet-creation operation.

### Wallet lifecycle (required order)

1. Create the strategy wallet via Senpi MCP (or confirm it already exists).
2. Put that wallet address into the runtime YAML (`strategy.wallet` / `${WALLET_ADDRESS}`).
3. Install/create the runtime — this links the runtime to the existing wallet for monitoring and exits.

Never treat `openclaw senpi runtime create` as wallet creation.

### Validation guardrail (mandatory before runtime create/install)

**Validation is agent-side, not user-side.** Call `strategy_list` via MCP *yourself* — do not ask the user to check the Senpi UI or run the call on their host. If the wallet appears in `strategies[].strategyWalletAddress` (case-insensitive), validation passes and you proceed directly to runtime install. If it does not appear, confirm with the user before creating a new one.

MCP-first flow:

1. Call `strategy_list` and collect `strategies[].strategyWalletAddress`.
2. If the provided wallet matches an entry (case-insensitive), validation passes — proceed to runtime install.
3. If the list is empty OR the wallet is not in the list: confirm with the user (including the `initialBudget` in USDC), then call `strategy_create_custom_strategy`.
4. Use the returned strategy wallet address as `WALLET_ADDRESS` in the YAML, then install the runtime.

**Hard rules:**
- Never use an embedded wallet / injected wallet for runtime linking.
- Never treat a wallet as a strategy wallet unless it appears in `strategy_list` — a user saying "it's my strategy wallet" is not a substitute; still call `strategy_list` to confirm.
- Runtime install is blocked until strategy wallet validation passes.
- Always confirm with the user before creating a new strategy wallet, and explicitly confirm the budget (`initialBudget`) that will be used.

**Example MCP flow:**

```text
strategy_list({})
if provided_wallet not in strategies[].strategyWalletAddress:
  # confirm with user first: create new strategy wallet + initialBudget
  strategy_create_custom_strategy({
    initialBudget: <budget_usdc>,
    positions: [],
    skillName: <runtime_name>,
    skillVersion: "1.0.0"
  })
```

Notes:
- `initialBudget` is required when creating the strategy wallet.
- Use `positions: []` when you only need the strategy wallet created now and will trade later.

## CLI Commands

All commands require the OpenClaw gateway running (`openclaw gateway run`). Print a full commands cheatsheet at any time with `openclaw senpi --cheatsheet`.

### Runtime management

```bash
# Create a runtime from YAML file
openclaw senpi runtime create --path ./my-strategy.yaml

# Create with inline YAML content
openclaw senpi runtime create --content "<yaml>"

# Create with custom ID
openclaw senpi runtime create --path ./my-strategy.yaml --runtime-id my-name

# List installed runtimes (id, wallet, source, status)
openclaw senpi runtime list

# Delete a runtime by id or wallet address
openclaw senpi runtime delete --id <runtime_id>
openclaw senpi runtime delete --address <wallet>
openclaw senpi runtime delete <runtime_id>   # positional also works
```

### DSL position inspection

```bash
# All active DSL-tracked positions
openclaw senpi dsl positions
openclaw senpi dsl positions --runtime <id>
openclaw senpi dsl positions --address <0x...>
openclaw senpi dsl positions --json

# Inspect one position (full DslState)
openclaw senpi dsl inspect <ASSET>
openclaw senpi dsl inspect SOL --runtime <id>
openclaw senpi dsl inspect BTC --json

# Archived (closed) positions
openclaw senpi dsl closes
openclaw senpi dsl closes --limit 20 --json
```

### Health and system state

Use these commands to check whether the runtime is operating correctly and to diagnose issues.

**`status`** — lightweight health check. Use as a first step when something seems wrong (e.g., positions not being tracked, stop-losses not firing, scanners not running). Shows overall health, scanner summary, and DSL summary per runtime.

```bash
openclaw senpi status                  # Health for all running runtimes
openclaw senpi status --runtime <id>   # Health for a specific runtime
openclaw senpi status --json           # Raw JSON output
```

**`state`** — full runtime snapshot. Use when `status` shows a problem and you need to dig deeper — it includes scanner registration details, DSL monitor telemetry (tick counts, errors, timing), active positions, and state directory location.

```bash
openclaw senpi state                   # Full state for all running runtimes
openclaw senpi state --runtime <id>    # Full state for a specific runtime
openclaw senpi state --json            # Raw JSON output
```

**When to use which:**
- Start with `status` — if everything shows healthy, the runtime is operating normally.
- Move to `state` when `status` reports degraded/unhealthy and you need to understand why (e.g., which scanner is erroring, whether the DSL monitor is stuck, tick error counts).
- Use `dsl positions` / `dsl inspect` for position-level detail (trailing stop floors, current tier, breach counts) — those are about individual positions, not runtime health.

### In-shell reference

```bash
openclaw senpi guide              # Overview and quick command list
openclaw senpi guide scanners     # Scanner types and config fields
openclaw senpi guide actions      # Action types and decision modes
openclaw senpi guide dsl          # DSL exit engine reference
openclaw senpi guide examples     # Print minimal strategy YAML
openclaw senpi guide schema       # Full YAML schema
openclaw senpi guide version      # Plugin version and changelog URL
```

### Diagnostic action commands

Use these when a trade didn't fire as expected, to audit decision-engine runs and execution history.

```bash
openclaw senpi action list                          # All registered actions with counters
openclaw senpi action inspect <action-name>         # Persisted latest state for one action
openclaw senpi action history [action-name]         # Rolling execution history
openclaw senpi action decisions [action-name]       # Rows where decision engine ran (LLM reasoning)
```

All accept `--runtime <id>`, `--address <wallet>`, `--limit <n>`, and `--json`.

### Configuration

```bash
openclaw senpi config set-chat-id <chatId>           # Telegram notifications
openclaw senpi config set-senpi-jwt-token <token>     # Senpi MCP auth
openclaw senpi config set-state-dir <dir>             # State directory
openclaw senpi config get <key>
openclaw senpi config list
openclaw senpi config unset <key>
openclaw senpi config reset
```

### Telegram notifications — two layers

Two independent mechanisms exist by design; they deliver different event classes, not duplicates.

| Layer | Set via | Scope | Events delivered |
|-------|---------|-------|------------------|
| Runtime-wide infra channel | `openclaw senpi config set-chat-id <id>` | Per host (all runtimes) | Plugin lifecycle, startup, infra / runtime-level errors |
| Per-strategy channel | `notifications.telegram_chat_id` in YAML (usually via `${TELEGRAM_CHAT_ID}`) | Per strategy | Position opens, closes, DSL exits, action decisions |

Typical setup: set both to the same chat id. They are not redundant — they cover different event classes, so setting only one leaves a class unnotified.

## Python Producer SDK

The runtime ships with `senpi_runtime_helpers`, a stdlib-only Python SDK for writing external-scanner producers. It is the canonical client for the runtime's `/signals` endpoint and replaces the legacy `mcporter` subprocess and `openclaw senpi external-scanner ingest` CLI patterns. **Do not use `openclaw cron add senpi-producer-…`** — that is the legacy fork-storm path the daemon was written to replace.

### Rules — read before writing a producer

**`asset` and `direction` are top-level, never inside `data`.** The wire schema is `additionalProperties: false`. Routing fields live at the top level; scanner-specific fields go in `data` (validated against the scanner's `config.fields`).

```python
# RIGHT
client.push_signal(
    address=wallet, scanner="my_signals",
    asset="BTC", direction="LONG",
    score=0.85,
    signal_type="MOMENTUM",
    data={"rsi": 75},
)

# WRONG — runtime rejects with INVALID_REQUEST
client.push_signal(
    address=wallet, scanner="my_signals",
    data={"asset": "BTC", "direction": "LONG"},
)
```

**Pass `signal_type=` explicitly on every emission.** The fallback is the scanner's `defaultSignalType` from `runtime.yaml`; most skills don't declare one, so omission lands empty type tags in audit logs and LLM decision context.

### Import shim

The package ships inside this skill. Producers add the skill directory to `sys.path`, then import normally:

```python
import os, sys
from pathlib import Path

_sdk_path = str(
    Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"))
    / "skills" / "senpi-trading-runtime"
)
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

from senpi_runtime_helpers import (
    SenpiClient, SenpiClientError,
    scanner_lock, tick_cache, parallel, producer_daemon,
)
```

`SenpiClient()` reads `SENPI_MCP_URL`, `SENPI_AUTH_TOKEN`, `SENPI_RUNTIME_API_HOST`, `SENPI_RUNTIME_API_PORT` from env.

### New producer skeleton

```python
# scripts/<skill>-producer.py
import os, sys
from pathlib import Path

_sdk_path = str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime")
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

from senpi_runtime_helpers import (
    SenpiClient, scanner_lock, tick_cache, producer_daemon,
)

WALLET = os.environ["<SKILL>_WALLET"]
SCANNER_NAME = "<scanner_name>"           # matches runtime.yaml
LOCK_NAME = f"<skill>-{WALLET[2:10]}"     # per-wallet — multi-wallet host safe

client = SenpiClient()
mcp = tick_cache(client)                  # per-tick TTL memoization

def run_one_tick():
    with scanner_lock(LOCK_NAME):
        ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)
        markets = mcp("leaderboard_get_markets", limit=100)
        # ... gating ...
        if signal_ready:
            client.push_signal(
                address=WALLET, scanner=SCANNER_NAME,
                asset="BTC", direction="LONG",
                score=0.85,
                signal_type="MOMENTUM",
                data={"funding_bps": 18},
            )

if __name__ == "__main__":
    producer_daemon(
        fn=run_one_tick,
        interval_seconds=300,
        name=LOCK_NAME,
        wallet=WALLET,                    # daemon self-terminates if the
        scanner=SCANNER_NAME,             # runtime is deleted OR scanner renamed
    )
```

The daemon stays alive across ticks; tick failures log and continue; SIGTERM / SIGINT drain gracefully. After any container restart relaunch manually: `nohup python3 -u <producer>.py …` with the skill's required env vars; the daemon records argv + cwd in `boot.json` and `senpi-helpers restart` handles subsequent restarts.

### Batch and parallel

Batch ingest — runtime is not atomic; successful items are ingested even on partial failure:

```python
client.push_signals([
    {"address": w, "scanner": "my_signals", "asset": "BTC", "direction": "LONG", "data": {...}},
    {"address": w, "scanner": "my_signals", "asset": "ETH", "direction": "LONG", "data": {...}},
])
```

Parallel MCP fan-out — concurrency-capped at `SENPI_HELPERS_MAX_CONCURRENT` (default 8); excess calls queue, never reject:

```python
results = parallel([
    lambda: mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET),
    lambda: mcp("leaderboard_get_markets", limit=100),
])
ch_ok, ch = results[0]
```

### Errors → fixes (most common)

| Error contains | Fix |
|---|---|
| `signal_post: … code=INVALID_REQUEST` | Most common: `asset`/`direction` inside `data`. Move to top-level kwargs; verify `data` keys against `runtime.yaml` `config.fields` |
| `signal_post: … code=NOT_FOUND` | Verify runtime install (`openclaw senpi runtime list`); confirm `scanner` matches `runtime.yaml` |
| `signal_post: unexpected envelope shape` | Runtime is on the legacy 1.x envelope; bump runtime to `>= 2.0.0` |
| `signal_post: HTTP 400 … Exceeded api.maxItemsPerSignalsRequest=10` | Batch too large; split batch (default cap 10) |
| `MCP error: …` from `mcp_call` | Check tool name + arguments against `senpi-hyperliquid-mcp` schema |
| `lock_recovered_after_crash` | Previous holder crashed; auto-recovered — no action |

Full per-item error codes and validation rules: [`references/signal-schema.md`](references/signal-schema.md).

### Operator CLI: `senpi-helpers`

Bundled with the SDK. Reads `pid.json` / `boot.json` / `heartbeat.json` under `$SENPI_HELPERS_STATE_DIR`; sends signals to control running daemons.

```bash
${OPENCLAW_WORKSPACE:-/data/workspace}/skills/senpi-trading-runtime/senpi-helpers <subcommand>
```

| Subcommand | Purpose |
|---|---|
| `list`    | All daemons on this host |
| `health`  | One daemon's health; non-zero exit on degraded |
| `stats`   | Hourly UTC bucket aggregation from the daemon's log (default 72h) |
| `stop`    | SIGTERM, poll, escalate to SIGKILL on timeout |
| `restart` | Stop + re-exec from `boot.json` |

`start` is not a subcommand — first launch goes through the `nohup python3 -u <producer>.py …` recipe above. Full subcommand reference, exit codes, JSON envelopes: [`references/senpi-helpers-cli.md`](references/senpi-helpers-cli.md).

### Logging

JSON lines to **stderr** prefixed `[senpi_helpers]`. Stdout stays clean. Filter Railway logs by `[senpi_helpers]`.

## Liveness Verification

`openclaw senpi runtime list: running` reports process status, not functional liveness. A runtime can be `running` while every component inside it is silent — scanners not ticking, DSL not evaluating positions, external scanners declared but no producer pushing data, actions wired but never invoked. **Never declare the runtime live based on `runtime list` alone.**

This is an **agent-side check**. Run the commands yourself (the pre-flight probe in Deploy Step 1 confirms you can exec here); do not ask the user to confirm "is it working?"

### Self-questions checklist

Before claiming the runtime is operating, the agent must be able to answer **yes** to all three — with field values as evidence, not assertions:

1. **Is the target runtime in `runtime list` with `status: running`?**
2. **For every scanner declared in the YAML, has it executed at least once and recently?** Walk `state.components.scanners.state.scanners[]` — for interval scanners check `lastRunFinishedAt` is within `2 × intervalSeconds`; for external scanners check `runCount > 0` and `lastRunFinishedAt` is within (producer cadence + buffer).
3. **For every external scanner in a running runtime, is the paired producer daemon registered on this host and healthy? And vice versa?** Reconcile `senpi-helpers list --json` (each entry's `wallet` + `scanner` fields) against running runtimes' external scanners from `state`. Report orphans on either side. Confirm health with `senpi-helpers health <daemon-name>` — exit code 0 means `healthy`; anything else is degraded.

DSL counters (`tickSuccessCount`, `lastTickFinishedAt`) are intentionally not in this checklist — they prove the runtime's internal tick loop moved, not that the protective path actually works (price providers, MCP, exchange connectivity are outside the runtime's visibility). Use `openclaw senpi dsl inspect <ASSET>` for per-position triage instead.

### What you can and can't trust from `senpi status`

- `status` is a useful first signal but **does not prove liveness for external scanners** — push-driven scanners are reported `healthy` even with `runCount === 0`, because the health layer cannot distinguish "waiting for first ingest" from "broken pipe."
- `status` aggregates across components; a hung DSL monitor can be masked by healthy scanners. Drill into the DSL component specifically.
- For anything more than a sanity check, walk `state --json` field-by-field per the decision tree.

### Decision tree and reconciliation

For the per-component field rules (interval scanner, external scanner, action) and the daemon ↔ runtime reconciliation algorithm, see [`references/liveness-verification.md`](references/liveness-verification.md). It is the authoritative source on which fields prove operation versus silence.

### Reporting back to the user

When liveness verification fails, surface the specific failing field and the remediation — never a generic "looks fine" or "still starting up." Examples:

- "Position tracker scanner is registered but `runCount === 0` and `lastRunFinishedAt` is null — the scheduler hasn't picked it up. `lastError`: `<message>`. Recommend recreating the runtime."
- "External scanner `external_momentum` has `runCount === 0`. No daemon for `(wallet=0x…, scanner=external_momentum)` appears in `senpi-helpers list` — the producer was never launched. Run Deploy Checklist Step 5."

## Runtime YAML

The runtime YAML drives all behavior. Top-level keys: `name`, `strategy`, `scanners`, `actions`, `exit`, `risk`, `notifications`.

```yaml
name: my-tracker
version: 1.0.0
description: >
  On-chain position tracker with DSL trailing stop-loss.

strategy:
  wallet: "${WALLET_ADDRESS}"
  slots: 2
  margin_pct: 15
  trading_risk: conservative    # conservative | moderate | aggressive
  enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

exit:
  engine: dsl
  interval_seconds: 30            # how often the price monitor runs (5-3600)
  order_type: FEE_OPTIMIZED_LIMIT # MARKET (default) or FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true       # fall back to market if maker doesn't fill
    execution_timeout_seconds: 15         # seconds to wait for maker fill (1-300, default 45)
  dsl_preset:                     # single preset (no named map needed)
    hard_timeout:
      enabled: true
      interval_in_minutes: 360
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 120
      min_value: 5
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 60
    phase1:
      enabled: true
      max_loss_pct: 4.0
      retrace_threshold: 7
      consecutive_breaches_required: 1
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 7,  lock_hw_pct: 40 }
        - { trigger_pct: 12, lock_hw_pct: 55 }
        - { trigger_pct: 15, lock_hw_pct: 75 }
        - { trigger_pct: 20, lock_hw_pct: 85 }

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

Environment variable substitution: `${VAR}` and `${VAR:-default}` resolved at load time.

The `risk:` block is optional — include it when the strategy needs guard rails (daily loss cap, max entries per day, drawdown halt, per-asset cooldown, etc.). See the momentum-guarded reference for a worked example and the YAML schema reference for the full field list.

For full field details: [YAML Schema Reference](references/yaml-schema.md)

## DSL Exit Engine — Key Concepts

**Two-phase trailing stop-loss** protecting open positions:

- **Phase 1** (entry → first tier): limits downside. `max_loss_pct` sets a hard loss cap; `retrace_threshold` trails the high-water mark.
- **Phase 2** (after first tier reached): locks in profits. Each tier's `lock_hw_pct` sets a floor as a % of the high-water ROE — the floor trails upward and never loosens.
- **Time-based cuts** (`hard_timeout`, `weak_peak_cut`, `dead_weight_cut`) — declared at the preset level (siblings of `phase1`/`phase2`) and evaluated **in both phases** as outer-bound protections, not Phase-1 patience knobs. `hard_timeout` fires whenever elapsed time exceeds its interval, regardless of phase or handoff status (a deliberate test-locked behavior). `weak_peak_cut` evaluates in both phases too, but its `peakROE < min_value` guard makes it *effectively* Phase 1 only when `min_value` is set below the first tier's `trigger_pct`.

**Tiers** are profit milestones. `trigger_pct` = ROE % that activates the tier, `lock_hw_pct` = % of high-water ROE to lock as floor. Tiers must have strictly increasing `trigger_pct`.

**Tuning guidance:**
- Higher `max_loss_pct` = more room before hard stop (conservative: 5-6%, aggressive: 2-3%)
- Higher `retrace_threshold` = more tolerance for pullbacks from peak
- Higher `consecutive_breaches_required` = more tolerant of temporary dips (1 = instant, 3 = sustained)
- Longer time-cut intervals = more patience before cutting underperformers

For full DSL mechanics (retrace math, breach logic, close reasons, events): [DSL Configuration Reference](references/dsl-configuration.md)

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `WALLET_ADDRESS` | Yes | Strategy wallet address (used in YAML via `${WALLET_ADDRESS}`). |
| `SENPI_API_KEY` | For live MCP | Senpi MCP authentication. |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID for notifications. |
| `DSL_STATE_DIR` | No | Override DSL state file directory. |
| `OPENCLAW_WORKSPACE` | No | Workspace root for skills (default `/data/workspace`). Used by the Python Producer SDK import shim. |
| `SENPI_AUTH_TOKEN` | For Python producers | MCP bearer token used by `SenpiClient`. |
| `SENPI_MCP_URL` | No | MCP endpoint (default `https://mcp.prod.senpi.ai/mcp`). |
| `SENPI_RUNTIME_API_HOST` | No | Runtime signals host (default `127.0.0.1`). |
| `SENPI_RUNTIME_API_PORT` | No | Runtime signals port (default `8787`). |
| `SENPI_HELPERS_STATE_DIR` | No | Daemon state files (default `/data/.openclaw/senpi-helpers`). |
| `SENPI_HELPERS_MCP_TIMEOUT` | No | Per-call MCP timeout in seconds (default `30.0`). |
| `SENPI_HELPERS_SIGNAL_TIMEOUT` | No | Per-call signal POST timeout in seconds (default `5.0`). |
| `SENPI_HELPERS_MAX_CONCURRENT` | No | `parallel(...)` concurrency cap (default `8`). |
| `SENPI_HELPERS_TICK_CACHE_TTL` | No | Tick cache TTL in seconds (default `120`). |

## References

- [Momentum-Guarded Strategy](references/momentum-guarded-strategy.md) — End-to-end quick-start strategy exercising external scanners, LLM decisions, risk gates, and DSL exits
- [Runtime Concepts](references/runtime-concepts.md) — Conceptual explanation of scanners, actions, DSL phases, and what every field controls in trading terms
- [Strategy YAML Reference](references/yaml-schema.md) — Schema reference: top-level sections, building blocks, template variables, wiring rules, validation errors
- [DSL Configuration Reference](references/dsl-configuration.md) — DSL exit engine: phases, tiers, time cuts, tuning guidance, close reasons, events
- [Strategy Examples](references/strategy-examples.md) — Position-tracker runtimes with different DSL tuning profiles
- [External Producers](references/external-producers.md) — How to schedule, deploy, and build external-scanner producers
- [Liveness Verification](references/liveness-verification.md) — Field-level decision tree and daemon ↔ runtime reconciliation for confirming the runtime is actually operating, not just registered
- [Signal Schema](references/signal-schema.md) — `SignalItem` wire format consumed by `POST /signals`: routing fields vs. `data`, validation, per-item error codes, batch semantics
- [`senpi-helpers` CLI](references/senpi-helpers-cli.md) — Operator CLI for producer daemons: list / health / stats / stop / restart with exit codes and JSON envelopes
