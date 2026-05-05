# senpi_runtime_helpers

Stdlib-only Python helper for senpi-skills producers running under runtime-2.

Turns good practices into the path of least resistance: producers shorten,
the wrapper handles MCP transport, locks, fan-out, caching, timeouts.

## Quick start

```python
import sys
sys.path.insert(0, "/data/workspace/skills/_helpers")  # or wherever this package lives
from senpi_runtime_helpers import SenpiClient, scanner_lock, tick_cache, parallel

client = SenpiClient()  # reads SENPI_MCP_URL + SENPI_AUTH_TOKEN from env
mcp = tick_cache(client)

with scanner_lock("phoenix"):
    markets = mcp("leaderboard_get_markets", limit=100)
    ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=wallet)
```

## What problem each helper solves

| Helper | Replaces | Why |
|---|---|---|
| `client.mcp_call(tool, **kwargs)` | `subprocess.run(["mcporter","call","senpi",tool,...])` | Direct HTTPS to MCP. Kills the 6-process tree (`gateway → sh → python → node mcporter → npm exec → sh → node mcp-remote`) per call. Saves 250-300 MB transient RSS and 2.5-5s per call. |
| `client.signal(...)` / `client.signals([...])` | `subprocess.run(["openclaw","senpi","external-scanner","ingest",...])` | HTTP POST to the runtime's `/signals` endpoint on `127.0.0.1`. Saves the 5-8s CLI cold start. |
| `scanner_lock(name)` | hand-rolled `fcntl.flock(...)` | PID + heartbeat-mtime liveness check; auto-recovers from killed/crashed processes. No more "manual `rm /tmp/foo.lock`" runbook. |
| `parallel([...])` | serial loop of MCP calls | Concurrency-bounded fan-out. Beyond `max_concurrent` calls **queue**, never reject. |
| `tick_cache(client)` | repeated identical MCP calls in one tick | Per-process TTL cache. Same tool + same args within `TICK_CACHE_TTL`s reuses the result. |

## Wall-clock timeouts (enforced default)

Every `mcp_call` and `signal` has a default timeout. Producers cannot accidentally
hang forever. Override per call:

```python
markets = client.mcp_call("leaderboard_get_markets", timeout=60.0, limit=100)
```

## Configuration — all env vars, sensible defaults

| Env var | Default | Purpose |
|---|---|---|
| `SENPI_MCP_URL` | `https://mcp.prod.senpi.ai/mcp` | Direct MCP endpoint (no gateway) |
| `SENPI_AUTH_TOKEN` | _(required)_ | Bearer token for MCP |
| `SENPI_RUNTIME_API_HOST` | `127.0.0.1` | Runtime API host (signals) |
| `SENPI_RUNTIME_API_PORT` | `18790` | Runtime API port (signals) |
| `SENPI_HELPERS_MCP_TIMEOUT` | `30.0` | Per-call MCP timeout (seconds) |
| `SENPI_HELPERS_SIGNAL_TIMEOUT` | `5.0` | Per-call signal POST timeout |
| `SENPI_HELPERS_MAX_CONCURRENT` | `8` | Cap on in-flight parallel MCP calls |
| `SENPI_HELPERS_QUEUE_WARN_DEPTH` | `50` | Warn when this many calls queue |
| `SENPI_HELPERS_TICK_CACHE_TTL` | `120.0` | Per-tick cache TTL (seconds) |
| `SENPI_HELPERS_LOCK_HEARTBEAT_TIMEOUT` | `300.0` | Stale-lock recovery threshold |
| `SENPI_HELPERS_LOCK_DIR` | `/tmp` | Where lock files live |

## Logs — proof the fix works

Every helper emits structured JSON lines to **stderr** prefixed `[senpi_helpers]`.
Stdout stays clean for skills that print signals to stdout (Phoenix, etc.).

```
[senpi_helpers] {"ts":..., "event":"mcp_call", "tool":"leaderboard_get_markets", "duration_ms":380, "status":"ok"}
[senpi_helpers] {"ts":..., "event":"cache_hit", "tool":"strategy_get_clearinghouse_state", "age_s":12.3}
[senpi_helpers] {"ts":..., "event":"lock_acquired", "name":"phoenix", "pid":12345}
[senpi_helpers] {"ts":..., "event":"lock_stale_recovered", "name":"phoenix", "prev_pid":99998, "prev_age_s":421, "prev_alive":false}
[senpi_helpers] {"ts":..., "event":"parallel_complete", "total":9, "failed":0, "duration_ms":420}
[senpi_helpers] {"ts":..., "event":"signal_post", "batch_size":1, "bytes":284, "duration_ms":12, "status":"ok"}
```

Filter by `[senpi_helpers]` in Railway log search to compare pre/post migration.

## Tests

Stdlib `unittest`. Run from this directory:

```bash
python3 -m unittest discover -s tests -v
```

## Migration cookbook (per skill, opt-in)

1. Drop a small import shim near the top of `<skill>/scripts/<skill>_config.py`:
   ```python
   import sys, os
   _HELPERS = os.path.join(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"), "skills", "_helpers")
   if _HELPERS not in sys.path:
       sys.path.insert(0, _HELPERS)
   from senpi_runtime_helpers import SenpiClient, scanner_lock, tick_cache
   _client = SenpiClient()
   _cached_mcp = tick_cache(_client)
   ```
2. Replace `mcporter_call(tool, **params)` body with `return _cached_mcp(tool, **params)` (or just call `_cached_mcp` at the call sites).
3. Optionally wrap the scanner's `run()` in `with scanner_lock("<skill>"):`.
4. Verify `[senpi_helpers]` logs appear in stderr after one tick.

Each skill commit is independent and cherry-pickable. Wrapper commit must
land first.
