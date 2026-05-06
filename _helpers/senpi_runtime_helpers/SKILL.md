---
name: senpi-runtime-helpers
description: >-
  Stdlib-only Python helper package (`senpi_runtime_helpers`) for senpi-skills
  producers running under runtime-2. Use this skill when authoring or
  migrating any producer / scanner skill that calls Senpi MCP tools or emits
  signals to the runtime — it replaces the legacy `mcporter` subprocess and
  `openclaw senpi external-scanner ingest` CLI patterns with a persistent
  HTTPS keep-alive client, a daemon scheduler, scanner_lock with stale-PID
  recovery, bounded `parallel(...)` fan-out, and a per-tick TTL cache. Mandatory
  for new producers; required for any skill that needs sub-second MCP latency,
  predictable memory, or freedom from the cron-+-LLM agentTurn coupling that
  causes fork-storms on shared infra. Triggers on mentions of mcporter,
  mcporter_call, external-scanner ingest, openclaw cron + agent main,
  producer skill authoring, signal emission, fork-storm, mcp-remote spawn,
  per-call CLI cold start, runtime-2 producer migration.
license: MIT
compatibility: >-
  Python 3.10+. Stdlib only (no third-party deps). Requires senpi-trading-runtime
  v1.0.95-dev.runtime-phase-2-api.* or newer for the `/signals` endpoint.
  Intended to be loaded from `${OPENCLAW_WORKSPACE:-/data/workspace}/skills/_helpers/`.
metadata:
  author: senpi
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi_runtime_helpers — the producer wrapper

Use this skill whenever you are writing or modifying a Senpi producer (any script that calls MCP tools and emits signals). The wrapper gives you, in one persistent client:

| For | Use |
|---|---|
| MCP tool calls | `client.mcp_call(tool, **kwargs)` |
| Signal emission | `client.push_signal(...)` / `client.push_signals([...])` |
| Per-tick lock | `with scanner_lock(name): ...` |
| Bounded fan-out | `parallel([fn0, fn1, …], max_concurrent=N)` |
| Tick scheduler | `producer_daemon(tick=run_one_tick, interval_seconds=N, name=...)` |

`senpi-skills/GUIDE.md` Section 3 documents older `mcporter`-subprocess and `openclaw senpi external-scanner ingest` patterns — those stay published as a reference for skills authored before the wrapper landed and not yet migrated. New skills follow the table above.

## Why it exists

`senpi-trading-runtime/docs/runtime-v2-fixes/runtime-2-performance-findings.md` documents the failure mode the wrapper was built to eliminate:

- Per-call CLI cold start: 5–8 s to bootstrap Node + register the openclaw plugin.
- Per-call `mcp-remote` spawn: 6-process tree (`gateway → sh → python → node mcporter → npm exec → sh → node mcp-remote`), 250–300 MB transient RSS, 2.5–5 s per call.
- Cron + `agentTurn` coupling: every cron tick paid for a full LLM inference whose only job was to dispatch a python script.
- Fork-storm under concurrent load: kernel returns `EAGAIN`; tools fail.

The wrapper replaces all of this with a persistent HTTPS connection, an internal scheduler, and bounded fan-out. Producer ticks drop from minutes to ~4 seconds; per-MCP-call latency drops from 2.5 s to ~280 ms.

## Quick start

```python
import os, sys
from pathlib import Path

_helpers_path = str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)

from senpi_runtime_helpers import (
    SenpiClient, scanner_lock, tick_cache, parallel, producer_daemon,
)

client = SenpiClient()           # reads SENPI_MCP_URL + SENPI_AUTH_TOKEN from env
mcp = tick_cache(client)         # per-tick TTL cache wrapper

def run_one_tick():
    with scanner_lock(f"<skill>-{wallet_hash}"):
        markets = mcp("leaderboard_get_markets", limit=100)
        ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=wallet)
        # …gating logic…
        if signal_ready:
            client.push_signal(
                address=wallet,
                scanner="<skill>_signals",
                asset=asset,
                direction=direction,
                data={...},      # validated against scanner config.fields
            )

if __name__ == "__main__":
    producer_daemon(tick=run_one_tick, interval_seconds=300, name=f"<skill>-{wallet_hash}")
```

