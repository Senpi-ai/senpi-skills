---
name: senpi-runtime-helpers
description: >-
  Python client for senpi-skills producers: persistent MCP client, signal
  emission, scanner_lock, parallel fan-out, tick cache, daemon scheduler.
  Replaces mcporter subprocess + `openclaw senpi external-scanner ingest`.


  Triggers: producer authoring, scanner authoring, mcporter, mcporter_call,
  external-scanner ingest, push_signal, SenpiClientError, scanner_lock,
  tick_cache, parallel MCP, producer_daemon, runtime-2 migration.
license: MIT
compatibility: >-
  Python 3.10+. Stdlib only. Requires senpi-trading-runtime >= 2.0.0
  (uses the 2.0 senpi-stack `{success, data, error}` envelope on /signals
  and GET /state for liveness — neither shipped in the 1.x line). Loaded
  from `${OPENCLAW_WORKSPACE:-/data/workspace}/skills/_helpers/`.
metadata:
  author: senpi
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi_runtime_helpers

Python client for senpi-skills producers. Calls Senpi MCP, emits signals to
senpi-trading-runtime, locks per tick, fans out in parallel, caches per tick,
schedules ticks. Stdlib only. **No subprocesses, no openclaw gateway.**

Reference producer:
[`pangolin/scripts/pangolin-producer.py`](../../pangolin/scripts/pangolin-producer.py)
— copy its skeleton.

## When

- Authoring or migrating a producer.
- Calling any Senpi MCP tool from Python.
- Pushing signals over `/signals`.

## When NOT

- Editing `senpi-trading-runtime/` (TypeScript).
- Non-Python tools (the runtime accepts any HTTP client).

---

## Rules — read before writing code

### `asset` and `direction` go top-level, never inside `data`

```python
# RIGHT
client.push_signal(
    address=wallet, scanner="my_signals",
    asset="BTC", direction="LONG",
    score=0.85,
    data={"rsi": 75},
)

# WRONG — runtime rejects with INVALID_REQUEST
client.push_signal(
    address=wallet, scanner="my_signals",
    data={"asset": "BTC", "direction": "LONG"},
)
```

### `push_signal()` extracts only the kwargs you pass

Wire schema is `additionalProperties: false`. Stray top-level fields are
rejected; stray `data` keys not declared in the scanner's `config.fields`
are rejected.

- Pass as kwargs: `address`, `scanner`, `asset`, `direction`, `score`,
  `signal_type`, `data`.
- Server-set, never pass: `timestamp`, `factors`.

### Always pass `signal_type=` explicitly

Fallback is the scanner's `defaultSignalType` from `runtime.yaml`. Most
skills don't declare one, so the omission lands empty type tags in audit
logs and LLM decision context.

---

## Import shim

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

## Recipe: New producer

Self-contained skeleton. Adapt `<skill>`, scanner name, env vars, payload.

```python
# scripts/<skill>-producer.py
import os, sys
from pathlib import Path

_helpers_path = str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)

from senpi_runtime_helpers import (
    SenpiClient, scanner_lock, tick_cache, producer_daemon,
)

WALLET = os.environ["<SKILL>_WALLET"]
SCANNER_NAME = "<scanner_name>"           # matches runtime.yaml
LOCK_NAME = f"<skill>-{WALLET[2:10]}"     # per-wallet → multi-wallet hosts safe

client = SenpiClient()
mcp = tick_cache(client)                  # per-tick TTL memoization

def run_one_tick():
    with scanner_lock(LOCK_NAME):
        ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)
        markets = mcp("leaderboard_get_markets", limit=100)
        # ... gating ...
        if signal_ready:
            client.push_signal(
                address=WALLET, scanner=SCANNER_NAME,
                asset="BTC", direction="LONG",
                score=0.85,
                signal_type="MOMENTUM",
                data={"funding_bps": 18},
            )

if __name__ == "__main__":
    producer_daemon(
        fn=run_one_tick,
        interval_seconds=300,
        name=LOCK_NAME,
        wallet=WALLET,                    # daemon self-terminates if the
        scanner=SCANNER_NAME,             # runtime is deleted OR scanner renamed
    )
```

---

## Recipe: Migrate a legacy producer

Mechanical replacements:

| Old | New |
|---|---|
| `subprocess.run(["mcporter", "call", "senpi", tool, …])` | `client.mcp_call(tool, **kwargs)` |
| Repeated identical `mcporter_call` in one tick | `mcp = tick_cache(client); mcp(tool, **kwargs)` |
| Serial loop of MCP calls | `parallel([lambda: mcp(t1, …), lambda: mcp(t2, …)])` |
| `subprocess.run(["openclaw", "senpi", "external-scanner", "ingest", …])` | `client.push_signal(address=…, scanner=…, asset=…, direction=…, data={…})` |
| Hand-rolled `fcntl.flock(...)` lock | `with scanner_lock(name): …` |
| openclaw cron entry | `producer_daemon(fn=run_one_tick, interval_seconds=N, name=…)` + delete the cron entry |

