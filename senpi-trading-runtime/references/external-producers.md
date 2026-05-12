# External Scanner Producers

External scanners are push-driven — the runtime does not poll them. A **producer** is an out-of-process script that computes signals or context and pushes them into a running runtime via `POST /signals`.

Producers are authored against the Python Producer SDK (`senpi_runtime_helpers`) bundled with this skill. See [Python Producer SDK](../SKILL.md#python-producer-sdk) in the main SKILL.md for the import shim, rules, new-producer skeleton, batch + parallel recipes, error table, and operator CLI. This file is the operations-level reference: how producers work end-to-end, the env vars they share, and how to launch / persist them.

---

## How producers work

1. The producer process loads its own data (via MCP, REST, SDK, etc.).
2. It computes signals or context.
3. It calls `client.push_signal(...)` (or `client.push_signals([...])` for batches) — direct HTTP POST to the runtime API on `127.0.0.1:8787`.
4. The runtime's `external_scanner` routes the payload to `SignalStore` / `SharedArtifactStore` and emits `scanner:run:complete`.
5. Actions subscribed to that scanner are triggered identically to built-in scanner runs.

The producer is wrapped in `producer_daemon(...)` — a long-running scheduler that calls `run_one_tick` on an interval, handles SIGTERM gracefully, writes self-describing state files (`pid.json` / `boot.json` / `heartbeat.json`), and optionally self-terminates when the runtime or scanner is gone.

---

## Producer file layout

Producers follow a two-file convention per strategy skill:

```
<skill-name>/scripts/<skill-name>-producer.py   # entry point: imports SDK, runs producer_daemon
<skill-name>/scripts/<skill-name>_config.py     # SDK import shim + MCP helpers + per-skill state I/O
```

After the strategy skill is installed via `npx skills add … --skill <skill-name>`, the producer scripts live at `${OPENCLAW_WORKSPACE}/skills/<skill-name>/scripts/`.

The Python Producer SDK itself ships inside the `senpi-trading-runtime` skill at `~/.openclaw/skills/senpi-trading-runtime/senpi_runtime_helpers/`. The shim in `<skill-name>_config.py` probes that location (and falls back to `${OPENCLAW_WORKSPACE}/skills/senpi-trading-runtime/`); the producer never has to know which install path the host uses.

---

## Common environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `<SKILL>_WALLET` | yes | Strategy wallet address for this producer (e.g. `PANGOLIN_WALLET`) |
| `SENPI_AUTH_TOKEN` | yes | Senpi MCP bearer token used by `SenpiClient` |
| `OPENCLAW_WORKSPACE` | no | Workspace root for skills (default `/data/workspace`); used by the SDK import shim's fallback path |
| `SENPI_MCP_URL` | no | MCP endpoint (default `https://mcp.prod.senpi.ai/mcp`) |
| `SENPI_RUNTIME_API_HOST` | no | Runtime signals host (default `127.0.0.1`) |
| `SENPI_RUNTIME_API_PORT` | no | Runtime signals port (default `8787`) |

The full SDK-tuning env table (timeouts, concurrency caps, tick cache, daemon state dir) is in [SKILL.md → Environment Variables](../SKILL.md#environment-variables). Strategy-specific tuning vars live in the strategy skill's own SKILL.md / README.

---

## Launching a producer

First launch on a host is manual; the daemon records argv + cwd into `boot.json` so subsequent restarts are handled by `senpi-helpers restart`.

```bash
SENPI_AUTH_TOKEN=<token> <SKILL>_WALLET=0x... \
  nohup python3 -u ${OPENCLAW_WORKSPACE}/skills/<skill-name>/scripts/<skill-name>-producer.py \
  > /tmp/<skill-name>-producer.log 2>&1 &
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

The daemon name is the per-skill `LOCK_NAME` used in the producer (typically `<skill>-<wallet-suffix>` — the first 8 hex characters after `0x` of the strategy wallet, e.g. `<skill>-a919c1e2`).

---

## Custom producers

To write a new producer, start from the [New producer skeleton](../SKILL.md#new-producer-skeleton) in SKILL.md.

For the wire format consumed by `POST /signals` (used internally by `client.push_signal(...)`), see [Signal Schema](signal-schema.md) — the routing fields (`address`, `scanner`, `asset`, `direction`, `score`, `signal_type`) versus the validated `data` block, response envelope, per-item error codes.

Non-Python producers (curl, Node.js, Go) implement the wire format from [Signal Schema](signal-schema.md) directly. The Python SDK is the canonical client, but the HTTP contract is stable.
