# senpi_runtime_helpers

Stdlib-only Python wrapper for senpi-skills producers. Replaces the legacy
`mcporter` subprocess and `openclaw senpi external-scanner ingest` CLI
patterns with a persistent HTTPS MCP client, a daemon scheduler,
`scanner_lock` with stale-PID recovery, bounded `parallel(...)` fan-out, and
a per-tick TTL cache.

> **The full agent-facing documentation is in [`SKILL.md`](SKILL.md).**
> If you are an LLM authoring or migrating a producer, read SKILL.md — it has
> a decision tree, copy-paste recipes for the 6 common producer tasks, and
> an error-message → fix mapping. Keep this README short on purpose.

## Why it exists

Per-call `mcporter` spawns a 6-process tree (250–300 MB transient RSS, 2.5–5 s
per call). Per-call `openclaw senpi external-scanner ingest` cold-starts a
Node CLI (5–8 s per call). The wrapper replaces both with a persistent HTTPS
connection. Producer tick wall-clock drops from minutes to ~4 seconds; per
MCP-call latency drops from ~2.5 s to ~280 ms. Full background:
[`references/architecture.md`](references/architecture.md).

## What's in this package

| File | What |
|---|---|
| [`SKILL.md`](SKILL.md) | Agent-optimized usage doc — start here. |
| [`client.py`](client.py) | `SenpiClient`: persistent HTTPS to MCP + `/signals` POST to runtime. |
| [`daemon.py`](daemon.py) | `producer_daemon`: long-running scheduler that replaces openclaw cron + agentTurn. |
| [`lock.py`](lock.py) | `scanner_lock`: liveness-aware fcntl lock; recovers stale locks from crashed prior holders. |
| [`parallel.py`](parallel.py) | `parallel`: concurrency-bounded fan-out. Calls beyond cap queue, never reject. |
| [`cache.py`](cache.py) | `tick_cache`: per-tick TTL+LRU memoization with thundering-herd coalescing. |
| [`_logging.py`](_logging.py) | Structured `[senpi_helpers]` JSON event logger to stderr. |
| [`tests/`](tests/) | Stdlib `unittest` — 51 tests, no credentials needed. |
| [`references/`](references/) | Long-form deep-dive docs (migration cookbook, architecture, signal schema). |

## Quick start

The 30-second example — just to give shape. The full version, with all
recipes (parallel fan-out, per-tick cache, daemon scheduling, error handling),
is in [`SKILL.md`](SKILL.md).

```python
import os, sys
from pathlib import Path

_helpers_path = str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)

from senpi_runtime_helpers import SenpiClient, scanner_lock, tick_cache

client = SenpiClient()                # reads SENPI_MCP_URL + SENPI_AUTH_TOKEN
mcp = tick_cache(client)

with scanner_lock(f"my-skill-{wallet[2:10]}"):
    ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if signal_ready:
        client.push_signal(
            address=wallet, scanner="my_signals",
            asset="BTC", direction="LONG",      # routing — top level
            data={"funding_bps": 18},          # scanner-specific
        )
```

The reference wrapper-based producer is
[`pangolin/scripts/pangolin-producer.py`](../../pangolin/scripts/pangolin-producer.py)
— copy its skeleton when authoring a new skill.

## Tests

```bash
cd _helpers/senpi_runtime_helpers
python3 -m unittest discover -s tests -v
```

## License

MIT.
