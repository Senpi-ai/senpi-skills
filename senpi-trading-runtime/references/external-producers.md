# External Scanner Producers

External scanners are push-driven — the runtime does not poll them. A **producer** is an out-of-process script that computes signals or context and pushes them into a running runtime via `POST /signals`.

Producers are authored against the Python Producer SDK (`senpi_runtime_helpers`) bundled with this skill. See [Python Producer SDK](../SKILL.md#python-producer-sdk) in the main SKILL.md for the import shim, rules, new-producer skeleton, batch + parallel recipes, error table, and operator CLI. This file is the operations-level reference: where shipped producers live, what env vars they share, and how to launch / persist them.

---

## How producers work

1. The producer process loads its own data (via MCP, REST, SDK, etc.).
2. It computes signals or context.
3. It calls `client.push_signal(...)` (or `client.push_signals([...])` for batches) — direct HTTP POST to the runtime API on `127.0.0.1:8787`.
4. The runtime's `external_scanner` routes the payload to `SignalStore` / `SharedArtifactStore` and emits `scanner:run:complete`.
5. Actions subscribed to that scanner are triggered identically to built-in scanner runs.

The producer is wrapped in `producer_daemon(...)` — a long-running scheduler that calls `run_one_tick` on an interval, handles SIGTERM gracefully, writes self-describing state files (`pid.json` / `boot.json` / `heartbeat.json`), and optionally self-terminates when the runtime or scanner is gone.

---

## Where shipped producers live

Built-in producers ship with the senpi-skills repo, one per strategy directory:

```
<skill-name>/scripts/<skill-name>-producer.py
<skill-name>/scripts/<skill-name>_config.py
```

For example: `pangolin/scripts/pangolin-producer.py` is the canonical reference producer.

After installation via `npx skills add … --skill <skill-name>`, they land at:

```
${OPENCLAW_WORKSPACE:-/data/workspace}/skills/<skill-name>/scripts/
```

The Python Producer SDK ships inside the `senpi-trading-runtime` skill. On global-install hosts it lives at `~/.openclaw/skills/senpi-trading-runtime/senpi_runtime_helpers/` (e.g. `/data/.openclaw/skills/senpi-trading-runtime/senpi_runtime_helpers/` on Railway); some setups put user skills under `${OPENCLAW_WORKSPACE}/skills/`. Producers' import shim probes both — see the [import shim](../SKILL.md#import-shim) in SKILL.md.

---

## Common environment variables

Producer scripts share these env vars; strategy-specific vars (`PANGOLIN_WALLET`, `WOLVERINE_DECISION_MODEL`, etc.) are documented in the per-skill SKILL.md / README.

| Variable | Required | Purpose |
|----------|----------|---------|
| `<SKILL>_WALLET` | yes | Strategy wallet address for this producer (e.g. `PANGOLIN_WALLET`) |
| `SENPI_AUTH_TOKEN` | yes | Senpi MCP bearer token used by `SenpiClient` |
| `OPENCLAW_WORKSPACE` | no | Workspace root for skills (default `/data/workspace`); used by the SDK import shim |
| `SENPI_MCP_URL` | no | MCP endpoint (default `https://mcp.prod.senpi.ai/mcp`) |
| `SENPI_RUNTIME_API_HOST` | no | Runtime signals host (default `127.0.0.1`) |
| `SENPI_RUNTIME_API_PORT` | no | Runtime signals port (default `8787`) |
| `SENPI_HELPERS_STATE_DIR` | no | Daemon state files (default `/data/.openclaw/senpi-helpers`) |

The full SDK-tuning env table (timeouts, concurrency caps, tick cache) is in [SKILL.md → Environment Variables](../SKILL.md#environment-variables).

---

## Launching a producer

First launch on a host is manual; the daemon records argv + cwd into `boot.json` so subsequent restarts are handled by `senpi-helpers restart`.

```bash
nohup python3 -u ${OPENCLAW_WORKSPACE}/skills/<skill>/scripts/<skill>-producer.py \
  > /tmp/<skill>-producer.log 2>&1 &
```

Confirm the daemon registered itself:

```bash
senpi-helpers list                 # all daemons on this host
senpi-helpers health <daemon-name> # health summary; non-zero exit if degraded
```

After a container restart, relaunch from the recorded boot.json:

```bash
senpi-helpers restart <daemon-name>
```

The daemon name is the per-skill `LOCK_NAME` used in the producer (typically `<skill>-<wallet-suffix>`, e.g. `pangolin-a919c1e2`).

---

## Custom producers

If you are writing a new producer (skill in this repo or external project), start from the [New producer skeleton](../SKILL.md#new-producer-skeleton) in SKILL.md and use [`pangolin/scripts/pangolin-producer.py`](../../pangolin/scripts/pangolin-producer.py) as a working reference.

For the wire format consumed by `POST /signals` (used internally by `client.push_signal(...)`), see [Signal Schema](signal-schema.md) — the routing fields (`address`, `scanner`, `asset`, `direction`, `score`, `signal_type`) versus the validated `data` block, response envelope, per-item error codes.

Non-Python producers (curl, Node.js, Go) implement the wire format from [Signal Schema](signal-schema.md) directly. The Python SDK is the canonical client, but the HTTP contract is stable.