Verify after migration: `[senpi_helpers]` events appear in stderr.
`mcp_initialized` fires once per process. `daemon_tick_finished status=ok`
fires on interval.

---

## Recipe: Emit a signal

Single:

```python
client.push_signal(
    address=wallet, scanner="my_signals",
    asset="BTC", direction="LONG",
    score=0.85,
    signal_type="MOMENTUM",          # always pass explicitly
    data={"rsi": 75},                # validated against scanner config.fields
)
```

Batch:

```python
client.push_signals([
    {"address": wallet, "scanner": "my_signals", "asset": "BTC", "direction": "LONG", "data": {…}},
    {"address": wallet, "scanner": "my_signals", "asset": "ETH", "direction": "LONG", "data": {…}},
])
```

Helper raises `SenpiClientError` if any item is rejected. The runtime is
**not atomic** — successful items ARE ingested even when the helper raises.
For per-item outcome, push one at a time or catch + inspect the histogram
in the error message.

Full schema: [`references/signal-schema.md`](references/signal-schema.md).

---

## Recipe: Parallel MCP fan-out

`parallel(...)` → `List[Tuple[bool, Any]]`. Each tuple is `(True, value)` or
`(False, exception)`.

```python
# Fail-fast: any failure raises after the fan-out completes
[(_, ch), (_, markets), (_, funding)] = parallel(
    [
        lambda: mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET),
        lambda: mcp("leaderboard_get_markets", limit=100),
        lambda: mcp("market_get_funding_history", coin="BTC", limit=24),
    ],
    raise_after_completion=True,
)

# Per-result handling
results = parallel([
    lambda: mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET),
    lambda: mcp("leaderboard_get_markets", limit=100),
])
ch_ok, ch = results[0]
if not ch_ok:
    log.warning("clearinghouse fetch failed: %s", ch)
```

Concurrency capped at `SENPI_HELPERS_MAX_CONCURRENT` (default 8). Excess
calls **queue**, never reject. Combine with `tick_cache` to dedupe.

---

## Recipe: Per-tick cache

```python
mcp = tick_cache(client)
ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)
# ... later in same tick ...
ch_again = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)  # cache hit
```

TTL: 120s (`SENPI_HELPERS_TICK_CACHE_TTL`). Hard cap: 512 entries
(`SENPI_HELPERS_TICK_CACHE_MAX_ENTRIES`, LRU).

---

## Recipe: Daemon scheduling

```python
producer_daemon(
    fn=run_one_tick,
    interval_seconds=300,
    name=f"<skill>-{wallet_hash}",
    wallet=WALLET,                   # daemon self-terminates if the runtime
    scanner=SCANNER_NAME,            # for this wallet is deleted OR the
                                     # scanner is dropped/renamed.
                                     # Pass alive_check=None to opt out.
)
```

Replaces openclaw cron + `agentTurn`. Daemon stays alive across ticks; tick
failures log and continue. SIGTERM / SIGINT drain gracefully. **Do not use
`openclaw cron add senpi-producer-…`** — that's the legacy fork-storm path
the daemon was written to replace.

After any container restart, relaunch manually: `nohup python3 -u
<producer>.py …` with the skill's required env vars. The daemon records
argv + cwd in `boot.json`; `senpi-helpers restart` handles subsequent
restarts.

---

## Errors → fixes

| Error contains | Cause | Fix |
|---|---|---|
| `signal_post: response body was empty` | Proxy/sidecar stripped body | Check container network, `SENPI_RUNTIME_API_HOST/PORT` |
| `signal_post: response not valid JSON` | Mid-stream truncation | Network instability; retry on next tick |
| `signal_post: unexpected envelope shape` | Runtime is on the legacy 1.x envelope | Bump runtime to `>= 2.0.0` |
| `signal_post: … code=INVALID_REQUEST` | Per-item schema violation. Most common: `asset`/`direction` inside `data` | Move to top-level kwargs; verify `data` keys against `runtime.yaml` `config.fields` |
| `signal_post: … code=NOT_FOUND` | No runtime for wallet, or scanner name unknown | Verify runtime install (`openclaw senpi runtime list`); confirm `scanner` matches `runtime.yaml` |
| `signal_post: HTTP 400 … Exceeded api.maxItemsPerSignalsRequest=10` | Batch too large | Split batch (default cap 10) |
| `signal_post: HTTP 4xx/5xx ENVELOPE_CODE: …` | Runtime envelope error | Read the human message in the exception |
| `MCP error: …` from `mcp_call` | MCP-side tool error | Check tool name + arguments against `senpi-hyperliquid-mcp` schema |
| `urllib.error.URLError` from `mcp_call` | Network / connect / TLS failure | Verify `SENPI_MCP_URL`, `SENPI_AUTH_TOKEN`; retry on next tick |
| `socket.timeout` | Wall-clock timeout exceeded | Override per-call: `client.mcp_call(tool, timeout=60.0, …)` |
| `lock_recovered_after_crash` | Previous holder crashed; auto-recovered | No action — self-healing |

