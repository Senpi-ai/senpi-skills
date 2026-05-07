---
name: senpi-runtime-helpers
description: >-
  Python wrapper for senpi-skills producers: persistent MCP client, signal
  emission, scanner_lock, parallel fan-out, tick cache, daemon scheduler.
  Replaces mcporter subprocess and `openclaw senpi external-scanner ingest`
  CLI calls.


  Triggers: producer authoring, scanner authoring, external_scanner producer,
  mcporter, mcporter_call, external-scanner ingest, push_signal,
  SenpiClientError, scanner_lock, tick_cache, parallel MCP, producer_daemon,
  fork-storm, runtime-2 producer migration.
license: MIT
compatibility: >-
  Python 3.10+. Stdlib only — no third-party deps. Requires the
  senpi-trading-runtime build that ships the senpi-stack response envelope on
  /signals and /audit (success / data / error). The wrapper does not parse the
  legacy { results: [...] } envelope. Currently verified against
  runtime-phase-2-api dev pin v1.0.95-dev.runtime-phase-2-api.20260507134852 —
  see references/runtime-deployment.md for the matching senpi-skills
  branch pin. After the runtime ships its release line, follow the release
  version. Loaded from `${OPENCLAW_WORKSPACE:-/data/workspace}/skills/_helpers/`.
metadata:
  author: senpi
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi_runtime_helpers — wrapper for Senpi producers

This is the canonical Python client for everything a producer needs:
calling MCP, emitting signals, locking per tick, fanning out in parallel,
caching, scheduling. **One import; no subprocesses.**

[`pangolin/scripts/pangolin-producer.py`](../../pangolin/scripts/pangolin-producer.py)
is the reference wrapper-based producer. Copy its skeleton when starting a new
skill.

## Glossary

- **Producer** — Python script in a Senpi skill (e.g. `<skill>/scripts/<skill>-producer.py`) that runs on a schedule, calls MCP for market data, evaluates a trading thesis, and pushes signals to a `senpi-trading-runtime` instance.
- **Scanner / external_scanner** — the runtime-side declaration (in `runtime.yaml`) that names a producer's signal stream and validates the `data` block against `config.fields`. The producer's `client.push_signal(scanner=…)` must match this name.
- **Runtime** — `senpi-trading-runtime`, the OpenClaw plugin that consumes producer signals at `POST /signals` (on `127.0.0.1:8787`), routes them through the LLM gauntlet + risk gates, opens positions, and runs the DSL exit engine.
- **Daemon** — `producer_daemon(...)` from this wrapper. A long-lived Python process that fires `run_one_tick()` on a fixed interval. Replaces openclaw cron + agentTurn (which paid for a full LLM inference per tick to dispatch a Python script).

---

## When to use

- Authoring a **new** producer / scanner / external-scanner Python script.
- Migrating an **existing** producer that uses `subprocess.run(["mcporter", …])`
  or `subprocess.run(["openclaw", "senpi", "external-scanner", "ingest", …])`.
- Calling **any** Senpi MCP tool from Python in this repo.
- Pushing signals to a `senpi-trading-runtime` instance over `/signals`.

## When NOT to use

- You're editing the runtime itself (`senpi-trading-runtime/`) — that's
  TypeScript; this helper is for Python producers.
- You're writing a non-Python tool. The runtime accepts any HTTP client; this
  wrapper is just the Python one.

---

## ⚠️ The one footgun that costs trading capital

**Keep `asset` and `direction` out of the `data` block.** They are top-level
routing fields on `SignalItem`. Putting them in `data` makes the runtime store
two copies (`signal.asset` vs `signal.meta.asset`) which downstream consumers
read inconsistently — that triggered `INVALID_REQUEST` rejections in the
Pangolin TST incident on 2026-05-05.

**Right:**
```python
client.push_signal(
    address=wallet, scanner="my_signals",
    asset="BTC", direction="LONG",        # routing → top level
    score=0.85,                            # confidence 0..1 → top level
    data={"rsi": 75, "funding_bps": 18},  # scanner-specific → data
)
```

**Wrong:**
```python
client.push_signal(
    address=wallet, scanner="my_signals",
    data={"asset": "BTC", "direction": "LONG", "score": 0.85},  # ❌
)
```

---

## Decision tree — pick your starting point

