# Migration cookbook — legacy producer → wrapper

Step-by-step migration of an existing producer that still uses
`subprocess.run(["mcporter", …])` and / or
`subprocess.run(["openclaw", "senpi", "external-scanner", "ingest", …])`.

For new producers, read [`../SKILL.md`](../SKILL.md) § "Recipe: New producer"
instead — this doc is for the case where you already have a working
mcporter-based producer.

The reference migration is `pangolin/scripts/pangolin-producer.py` +
`pangolin/scripts/pangolin_config.py` — copy that pattern.

---

## Step 1 — drop the import shim into `<skill>/scripts/<skill>_config.py`

Most legacy producers have a `<skill>_config.py` that exports an
`mcporter_call(tool, **params)` function. Add this near the top:

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

_wrapper_client = SenpiClient()                 # reads SENPI_MCP_URL + SENPI_AUTH_TOKEN from env
_cached_mcp = tick_cache(_wrapper_client)       # per-tick TTL memoization
```

`SenpiClient()` is process-wide. One instance is fine for one producer.

---

## Step 2 — replace `mcporter_call` with `_cached_mcp`

**Before** (legacy):
```python
def mcporter_call(tool, **params):
    proc = subprocess.run(
        ["mcporter", "call", "senpi", tool, "--params", json.dumps(params)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
```

**After** (wrapper, drop-in for callers that read the unwrapped JSON):
```python
def mcporter_call(tool, **params):
    """Backward-compat shim — delegates to wrapper.

    Returns None on failure to preserve the legacy contract used by
    callers throughout this skill. New code should call _cached_mcp
    directly so exceptions are not swallowed.
    """
    try:
        return _cached_mcp(tool, **params)
    except (SenpiClientError, OSError) as e:
        log.warning("mcp_call %s failed: %s", tool, e)
        return None
```

If the producer's call sites can adopt exceptions, prefer:

```python
result = _cached_mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)
```

— direct, no shim. `SenpiClientError` propagates to `producer_daemon`'s
per-tick error handler, which logs and continues.

---

## Step 3 — replace the `external-scanner ingest` block

**Before** (legacy):
```python
def push_signal(payload):
    proc = subprocess.run(
        [
            "openclaw", "senpi", "external-scanner", "ingest",
            "--address", WALLET,
            "--scanner", SCANNER_NAME,
            "--payload", json.dumps(payload),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ingest failed: {proc.stderr}")
```

**After** (wrapper):
```python
def push_signal(payload):
    """Push a signal payload via the wrapper.

    Routing fields go top-level. Scanner-specific fields go in `data`.
    Helper raises SenpiClientError on per-item rejection — the daemon's
    per-tick error handler catches and continues.
    """
    _wrapper_client.push_signal(
        address=WALLET,
        scanner=SCANNER_NAME,
        asset=payload.get("asset"),
        direction=payload.get("direction"),
        score=payload.get("score"),
        data=payload.get("data") or {},
    )
```

**Critical reminder** (from the Pangolin tick-2 incident, 2026-05-05):
keep `asset` and `direction` **out** of `data`. They are top-level routing
fields. See [`signal-schema.md`](signal-schema.md).

For batch emission in a single round-trip, use `_wrapper_client.push_signals([…])`.

---

## Step 4 — replace cron with `producer_daemon`

**Before** (legacy openclaw cron entry):
```yaml
# in <skill>/config/<skill>-config.json or runtime YAML
cron:
  expression: "*/5 * * * *"
  agentTurn:
    prompt: "Run the <skill> producer."
    tools: ["exec"]
```

The cron paid for a full LLM inference whose only job was to invoke
`exec("python <skill>-producer.py")`. Skip the LLM entirely.

**After** (wrapper):
```python
# at the bottom of <skill>/scripts/<skill>-producer.py
if __name__ == "__main__":
    producer_daemon(
        fn=run_one_tick,                     # the per-tick callable
        interval_seconds=300,                # match the legacy cron interval
        name=f"<skill>-{WALLET[2:10]}",     # used by scanner_lock + log fields
    )
```

In the openclaw container, replace the cron entry with a long-running
process supervisor. Common patterns: `tini`-managed
`python -u scripts/<skill>-producer.py`, or a supervisord entry, or
`docker-compose` `command:` directly.

---

## Step 5 — drop hand-rolled `fcntl` locks; use `scanner_lock`

Legacy producers often had something like:

```python
LOCK_PATH = "/tmp/<skill>.lock"
lock_fd = open(LOCK_PATH, "w")
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(0)
```

Replace with:

```python
def run_one_tick():
    with scanner_lock(f"<skill>-{WALLET[2:10]}"):
        ...
```

`scanner_lock` adds **stale-PID recovery**: if a prior tick crashed before
releasing the lock, the next tick checks the holder's PID via `os.kill(pid, 0)`
and recovers if dead. Hand-rolled `fcntl` doesn't do this — a single hard
crash bricks the producer until the lock file is manually deleted.

**Lock-name convention:** include a wallet identifier when the host runs
multiple wallets — the lock file is `${SENPI_HELPERS_LOCK_DIR}/senpi-<name>.lock`
and a per-wallet name keeps multi-wallet hosts from serialising on a
single skill-wide lock.

---

## Step 6 — verify

After deploying, tail the producer's stderr in Railway logs:

```
[senpi_helpers] {…, "event": "lock_acquired",        "name": "<skill>-<wallet_hash>"}
[senpi_helpers] {…, "event": "mcp_call",             "tool": "leaderboard_get_markets", "duration_ms": 280, "status": "ok"}
[senpi_helpers] {…, "event": "cache_hit",            "tool": "strategy_get_clearinghouse_state", "age_s": 0.78}
[senpi_helpers] {…, "event": "signal_post",          "batch_size": 1, "duration_ms": 12, "status": "ok"}
[senpi_helpers] {…, "event": "lock_released",        "name": "<skill>-<wallet_hash>"}
[senpi_helpers] {…, "event": "daemon_tick_finished", "tick": 1, "duration_ms": 4011, "status": "ok"}
```

Pre/post sanity checks:
- Tick wall-clock should drop ~10× (e.g. 30–60 s → 4 s for 9-MCP-call producers).
- Signal-post latency (`signal_post.duration_ms`) should be < 50 ms.
- Plugin re-registrations in gateway logs should drop to ~0/hour (was 605
  in 3.5 days under the legacy stack).
- No more 5–8 s gaps before each signal lands.

---

## Step 7 — clean up

After the wrapper is in production and stable for ≥ 24 h:

- Delete the `mcporter_call` shim (callers either use `_cached_mcp` directly
  or were updated to handle exceptions).
- Delete the `subprocess.run(["openclaw", "senpi", "external-scanner", …])`
  block entirely.
- Remove the legacy `<skill>_signal_lock` file from `/tmp` if hand-rolled
  fcntl was used.
- Remove the cron entry from `<skill>/config/<skill>-config.json` (or wherever
  it lived).

The reference fully-cleaned producer:
[`pangolin/scripts/pangolin-producer.py`](../../../pangolin/scripts/pangolin-producer.py).

---

## Common migration mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Putting `asset` / `direction` in `data` | `INVALID_REQUEST` rejection on every signal | Move to top-level kwargs of `push_signal` |
| Forgetting to wrap with `scanner_lock` | Concurrent ticks duplicate work / fight for state files | Wrap `run_one_tick` body |
| Calling `producer_daemon` from inside `run_one_tick` | Recursive daemon | `producer_daemon` is the entry point — it CALLS `fn=run_one_tick`, not the other way around |
| Using `mcporter_call` shim that swallows exceptions and a caller that doesn't check `None` | Silent failures, missing signals | Migrate the caller to direct `_cached_mcp` + `try/except SenpiClientError` |
| Leaving the openclaw cron entry alongside the daemon | Two producers contending for the same lock | Delete the cron entry once daemon is verified |
| Different `LOCK_NAME` between scanner_lock and producer_daemon | Both take separate locks; concurrency safety partially broken | Pass the same `name=` to both |
| Hardcoding `https://mcp.prod.senpi.ai/mcp` instead of reading env | Test boxes hit prod; prod boxes can't be redirected | Let `SenpiClient()` read `SENPI_MCP_URL` from env |
