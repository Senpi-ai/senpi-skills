# Architecture & rationale

Background for `senpi_runtime_helpers`. Read [`../SKILL.md`](../SKILL.md) first
for usage; this doc is the "why."

## What the legacy stack looked like (pre-runtime-2)

```
openclaw cron (every N min) → agentTurn (full LLM inference)
                            → exec("python <skill>-producer.py")
                                       │
                                       ├─ subprocess.run(["mcporter", "call", "senpi", tool, …])
                                       │     └─ spawns: gateway → sh → python → node mcporter → npm exec → sh → node mcp-remote
                                       │     └─ 250–300 MB transient RSS, 2.5–5 s per MCP call
                                       │
                                       └─ subprocess.run(["openclaw", "senpi", "external-scanner", "ingest", …])
                                             └─ cold-starts Node CLI: 5–8 s per signal emit
```

## Failure modes that triggered the rewrite

From `senpi-trading-runtime/docs/runtime-v2-fixes/runtime-2-performance-findings.md`:

- **Per-call CLI cold start** — 5–8 s to bootstrap Node + register the
  openclaw plugin every time a signal was emitted.
- **Per-call `mcp-remote` spawn** — 6-process tree, 250–300 MB transient
  RSS, 2.5–5 s per MCP call.
- **Cron + `agentTurn` coupling** — every cron tick paid for a full LLM
  inference whose only job was to dispatch a Python script. Hundreds of
  agent runs/day with 80–200 s latency each.
- **Fork-storm under concurrent load** — when multiple producers overlapped
  on overlapping cron schedules, kernel returned `EAGAIN` from `fork()`;
  tools failed; agent stalled.
- **Memory spikes** — overlapping bursts pushed gateway RSS to 2–2.5 GB,
  then crashes restarted the box.
- **Latency cascade** — by the time a signal reached the exchange, the
  price was stale. Hyperliquid rejected ALO orders that would cross the
  spread.
- **Audit blind spot** — even when an order was rejected, `audit_query`
  reported `success: true` (root cause: MCP audit logger read outer
  envelope, not inner data — fixed in `senpi-hyperliquid-mcp`).

## What the wrapper does instead

```
producer_daemon (long-running Python process)
  loop forever:
    sleep(interval_seconds)
    with scanner_lock(name):              # stale-PID recovery
       run_one_tick()
                │
                ├─ SenpiClient.mcp_call(tool, **kwargs)
                │     └─ persistent HTTPS keep-alive to MCP. ~280 ms typical.
                │     └─ NO subprocess. NO 6-process spawn tree.
                │
                ├─ tick_cache: same tool+args within TTL → cache hit
                │
                ├─ parallel([fn0, fn1, …]) → ThreadPoolExecutor, bounded
                │
                └─ SenpiClient.push_signal(...)
                      └─ HTTP POST to 127.0.0.1:8787/signals on the runtime.
                      └─ ~12 ms typical. NO 5–8 s cold start.
```

## Measured impact (from migration-test boxes)

| Metric | Pre (legacy) | Post (wrapper) |
|---|---|---|
| MCP call latency (median) | 2.5–5 s | ~280 ms |
| Signal-emit latency (p95) | 5–8 s | < 500 ms |
| Producer tick wall-clock (Polar, 9 MCP calls) | 30–60 s | ~4 s |
| Gateway plugin re-registrations (3.5 days) | 605 | ~0 |
| OOM crashes during overlapping ticks | recurring | none observed |

Full pre/post comparison methodology:
`senpi-trading-runtime/docs/runtime-v2-fixes/wrapper-plan.md`.

## Why MCP goes direct, not through the gateway

The openclaw gateway provides a useful UNIX-domain socket for tool dispatch,
but every gateway call goes through `mcporter` → `mcp-remote` (the 6-process
tree). For producers, that overhead is pure cost: producers don't need
gateway-side features (no LLM, no plugin orchestration, no auth integration —
producers carry their own Bearer token). Direct HTTPS to the MCP server
eliminates the entire spawn tree.

The runtime, by contrast, still goes through the gateway for its OpenClaw
plugin contract (CLI commands, lifecycle hooks). Different tradeoffs.

## Why signals goes to localhost, not the gateway

The runtime exposes `/signals` on `127.0.0.1:8787` inside the openclaw
container. Producers run inside the same container, so localhost POST has
zero network round-trip. The HTTP envelope is the senpi-stack
`{ success, data, error }` shape (matching `senpi-hyperliquid-mcp`); the
wrapper parses both success and error envelopes from this shape.

If a producer needs to reach the runtime from outside the container (for
debugging via Postman, etc.), the runtime's `api.host` config can be flipped
to `0.0.0.0` and a docker-compose port mapping added — see
`senpi-trading-runtime/docs/runtime-docs/runtime-api.md` § "Postman testing
recipe."

## Why a daemon, not openclaw cron

The original cron design wrapped every tick in a `agentTurn` LLM call. The
LLM's only job was to invoke `exec("python …producer.py")` — a non-decision
that paid for a full inference each time. Daemon-mode skips the agentTurn
entirely:

- Producer is a long-lived Python process.
- Internal scheduler fires `run_one_tick()` every `interval_seconds`
  (the callable is passed as `producer_daemon(fn=run_one_tick, ...)`).
- SIGTERM / SIGINT trigger graceful shutdown.
- Per-tick `scanner_lock` prevents accidental re-entry.

How the producer is started is a host-side concern — anything from a
one-shot `nohup python3 …-producer.py &` to a Procfile or supervisor entry
works. The daemon's lifecycle (SIGTERM-graceful shutdown, per-tick error
containment) is the same regardless.

## Why scanner_lock has PID-aliveness recovery

`fcntl.flock` is reliable inside one OS but doesn't survive a hard crash:
the lock file remains, and the next instance blocks forever waiting for a
lock holder that no longer exists. `scanner_lock` writes its PID + a
heartbeat timestamp into the lock file; the next caller checks whether the
prior PID is still alive (via `os.kill(pid, 0)`). If not, it logs
`lock_recovered_after_crash` and proceeds.

This eliminates the failure mode where one crashed tick permanently bricks
the producer until manual intervention.

## Why `parallel(...)` queues instead of rejecting

Backpressure design choice. When 9 MCP calls fan out and 8 is the cap
(`SENPI_HELPERS_MAX_CONCURRENT`), the 9th call queues for ~30 ms. Rejecting
would force every call site to handle retry — every producer would re-implement
the same backoff. Queuing centralises that concern, with a warning log when
queue depth exceeds `SENPI_HELPERS_QUEUE_WARN_DEPTH` (default 50) so operators
notice runaway producers.
