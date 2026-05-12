# Python Producer SDK reference

The runtime ships `senpi_runtime_helpers`, a stdlib-only Python package for writing external-scanner producers. It is the canonical client for the runtime's `/signals` endpoint and replaces the legacy `mcporter` subprocess and `openclaw senpi external-scanner ingest` CLI patterns.

The lede + rules live in [SKILL.md → Python Producer SDK](../SKILL.md#python-producer-sdk). This reference is the depth: import shim, full skeleton, batch + parallel recipes, errors, operator CLI, env var table.

---

## Import shim

The SDK ships inside this skill. Where the skill lives on disk depends on the host:

- Global install (`skills add … --skill senpi-trading-runtime -g`) lands the runtime skill under `~/.openclaw/skills/` — e.g. `/data/.openclaw/skills/senpi-trading-runtime/` on Railway.
- Some setups put user skills under `${OPENCLAW_WORKSPACE}/skills/` instead.

Probe both, then add whichever holds the package to `sys.path`:

```python
import os, sys
from pathlib import Path

_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next(
    (p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()),
    _sdk_candidates[0],
)
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

from senpi_runtime_helpers import (
    SenpiClient, SenpiClientError,
    scanner_lock, tick_cache, parallel, producer_daemon,
)
```

`SenpiClient()` reads `SENPI_MCP_URL`, `SENPI_AUTH_TOKEN`, `SENPI_RUNTIME_API_HOST`, `SENPI_RUNTIME_API_PORT` from env.

---

## New producer skeleton

```python
# scripts/<skill>-producer.py
import os, sys
from pathlib import Path

_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next(
    (p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()),
    _sdk_candidates[0],
)
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

from senpi_runtime_helpers import (
    SenpiClient, scanner_lock, tick_cache, producer_daemon,
)

WALLET = os.environ["<SKILL>_WALLET"]
SCANNER_NAME = "<scanner_name>"           # matches runtime.yaml
LOCK_NAME = f"<skill>-{WALLET[2:10]}"     # per-wallet — multi-wallet host safe

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

The daemon stays alive across ticks; tick failures log and continue; SIGTERM / SIGINT drain gracefully. After any container restart relaunch manually: `nohup python3 -u <producer>.py …` with the skill's required env vars. The daemon records argv + cwd in `boot.json`; `senpi-helpers restart` handles subsequent restarts.

---

## Batch and parallel

Batch ingest — runtime is not atomic; successful items are ingested even on partial failure:

```python
client.push_signals([
    {"address": w, "scanner": "my_signals", "asset": "BTC", "direction": "LONG", "data": {...}},
    {"address": w, "scanner": "my_signals", "asset": "ETH", "direction": "LONG", "data": {...}},
])
```

Parallel MCP fan-out — concurrency-capped at `SENPI_HELPERS_MAX_CONCURRENT` (default 8); excess calls queue, never reject:

```python
results = parallel([
    lambda: mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET),
    lambda: mcp("leaderboard_get_markets", limit=100),
])
ch_ok, ch = results[0]
```

---

## Errors → fixes (most common)

| Error contains | Fix |
|---|---|
| `signal_post: … code=INVALID_REQUEST` | Most common: `asset`/`direction` inside `data`. Move to top-level kwargs; verify `data` keys against `runtime.yaml` `config.fields`. |
| `signal_post: … code=NOT_FOUND` | Verify runtime install (`openclaw senpi runtime list`); confirm `scanner` matches `runtime.yaml`. |
| `signal_post: unexpected envelope shape` | Runtime is on the legacy envelope; bump the `runtime` plugin to `>= 1.1.0`. |
| `signal_post: HTTP 400 … Exceeded api.maxItemsPerSignalsRequest=10` | Batch too large; split batch (default cap 10). |
| `MCP error: …` from `mcp_call` | Check tool name + arguments against `senpi-hyperliquid-mcp` schema. |
| `lock_recovered_after_crash` | Previous holder crashed; auto-recovered — no action needed. |

Full per-item error codes and validation rules: [`signal-schema.md`](signal-schema.md).

---

## Operator CLI: `senpi-helpers`

Bundled with the SDK. Reads `pid.json` / `boot.json` / `heartbeat.json` under `$SENPI_HELPERS_STATE_DIR`; sends signals to control running daemons.

```bash
~/.openclaw/skills/senpi-trading-runtime/senpi-helpers <subcommand>
```

| Subcommand | Purpose |
|---|---|
| `list`    | All daemons on this host |
| `health`  | One daemon's health; non-zero exit on degraded |
| `stats`   | Hourly UTC bucket aggregation from the daemon's log (default 72h) |
| `stop`    | SIGTERM, poll, escalate to SIGKILL on timeout |
| `restart` | Stop + re-exec from `boot.json` |

`start` is not a subcommand — first launch goes through the `nohup python3 -u <producer>.py …` recipe above. Full subcommand reference, exit codes, JSON envelopes: [`senpi-helpers-cli.md`](senpi-helpers-cli.md).

---

## Logging

JSON lines to **stderr** prefixed `[senpi_helpers]`. Stdout stays clean. Filter logs by `[senpi_helpers]`.

---

## SDK-tuning environment variables

These knobs default sensibly for every host shipped today. Override only with a measured reason.

| Variable | Default | Purpose |
|----------|---------|---------|
| `SENPI_HELPERS_STATE_DIR` | `/data/.openclaw/senpi-helpers` | Where the daemon writes `pid.json` / `boot.json` / `heartbeat.json`. |
| `SENPI_HELPERS_MCP_TIMEOUT` | `30.0` | Per-call MCP timeout in seconds. |
| `SENPI_HELPERS_SIGNAL_TIMEOUT` | `5.0` | Per-call `/signals` POST timeout in seconds. |
| `SENPI_HELPERS_MAX_CONCURRENT` | `8` | `parallel(...)` concurrency cap. Excess calls queue, never reject. |
| `SENPI_HELPERS_QUEUE_WARN_DEPTH` | `50` | Warn when `parallel(...)` queue hits this depth (soft, no rejection). |
| `SENPI_HELPERS_TICK_CACHE_TTL` | `120.0` | Per-tick MCP cache TTL in seconds. |
| `SENPI_HELPERS_TICK_CACHE_MAX_ENTRIES` | `512` | Tick cache hard cap (LRU eviction). |
| `SENPI_HELPERS_LOCK_DIR` | `/tmp` | Lock file directory used by `scanner_lock`. |
