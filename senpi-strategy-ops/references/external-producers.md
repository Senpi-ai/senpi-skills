# Scanners (external producers) — operations reference

A strategy's **scanner** (`scanner.py`) is an external, push-driven producer: the runtime does not
poll it; the scanner computes signals and pushes them into a running runtime via `POST /signals`. This
file is the **ops-level** reference (how a scanner runs, its env, launch/restart). For the full deploy
sequence see [deploy-and-teardown.md](deploy-and-teardown.md); to *author* a scanner see the
**senpi-strategy-author** skill.

---

## How a scanner works

1. The scanner process loads its data (via MCP, REST, the SDK).
2. It computes signals.
3. It calls `client.push_signal(address=<wallet>, scanner=<name>, …)` — direct HTTP POST to the
   runtime API on `127.0.0.1:8787`.
4. The runtime's `external_scanner` (matching `scanner.name`) routes the payload and emits
   `scanner:run:complete`.
5. Actions subscribed to that scanner fire identically to built-in scanner runs.

The scanner is wrapped in `producer_daemon(...)` — a long-running scheduler that ticks on an interval,
handles SIGTERM gracefully, writes self-describing state files (`pid.json` / `boot.json` /
`heartbeat.json`), and self-terminates if its runtime or scanner is gone.

---

## Package layout

A scanner lives inside its **strategy package** (not a skill):

```
<id>/scripts/scanner.py      # entry point: imports the SDK, runs producer_daemon
<id>/scripts/<id>_config.py  # SDK import shim + MCP helpers + load_params + per-package state I/O
<id>/strategy.yaml           # declares the scanner name, wallet_env, instance env, tick, params
```

The package is deployed by **`install_strategy`** (see SKILL.md); the scanner reads its tunables from
`strategy.yaml` via `senpi_runtime_helpers.load_params()` and its wallet from the env the installer
injects. The Producer SDK itself ships in the **senpi-trading-runtime** infra bundle at
`~/.openclaw/skills/senpi-trading-runtime/senpi_runtime_helpers/` (with a fallback to
`${OPENCLAW_WORKSPACE}/skills/senpi-trading-runtime/`); the shim in `<id>_config.py` probes both, so
the scanner never hardcodes the install path.

---

## Environment variables

The installer injects these per instance (names declared in `strategy.yaml.defaults` +
`instances[].wallet_env` + `instances[].env`):

| Variable | Required | Purpose |
|----------|----------|---------|
| `<wallet_env>` | yes | this instance's strategy wallet address (e.g. `WALLET_ADDRESS`, `SPIDER_SWING_WALLET`) — the signal routing key |
| `SENPI_AUTH_TOKEN` | yes | Senpi MCP bearer token used by `SenpiClient` |
| `<decision_model_env>` | yes | the runtime LLM gate model — **bare model name, no provider prefix** |
| instance `env` (e.g. `SPIDER_LEG`) | per strategy | selects the instance for a multi-instance scanner |
| `OPENCLAW_WORKSPACE` | no | workspace root (default `/data/workspace`); SDK import-shim fallback |
| `SENPI_MCP_URL` | no | MCP endpoint (default `https://mcp.prod.senpi.ai/mcp`) |
| `SENPI_RUNTIME_API_HOST` / `_PORT` | no | runtime signals endpoint (default `127.0.0.1:8787`) |

The SDK-tuning vars (timeouts, concurrency, tick-cache, state dir) and the signal wire format
(`address`/`scanner`/`asset`/`direction`/`score`/`signal_type` vs the validated `data` block) are
documented in the **senpi-strategy-author** skill (`python-producer-sdk.md`, `signal-schema.md`).

---

## Launching a scanner

First launch is rendered by [deploy-and-teardown.md](deploy-and-teardown.md) /
`scripts/deploy_strategy.py`. The daemon records argv + cwd into `boot.json`, so restarts go through
`senpi-helpers`:

```bash
<wallet_env>=0x...  SENPI_AUTH_TOKEN=<token>  <decision_model_env>=<bare-model>  <instance.env...> \
  nohup python3 -u <id>/scripts/scanner.py > /tmp/<id>-<instance>-scanner.log 2>&1 & disown
```

Confirm it registered, then manage it via the daemon CLI:

```bash
senpi-helpers list                 # all daemons on this host
senpi-helpers health <daemon>      # health summary; non-zero exit if degraded
senpi-helpers restart <daemon>     # relaunch from recorded boot.json (after a container restart)
```

The daemon name is the package's lock name (typically `<id>-<wallet-suffix>` — the first hex chars of
the strategy wallet, e.g. `polar-a919c1e2`). See [senpi-helpers-cli.md](senpi-helpers-cli.md) for the
full daemon-CLI reference and [liveness-verification.md](liveness-verification.md) to confirm the
scanner is actually ticking (not just "running").
