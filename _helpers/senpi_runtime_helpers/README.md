# senpi_runtime_helpers

Stdlib-only Python wrapper for senpi-skills producers. Replaces the legacy
`mcporter` subprocess and `openclaw senpi external-scanner ingest` CLI
patterns with a persistent HTTPS MCP client, a daemon scheduler,
`scanner_lock` with stale-PID recovery, bounded `parallel(...)` fan-out,
and a per-tick TTL cache.

> **The agent-facing documentation is in [`SKILL.md`](SKILL.md).**
> If you're authoring or migrating a producer, read SKILL.md — it has
> the rules, copy-paste recipes, error → fix mapping, and env var table.

## What's in this package

| File | What |
|---|---|
| [`SKILL.md`](SKILL.md) | Agent-optimized usage doc — start here. |
| [`client.py`](client.py) | `SenpiClient`: persistent HTTPS to MCP + `/signals` POST to runtime. |
| [`daemon.py`](daemon.py) | `producer_daemon`: long-running scheduler. Replaces openclaw cron + agentTurn. |
| [`lock.py`](lock.py) | `scanner_lock`: liveness-aware fcntl lock; recovers stale locks from crashed prior holders. |
| [`parallel.py`](parallel.py) | `parallel`: concurrency-bounded fan-out. Calls beyond cap queue, never reject. |
| [`cache.py`](cache.py) | `tick_cache`: per-tick TTL+LRU memoization with thundering-herd coalescing. |
| [`state.py`](state.py) | Daemon self-describing state files (`pid.json` / `boot.json` / `heartbeat.json`). |
| [`stats.py`](stats.py) | Log-parsing hourly bucket aggregator for `senpi-helpers stats`. |
| [`manage.py`](manage.py) | Process-control primitives (`stop_pid`, `relaunch_daemon`) for `senpi-helpers stop` / `restart`. |
| [`cli.py`](cli.py) + [`../senpi-helpers`](../senpi-helpers) | `senpi-helpers` operator CLI — list / health / stats / stop / restart. |
| [`_logging.py`](_logging.py) | Structured `[senpi_helpers]` JSON event logger to stderr. |
| [`tests/`](tests/) | Stdlib `unittest`; no credentials needed. |
| [`references/`](references/) | Long-form deep-dive docs (signal schema, CLI reference). |

## Tests

```bash
cd _helpers/senpi_runtime_helpers
python3 -m unittest discover -s tests -v
```

## License

MIT.