| What you need to do | Jump to |
|---|---|
| Write a new producer from scratch | [Recipe: New producer](#recipe-new-producer) |
| Migrate a legacy `mcporter` / CLI producer | [Recipe: Migrate legacy producer](#recipe-migrate-legacy-producer) |
| Emit a signal to the runtime | [Recipe: Emit a signal](#recipe-emit-a-signal) |
| Fan out N parallel MCP calls in one tick | [Recipe: Parallel MCP fan-out](#recipe-parallel-mcp-fan-out) |
| Reuse identical MCP results within a tick | [Recipe: Per-tick cache](#recipe-per-tick-cache) |
| Schedule a recurring tick (replaces openclaw cron) | [Recipe: Daemon scheduling](#recipe-daemon-scheduling) |
| Debug a `SenpiClientError` from a producer | [Errors → fixes](#errors--fixes) |

---

## The import shim (paste at top of every producer file)

This is the only import boilerplate you ever need:

```python
import os, sys
from pathlib import Path

_helpers_path = str(
    Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"))
    / "skills" / "_helpers"
)
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)

from senpi_runtime_helpers import (
    SenpiClient, SenpiClientError,
    scanner_lock, tick_cache, parallel, producer_daemon,
)
```

`SenpiClient()` reads `SENPI_MCP_URL`, `SENPI_AUTH_TOKEN`,
`SENPI_RUNTIME_API_HOST`, `SENPI_RUNTIME_API_PORT` from env.

---

## Recipes

### Recipe: New producer

Full self-contained skeleton. Adapt `<skill>`, `<scanner_name>`, env-var
names, and the signal payload to your strategy.

```python
# scripts/<skill>-producer.py — wrapper-based producer (replaces openclaw cron)
import os, sys
from pathlib import Path

_helpers_path = str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)

from senpi_runtime_helpers import (
    SenpiClient, scanner_lock, tick_cache, producer_daemon,
)

WALLET = os.environ["<SKILL>_WALLET"]                      # e.g. PANGOLIN_WALLET
SCANNER_NAME = "<scanner_name>"                            # matches runtime.yaml
LOCK_NAME = f"<skill>-{WALLET[2:10]}"                      # per-wallet → multi-wallet hosts safe

client = SenpiClient()                                     # MCP + signals client
mcp = tick_cache(client)                                   # per-tick TTL memoization

def run_one_tick():
    with scanner_lock(LOCK_NAME):
        ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)
        markets = mcp("leaderboard_get_markets", limit=100)
        # ... gating logic ...
        if signal_ready:
            client.push_signal(
                address=WALLET,
                scanner=SCANNER_NAME,
                asset="BTC", direction="LONG",  # routing
                score=0.85,                      # 0..1 confidence
                data={"funding_bps": 18},       # scanner-specific
            )

if __name__ == "__main__":
    producer_daemon(
        fn=run_one_tick,
        interval_seconds=300,                   # 5-minute ticks
        name=LOCK_NAME,
    )
```

### Recipe: Migrate legacy producer

Mechanical replacements — apply in order:

| Old (legacy) | New (wrapper) |
|---|---|
| `subprocess.run(["mcporter", "call", "senpi", tool, …])` → parse JSON | `client.mcp_call(tool, **kwargs)` |
| Repeated identical `mcporter_call` in one tick | `mcp = tick_cache(client); mcp(tool, **kwargs)` |
| Serial loop of MCP calls | `parallel([lambda: mcp(t1, …), lambda: mcp(t2, …)])` |
| `subprocess.run(["openclaw", "senpi", "external-scanner", "ingest", …])` | `client.push_signal(address=…, scanner=…, asset=…, direction=…, data={…})` |
| Hand-rolled `fcntl.flock(...)` lock | `with scanner_lock(name): …` |
| openclaw cron entry → invokes script per tick | `producer_daemon(fn=run_one_tick, interval_seconds=N, name=…)` and remove the cron entry |

**Verify after migration** (one tick is enough — no need to wait for a candidate):

- `[senpi_helpers]` log events appear in stderr (filter Railway logs by `[senpi_helpers]`).
- Tick wall-clock drops ~10× (e.g. 30–60 s → ~4 s for a 9-MCP-call producer).
- `mcp_initialized` event appears exactly once per process lifetime (proves keep-alive is working).
- Per-MCP-call `duration_ms` lands in 250–500 ms typical (was 2.5–5 s under mcporter).
- Gateway log: plugin re-registrations drop to ~0/hour (was 605 in 3.5 days under legacy stack).

Long-form migration steps with before/after snippets — including the cron-cleanup
and lock-name conventions for multi-wallet hosts —
[`references/migration-cookbook.md`](references/migration-cookbook.md).

### Recipe: Emit a signal

```python
client.push_signal(
    address=wallet,
    scanner="my_signals",        # must match runtime.yaml `external_scanner.name`
    asset="BTC",                 # uppercase Hyperliquid ticker
    direction="LONG",            # "LONG" | "SHORT" — strict
    score=0.85,                  # 0..1 — top-level confidence
    signal_type="MOMENTUM",      # optional override; falls back to defaultSignalType
    data={                        # validated against scanner config.fields
        "rsi": 75,
        "funding_bps": 18,
    },
)
```

Batch form for emitting N signals in one HTTP round-trip:

```python
client.push_signals([
    {"address": wallet, "scanner": "my_signals", "asset": "BTC", "direction": "LONG", "data": {…}},
    {"address": wallet, "scanner": "my_signals", "asset": "ETH", "direction": "LONG", "data": {…}},
])
```

The helper raises `SenpiClientError` if **any** item is rejected. The runtime
itself is **not** atomic — successful items WERE ingested even when the helper
raises. If your producer needs per-item outcome, push one at a time (the
pangolin pattern) or catch and inspect.

Full schema: [`references/signal-schema.md`](references/signal-schema.md).

### Recipe: Parallel MCP fan-out

```python
results = parallel([
    lambda: mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET),
    lambda: mcp("leaderboard_get_markets", limit=100),
    lambda: mcp("market_get_funding_history", coin="BTC", limit=24),
])
ch, markets, funding = results
```

`parallel(...)` is bounded by `SENPI_HELPERS_MAX_CONCURRENT` (default 8).
Calls beyond the cap **queue**, never reject. Combine with `tick_cache` so
duplicated calls in the same tick don't hit the wire twice.

### Recipe: Per-tick cache

```python
mcp = tick_cache(client)        # wrap once at module load
ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)
# ... 30 lines later ...
ch_again = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)  # cache hit, no HTTP
```

TTL defaults to 120 s (`SENPI_HELPERS_TICK_CACHE_TTL`). Hard-cap entries:
512 (`SENPI_HELPERS_TICK_CACHE_MAX_ENTRIES`, LRU eviction).

### Recipe: Daemon scheduling

```python
producer_daemon(
    fn=run_one_tick,             # the per-tick callable
    interval_seconds=300,        # 5-minute ticks
    name=f"<skill>-{wallet_hash}",  # used by scanner_lock + log fields
)
```

Replaces openclaw cron + `agentTurn`. The daemon stays alive across ticks; on
tick failure it logs and continues to the next tick. SIGTERM / SIGINT trigger
graceful shutdown.

---

## Errors → fixes

| Error message contains | Likely cause | Fix |
|---|---|---|
| `signal_post: response body was empty` | Proxy/sidecar stripped the body | Check container network, `SENPI_RUNTIME_API_HOST/PORT` |
| `signal_post: response not valid JSON` | Mid-stream truncation (TLS, broken proxy) | Network instability; retry on next tick. Inspect first 200 bytes in error message |
| `signal_post: unexpected envelope shape` | Helper version expects `{success, data, error}`; runtime returned the legacy `{results: …}` | Bump runtime to ≥ `runtime-phase-2-api.*` (the helper requires the new envelope) |
| `signal_post: N/M item(s) rejected; first: code=INVALID_REQUEST …` | Per-item schema violation. **Most common: `asset`/`direction` inside `data`** | Move `asset`/`direction` to top level. Verify `data` keys match `runtime.yaml` `config.fields` |
| `signal_post: N/M item(s) rejected; first: code=NOT_FOUND` | No runtime is registered for the wallet, or the scanner name doesn't exist in `runtime.yaml` | Verify the runtime is installed for the wallet (`openclaw senpi runtime list`); verify `scanner` matches `runtime.yaml` |
| `signal_post: HTTP 400 INVALID_REQUEST: Exceeded api.maxItemsPerSignalsRequest=10` | Batch larger than runtime cap | Split the batch (default cap is 10) |
| `signal_post: HTTP 4xx/5xx ENVELOPE_CODE: …` | Runtime rejected the envelope itself | Read the human message in the exception — it's the runtime's diagnostic |
| `MCP error: …` from `mcp_call` | MCP server reported a tool-side error | Check tool name + arguments against `senpi-hyperliquid-mcp` schema |
| `urllib.error.URLError` from `mcp_call` | Network / connect / TLS failure | Verify `SENPI_MCP_URL`, `SENPI_AUTH_TOKEN`. Retry transient errors at next tick |
| `socket.timeout` from `mcp_call` or `signal_post` | Wall-clock timeout exceeded | Override per-call: `client.mcp_call(tool, timeout=60.0, …)` |
| `lock_stale_recovered` (info, not error) | Previous holder crashed; lock auto-recovered | No action; this is the intended self-healing behaviour |

---

## Configuration — env vars only

| Env var | Default | Purpose |
|---|---|---|
| `SENPI_MCP_URL` | `https://mcp.prod.senpi.ai/mcp` | Direct MCP endpoint (no gateway) |
| `SENPI_AUTH_TOKEN` | _(required)_ | Bearer token for MCP |
| `SENPI_RUNTIME_API_HOST` | `127.0.0.1` | Runtime API host (signals) |
| `SENPI_RUNTIME_API_PORT` | `8787` | Runtime API port (signals) |
| `SENPI_HELPERS_MCP_TIMEOUT` | `30.0` | Per-call MCP timeout (seconds) |
| `SENPI_HELPERS_SIGNAL_TIMEOUT` | `5.0` | Per-call signal POST timeout (seconds) |
| `SENPI_HELPERS_MAX_CONCURRENT` | `8` | Cap on in-flight `parallel(...)` calls |
| `SENPI_HELPERS_QUEUE_WARN_DEPTH` | `50` | Warn when this many calls queue |
| `SENPI_HELPERS_TICK_CACHE_TTL` | `120.0` | Per-tick cache TTL (seconds) |
| `SENPI_HELPERS_TICK_CACHE_MAX_ENTRIES` | `512` | Hard cap on cached entries (LRU) |
| `SENPI_HELPERS_LOCK_DIR` | `/tmp` | Where lock files live |

`OPENCLAW_WORKSPACE` (default `/data/workspace`) is read by the import shim
to locate this package.

---

## Logging

Every helper emits JSON lines to **stderr** prefixed `[senpi_helpers]`.
Stdout stays clean for skills that print to stdout. Field order is fixed:
`ts → iso → pid → event → ...`.

```
[senpi_helpers] {"ts": …, "event": "lock_acquired",       "name": "pangolin-a919c1e2"}
[senpi_helpers] {"ts": …, "event": "mcp_call",            "tool": "leaderboard_get_markets", "duration_ms": 265, "status": "ok"}
[senpi_helpers] {"ts": …, "event": "cache_hit",           "tool": "strategy_get_clearinghouse_state", "age_s": 0.78}
[senpi_helpers] {"ts": …, "event": "signal_post",         "batch_size": 1, "duration_ms": 12, "status": "ok"}
[senpi_helpers] {"ts": …, "event": "daemon_tick_finished","tick": 34, "duration_ms": 4011, "status": "ok"}
```

Filter Railway logs by `[senpi_helpers]` to verify the wrapper is in use and
to compare pre/post-migration.

---

## Tests

Stdlib `unittest` — no credentials needed.

```bash
cd _helpers/senpi_runtime_helpers
python3 -m unittest discover -s tests -v
```

35 tests cover client (HTTP + envelope parsing), lock (PID-aliveness recovery),
cache (TTL + LRU + thundering-herd coalescing), parallel (concurrency cap),
daemon (tick lifecycle + signal handling).

---

## See also

- [`references/migration-cookbook.md`](references/migration-cookbook.md) — long-form migration with before/after snippets.
- [`references/architecture.md`](references/architecture.md) — why this exists, performance numbers, incident background.
- [`references/signal-schema.md`](references/signal-schema.md) — full `SignalItem` shape, validation rules, `data` block conventions.
- [`pangolin/scripts/pangolin-producer.py`](../../pangolin/scripts/pangolin-producer.py) — reference wrapper-based producer.
- [`senpi-trading-runtime/SKILL.md`](../../senpi-trading-runtime/SKILL.md) — the runtime that consumes signals from this helper.