Three things to notice:

1. **No subprocess anywhere.** Both MCP and signal emission go through the persistent client.
2. **`scanner_lock` name should include the wallet** when a host runs multiple wallets — the lock file is `<lock_dir>/senpi-<name>.lock` and a per-wallet name keeps multi-wallet hosts safe.
3. **`producer_daemon` replaces openclaw cron.** No `agentTurn`, no per-tick LLM. The daemon stays alive across ticks; on tick failure it logs and continues to the next tick.

## What each helper does

| Helper | Replaces | Why |
|---|---|---|
| `SenpiClient.mcp_call(tool, **kwargs)` | `subprocess.run(["mcporter","call","senpi",tool,...])` | Direct streamable-HTTP to MCP. Eliminates the 6-process tree per call. ~10× faster (250–320 ms vs 2.5–5 s). |
| `SenpiClient.push_signal(...)` / `push_signals([...])` | `subprocess.run(["openclaw","senpi","external-scanner","ingest",...])` | HTTP POST to runtime `/signals` on `127.0.0.1:8787`. Eliminates the 5–8 s CLI cold start. Body is a bare `Array<SignalItem>` per the runtime schema. |
| `scanner_lock(name)` | `fcntl.flock(...)` | `flock` + PID-aliveness stale recovery. A crashed prior holder no longer permanently bricks future ticks. |
| `tick_cache(client)` | repeated identical MCP calls in one tick | Per-process LRU+TTL cache with thundering-herd coalescing on the same key. |
| `parallel([fn0, fn1, …], max_concurrent=N)` | serial loop of MCP calls | Bounded fan-out via `ThreadPoolExecutor`. Beyond `max_concurrent`, calls queue — never reject. |
| `producer_daemon(tick, interval_seconds, name)` | openclaw cron + `agentTurn` LLM dispatch | Internal scheduler with SIGALRM tick timeout, scanner_lock per tick, SIGTERM/SIGINT graceful shutdown. |

## Signal schema

Per `senpi-trading-runtime` `runtime-api/routes/signals.schema.ts`:

- **Top-level `SignalItem` fields** — `address`, `scanner`, `asset`, `direction`, `score` (0..1), `signal_type`. Pass them as kwargs to `push_signal(...)`.
- **Scanner-specific `data` block** — must match the `<scanner>.config.fields` declaration in `runtime.yaml`. Fields marked `required: true` must be present; everything else is free-form.
- **Score** — top-level `score` is 0..1. Producer-internal composites (e.g. funding-fade strategies typically score >=9) live in `data.score`; pass a separate normalized score, or omit, at the top level.

The one footgun worth a hard-stop: **keep `asset` and `direction` out of `data`.** They're top-level routing fields. Putting them in `data` makes the runtime store two copies (`signal.asset` vs `signal.meta.asset`) and downstream consumers read inconsistently — that's the failure mode that triggered `INVALID_REQUEST` rejections in the Pangolin tick-2 incident on 2026-05-05.

## Wall-clock timeouts (always on)

Every `mcp_call` and signal POST has a default timeout. Producers cannot accidentally hang forever. Override per call: `client.mcp_call(tool, timeout=60.0, **kwargs)`.

## Logging

Every helper emits JSON lines to **stderr** prefixed `[senpi_helpers]`. Stdout stays clean for skills that print signals to stdout. Field order is fixed: `ts → iso → pid → event → ...`.