---

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `SENPI_MCP_URL` | `https://mcp.prod.senpi.ai/mcp` | MCP endpoint |
| `SENPI_AUTH_TOKEN` | *(required)* | MCP bearer token |
| `SENPI_RUNTIME_API_HOST` | `127.0.0.1` | Runtime signals host |
| `SENPI_RUNTIME_API_PORT` | `8787` | Runtime signals port |
| `SENPI_HELPERS_MCP_TIMEOUT` | `30.0` | Per-call MCP timeout (s) |
| `SENPI_HELPERS_SIGNAL_TIMEOUT` | `5.0` | Per-call signal POST timeout (s) |
| `SENPI_HELPERS_MAX_CONCURRENT` | `8` | `parallel(...)` concurrency cap |
| `SENPI_HELPERS_QUEUE_WARN_DEPTH` | `50` | Warn when `parallel()` queue hits this depth |
| `SENPI_HELPERS_TICK_CACHE_TTL` | `120.0` | Cache TTL (s) |
| `SENPI_HELPERS_TICK_CACHE_MAX_ENTRIES` | `512` | Cache LRU cap |
| `SENPI_HELPERS_LOCK_DIR` | `/tmp` | Lock file directory |
| `SENPI_HELPERS_STATE_DIR` | `/data/.openclaw/senpi-helpers` | Daemon state files |
| `OPENCLAW_WORKSPACE` | `/data/workspace` | Helpers package location |

---

## Logging

JSON lines to **stderr** prefixed `[senpi_helpers]`. Stdout stays clean.

```
[senpi_helpers] {"ts": …, "event": "lock_acquired",        "name": "pangolin-a919c1e2"}
[senpi_helpers] {"ts": …, "event": "mcp_call",             "tool": "leaderboard_get_markets", "duration_ms": 265, "status": "ok"}
[senpi_helpers] {"ts": …, "event": "cache_hit",            "tool": "strategy_get_clearinghouse_state", "age_s": 0.78}
[senpi_helpers] {"ts": …, "event": "signal_post",          "batch_size": 1, "duration_ms": 12, "status": "ok"}
[senpi_helpers] {"ts": …, "event": "daemon_tick_finished", "tick": 34, "duration_ms": 4011, "status": "ok"}
```

Filter Railway logs by `[senpi_helpers]`.

---

## Operator CLI: `senpi-helpers`

Standalone CLI. Reads `pid.json` / `boot.json` / `heartbeat.json` under
`$SENPI_HELPERS_STATE_DIR`. No openclaw coupling.

```bash
${OPENCLAW_WORKSPACE:-/data/workspace}/skills/_helpers/senpi-helpers <subcommand>
```

| Subcommand | Purpose |
|---|---|
| `list`    | All daemons on this host |
| `health`  | One daemon's health; non-zero exit on degraded |
| `stats`   | Hourly UTC bucket aggregation from the daemon's log (default 72h) |
| `stop`    | SIGTERM, poll, escalate to SIGKILL on timeout |
| `restart` | Stop + re-exec from `boot.json` |

`start` is not a subcommand — first launch goes through the skill's
`nohup python3 -u <producer>.py …` recipe. The CLI takes over after the
daemon writes its `boot.json` on first boot.

Full reference:
[`references/cli-reference.md`](references/cli-reference.md).

---

## Tests

Stdlib `unittest`; no credentials needed.

```bash
cd _helpers/senpi_runtime_helpers
python3 -m unittest discover -s tests -v
```

Coverage: client (HTTP + envelope parsing), lock (PID-aliveness recovery),
cache (TTL + LRU + thundering-herd coalescing), parallel (concurrency cap),
daemon (tick lifecycle + signal handling + state-file writes), state
(round-trip + tolerant writes + sensitive-env scrubbing), manage
(SIGTERM/SIGKILL + relaunch), stats (log parser + hourly buckets), cli
(every subcommand).

---

## See also

- [`references/signal-schema.md`](references/signal-schema.md) — `SignalItem` shape, validation, `data` block conventions.
- [`references/cli-reference.md`](references/cli-reference.md) — `senpi-helpers` exit codes, JSON envelopes, recipes.
- [`pangolin/scripts/pangolin-producer.py`](../../pangolin/scripts/pangolin-producer.py) — reference producer.
- [`senpi-trading-runtime/SKILL.md`](../../senpi-trading-runtime/SKILL.md) — the runtime that consumes these signals.
