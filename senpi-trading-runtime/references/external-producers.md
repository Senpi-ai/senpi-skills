# External Scanner Producers

External scanners are push-driven — the runtime does not poll them. A **producer** is an out-of-process script that computes signals or context and pushes them into a running runtime via the `openclaw senpi external-scanner ingest` CLI command.

This file documents the generic producer operations pattern: how producers work, where shipped producers live, which environment variables they share, and how to schedule them with `openclaw cron`. For a complete end-to-end strategy that uses the shipped momentum producer, see [momentum-guarded-strategy.md](momentum-guarded-strategy.md).

---

## How producers work

1. The producer process loads its own data (via MCP, REST, SDK, etc.).
2. It computes signals or context.
3. It calls `openclaw senpi external-scanner ingest` to push a JSON payload into the runtime.
4. The runtime's `external_scanner` routes the payload to `SignalStore` / `SharedArtifactStore` and emits `scanner:run:complete`.
5. Actions subscribed to that scanner are triggered identically to built-in scanner runs.

### Ingest CLI

```bash
# Single signal
openclaw senpi external-scanner ingest \
  --address 0xYourWallet \
  --scanner external_momentum \
  --payload '{"asset":"ETH","direction":"LONG","score":0.85,"data":{...}}'

# Batch of signals
openclaw senpi external-scanner ingest \
  --address 0xYourWallet \
  --scanner external_momentum \
  --payload '{"signals":[{"asset":"ETH",...},{"asset":"SOL",...}]}'

# Context-only (no signal)
openclaw senpi external-scanner ingest \
  --address 0xYourWallet \
  --scanner custom_regime \
  --payload '{"data":{"regime":"RISK_ON","confidence":0.91}}'
```

Use `--payload-path <file>` to read a JSON payload from a file instead of passing it inline. The CLI emits pure JSON on stdout (`{"ok":true,...}` on success, `{"ok":false,"error":{...}}` on failure), which makes it safe to parse from any scripting language.

---

## Where producer scripts live

Built-in producers ship with the plugin:

| Environment | Producer path prefix |
|-------------|----------------------|
| Local development (after `npm run build`) | `<project-root>/dist/scanners/external/` |
| Railway OpenClaw host template | `/data/.openclaw/extensions/runtime/dist/scanners/external/` |

Each built-in producer shipped with the senpi plugin lives in its own subdirectory, e.g.:
- `dist/scanners/external/momentum/producer.mjs`

Stability note: from the built-in external scanner shipped with the senpi plugin, only momentum producer is production ready.

Custom producers can live anywhere — the only requirement is that the scheduler can execute them and the `openclaw` CLI is reachable on `PATH`.

---

## Common environment variables

Most MCP-backed producers (including the shipped ones) use:

| Variable | Required | Description |
|----------|----------|-------------|
| `SENPI_API_KEY` | yes | Senpi API key for MCP access |
| `SENPI_MCP_URL` | no | MCP server URL (default: `https://mcp.prod.senpi.ai/mcp`) |
| `STRATEGY_ADDRESS` | yes | Strategy wallet address of the running runtime |
| `EXTERNAL_SCANNER_NAME` | no | Overrides the configured `external_scanner` name the producer targets |
| `OPENCLAW_BIN` | no | Path to the `openclaw` CLI (default: `openclaw` on `PATH`) |

Individual producers may add their own scanner-specific env vars on top of these — see the producer source or its accompanying docs.

---

## Scheduling with `openclaw cron`

The OpenClaw parent CLI ships a cron scheduler that works well for producers. The template:

```bash
openclaw cron add \
  --name "<producer-name>" \
  --cron "<cron-expression>" \
  --session isolated \
  --wake now \
  --message "Run \`<ENV_VARS> node <PATH-TO-PRODUCER>/producer.mjs >> <PATH-TO-LOG>/<producer-name>.log 2>&1\` and report success/failure in this log." \
  --no-deliver
```

| Flag | Purpose |
|------|---------|
| `--name` | Unique name for the cron job (used to list/delete later) |
| `--cron` | Standard cron expression (e.g. `*/5 * * * *` for every 5 minutes) |
| `--session isolated` | Run in an isolated session (no conversation state leaks) |
| `--wake now` | Schedule starts immediately |
| `--no-deliver` | Run headless (no agent delivery) |
| `--message` | Shell command to execute, wrapped in backticks; redirect stdout+stderr to a log file you can tail |

The `openclaw cron` scheduler is just one option — any scheduler (custom scheduler, cron, systemd timer, launchd, Kubernetes CronJob, etc.) can run producers. The only contract is "exec a command on an interval."

---

## Building a custom producer

A producer must:

1. **Gather or compute the data** you want ingested.
2. **Format a payload** that matches the `external_scanner`'s `config.fields` declared in the strategy YAML.
3. **Shell out** to `openclaw senpi external-scanner ingest --address <wallet> --scanner <name> --payload '<json>'`.

For Node-based producers, reuse the helper at `src/scanners/external/shared/ingest.mjs`. It handles CLI shell-out, payload normalization, and response validation so producers never diverge on the wire format.

For producers in other languages, match the CLI payload shape directly. The CLI always returns JSON on stdout, which makes error handling uniform across languages.