```
[senpi_helpers] {"ts": ..., "event": "lock_acquired", "name": "pangolin-producer-a919c1e21a24"}
[senpi_helpers] {"ts": ..., "event": "mcp_call", "tool": "leaderboard_get_markets", "duration_ms": 265, "status": "ok"}
[senpi_helpers] {"ts": ..., "event": "cache_hit", "tool": "strategy_get_clearinghouse_state", "age_s": 0.78}
[senpi_helpers] {"ts": ..., "event": "signal_post", "batch_size": 1, "duration_ms": 12, "status": "ok"}
[senpi_helpers] {"ts": ..., "event": "daemon_tick_finished", "tick": 34, "duration_ms": 4011, "status": "ok"}
```

Filter by `[senpi_helpers]` in Railway logs to verify the wrapper is being used and to compare pre/post migration.

## Configuration — env vars only

| Env var | Default | Purpose |
|---|---|---|
| `SENPI_MCP_URL` | `https://mcp.prod.senpi.ai/mcp` | Direct MCP endpoint (no gateway). |
| `SENPI_AUTH_TOKEN` | _(required)_ | Bearer token for MCP. |
| `SENPI_RUNTIME_API_HOST` | `127.0.0.1` | Runtime API host (signals). |
| `SENPI_RUNTIME_API_PORT` | `8787` | Runtime API port (signals). |
| `SENPI_HELPERS_MCP_TIMEOUT` | `30.0` | Per-call MCP timeout (seconds). |
| `SENPI_HELPERS_SIGNAL_TIMEOUT` | `5.0` | Per-call signal POST timeout. |
| `SENPI_HELPERS_MAX_CONCURRENT` | `8` | Cap on in-flight `parallel(...)` calls. |
| `SENPI_HELPERS_TICK_CACHE_TTL` | `120.0` | Per-tick cache TTL (seconds). |
| `SENPI_HELPERS_TICK_CACHE_MAX_ENTRIES` | `512` | Hard cap on cached entries (LRU eviction). |
| `SENPI_HELPERS_LOCK_DIR` | `/tmp` | Where lock files live. |

## Migration cookbook

Migrating a legacy `mcporter_call`-based producer is mechanical:

1. Add the import shim from "Quick start" near the top of `<skill>/scripts/<skill>_config.py`.
2. Replace each `mcporter_call(tool, **params)` call with `_cached_mcp(tool, **params)`. The legacy `mcporter_call(...)` body can become a thin wrapper that delegates to `_cached_mcp(...)` and catches exceptions to preserve `None`-on-failure callers.
3. Replace the `subprocess.run(["openclaw","senpi","external-scanner","ingest",...])` block with `_client.push_signal(address=wallet, scanner="<skill>_signals", asset=..., direction=..., data={...})`.
4. Replace the cron-driven entry point with `if __name__ == "__main__": producer_daemon(tick=run_one_tick, interval_seconds=N, name=f"<skill>-{wallet_hash}")`. Remove the openclaw cron entry that used to dispatch this script.
5. Drop any hand-rolled `fcntl.flock(...)` lock — `scanner_lock(name)` inside `run_one_tick` replaces it.
6. Verify: tail the daemon log; `[senpi_helpers]` events should appear, gateway plugin re-registrations should drop to ~0/hour.

## Tests

Stdlib `unittest`. From the package directory:

```bash
python3 -m unittest discover -s tests -v
```

29 unit tests cover the client, lock, cache, parallel, and daemon modules. They run with no credentials (the client tests use a mock MCP and the daemon tests use stub callbacks).

## See also

- `_helpers/senpi_runtime_helpers/README.md` — the package's user-facing README, mostly overlaps this skill but is more concise.
- `senpi-trading-runtime/docs/runtime-v2-fixes/runtime-2-performance-findings.md` — the doc that motivated the wrapper.
- `senpi-skills/GUIDE.md` Section 3 — legacy `mcporter_call` pattern for skills not yet migrated.
- `senpi-skills/pangolin/scripts/pangolin-producer.py` — the canonical wrapper-based producer; copy this skeleton when authoring a new skill.
