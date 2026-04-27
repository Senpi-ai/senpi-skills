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
  --name "senpi-producer-<scanner-name>-<wallet-suffix>" \
  --cron "<cron-expression>" \
  --session isolated \
  --wake now \
  --message "Run \`<ENV_VARS> node <PATH-TO-PRODUCER>/producer.mjs >> <PATH-TO-LOG>/senpi-producer-<scanner-name>-<wallet-suffix>.log 2>&1\` and report success/failure in this log." \
  --no-deliver
```

| Flag | Purpose |
|------|---------|
| `--name` | Canonical producer name — see naming convention below. Used by reconciliation to join cron entries against running runtimes. |
| `--cron` | Standard cron expression (e.g. `*/5 * * * *` for every 5 minutes) |
| `--session isolated` | Run in an isolated session (no conversation state leaks) |
| `--wake now` | Schedule starts immediately |
| `--no-deliver` | Run headless (no agent delivery) |
| `--message` | Shell command to execute, wrapped in backticks; redirect stdout+stderr to a log file you can tail |

### Required cron job naming convention

The `--name` MUST follow this exact shape:

```
senpi-producer-<scanner-name>-<wallet-suffix>
```

- `<scanner-name>` is the `name:` of the external scanner block in the strategy YAML (e.g. `external_momentum`).
- `<wallet-suffix>` is the **last 4 hex characters** of the strategy wallet, lowercased (no `0x` prefix on the suffix). For wallet `0xAbC123dEf4567890aBc123def4567890ABc12345` the suffix is `2345`.

Example:

```
senpi-producer-external_momentum-2345
```

This naming is not cosmetic — it is the join key for cron ↔ runtime reconciliation. The agent uses the cron name to detect orphan producers (a cron exists with no matching running runtime) and orphan consumers (a runtime declares an external scanner but no producer cron is firing for it). The freeform `--message` field cannot be parsed reliably, so the name carries the contract.

**Collision caveat.** 4 hex characters give ~65k uniqueness, so suffix collisions between two strategy wallets on the same host are unlikely below ~30 wallets but possible. Reconciliation reports any suffix that matches more than one running runtime as **ambiguous** rather than auto-resolving — see [liveness-verification.md](liveness-verification.md#reconciliation-algorithm). If the host outgrows this scheme, lengthen the suffix.

If you must use a different scheduler (systemd timer, launchd, Kubernetes CronJob, etc.) the only contract is "exec the producer command on an interval." Reconciliation against `openclaw cron list` will not see those jobs, so document them out-of-band and verify liveness from the data side (see [liveness-verification.md](liveness-verification.md)) — `runCount > 0` and a recent `lastRunFinishedAt` on the external scanner is the canonical proof of life regardless of where the schedule lives.

---

## Building a custom producer

A producer must:

1. **Gather or compute the data** you want ingested.
2. **Format a payload** that matches the `external_scanner`'s `config.fields` declared in the strategy YAML.
3. **Shell out** to `openclaw senpi external-scanner ingest --address <wallet> --scanner <name> --payload '<json>'`.
4. **Validate the CLI response** against the rules below — the CLI always returns JSON on stdout, so the same checks work in any language.

### Batch item shape (wire format)

The runtime accepts either a single signal payload or `{ "signals": [ ... ] }` for batches. Each batch item has this shape:

```json
{
  "asset": "ETH",
  "direction": "LONG",
  "score": 0.85,
  "signal_type": "momentum_breakout",
  "data": {
    "sourceScannerId": "...",
    "sourceSignalType": "...",
    "sourceTimestamp": 1714200000,
    "sourceFactors": { "...": "..." },
    "sourceMeta": { "...": "..." }
  }
}
```

- `asset`, `direction`, `score`, `signal_type` are top-level routing fields the runtime uses directly.
- Everything else the producer wants to preserve goes under `data` — by convention with the `source*` prefix so downstream consumers can reconstruct producer-side context. The fields you put under `data` must satisfy the scanner's `config.fields` schema in the strategy YAML.

A producer that keeps a richer in-process signal object (`scannerId` / `factors` / `meta` / `timestamp`) should normalize to the shape above before shipping — preserve the rich fields under `data.source*` rather than dropping them.

### CLI response contract

On success:

```json
{ "ok": true, "result": { "accepted": true, "signalCount": <N>, "...": "..." } }
```

On failure:

```json
{ "ok": false, "error": { "code": "...", "message": "..." } }
```

A producer must treat the ingest as failed (and log + exit non-zero so the scheduler can retry / alert) when **any** of these are true:

- The stdout is not valid JSON.
- `response.ok !== true`.
- `response.result.accepted !== true`.
- `response.result.signalCount !== <number of signals you sent>` — a count mismatch means part of your batch was dropped silently.

These four checks are the load-bearing contract — silently treating a partial-acceptance response as success is the most common producer bug.

### Node producers

Node producers shipped with this plugin share an internal ingest helper (CLI shell-out + normalization + response validation in one call) so they never drift on the contract above. If you are writing a Node producer inside this repo, follow the existing producers' pattern. If you are writing one outside the repo, implement the four response-validation checks yourself against the JSON shape documented above — the contract, not the helper, is what's stable.

### Non-Node producers

Match the CLI payload shape directly and parse the JSON stdout. The four response checks above apply identically.
