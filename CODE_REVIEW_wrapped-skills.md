# Code Review — `wrapped-skills` branch

Reviewer: Claude (Opus 4.7, 1M context)
Branch: `wrapped-skills` vs `main`
Scope:
- `_helpers/senpi_runtime_helpers/` (new package, 1551 lines)
- `pangolin/scripts/pangolin-producer.py` (integration)
- `pangolin/scripts/pangolin_config.py` (integration)

Test status: `python3 -m unittest discover -s tests -v` → 23/23 pass on Python 3.14.

---

## Outline

1. **Critical** — Lock correctness in `_helpers/senpi_runtime_helpers/lock.py`
   - Stale-recovery `unlink → open` race lets two holders run concurrently
   - Alive-but-stale check uses `OR` — steals the lock from a slow-but-alive holder
2. **High** — Producer behavioral regressions in `pangolin/scripts/`
   - `mcporter_call` swallowed-error → exception change kills callers' `if not r:` fallback paths
   - `scanner_lock("pangolin-producer")` is global → collides across wallets
   - `tick_timeout=120` makes the existing `WARN_OVER_300S` warn unreachable
   - `push_signal` no longer returns `False` on failure — semantics quietly changed
3. **High** — `SenpiClient` thread-safety and init-failure handling
4. **Medium** — Config / docs drift
5. **Medium** — Dead code / unused symbols
6. **Medium** — `cache.py` correctness and growth
7. **Low** — Misc

---

## Section 1 — Critical lock correctness bugs

Both bugs are in [_helpers/senpi_runtime_helpers/lock.py](_helpers/senpi_runtime_helpers/lock.py). Either one alone breaks the "exclusive lock" guarantee and lets two producers run concurrently — which the producer's own comment says causes duplicate signal emission ([pangolin-producer.py:154-161](pangolin/scripts/pangolin-producer.py#L154-L161)).

### 1a. `unlink → open` race on stale recovery

[lock.py:108-121](_helpers/senpi_runtime_helpers/lock.py#L108-L121):

```python
if not alive or age > timeout:
    log_event("lock_stale_recovered", ...)
    try:
        path.unlink()         # ← line 117
    except FileNotFoundError:
        pass

fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)   # ← line 121
```

Scenario:
- Process **A** legitimately holds `flock` on inode `I_old`.
- A's mtime exceeds `LOCK_HEARTBEAT_TIMEOUT` (no heartbeat refresh exists — see 1b).
- Process **B** sees stale → `path.unlink()` removes the directory entry but `I_old` lives on through A's open fd.
- Process **B** calls `os.open(O_CREAT)` → creates a brand-new inode `I_new` and `flock`s it. **Succeeds**.
- A's `flock` on `I_old` is still held — A keeps running.
- **Both A and B now hold "the lock" on different inodes, on the same path.**

**Fix:** never `unlink` on stale recovery. Instead, open the existing file and try `flock(LOCK_EX | LOCK_NB)`. If it fails, the holder is genuinely alive (whatever the metadata says); back off. If it succeeds, the previous holder was a corpse — overwrite metadata in place. The whole point of `_write_metadata_inplace` ([lock.py:56-73](_helpers/senpi_runtime_helpers/lock.py#L56-L73)) is to keep the inode stable — but stale recovery breaks exactly that invariant.

### 1b. Alive-but-stale lock gets stolen

[lock.py:108](_helpers/senpi_runtime_helpers/lock.py#L108):

```python
if not alive or age > timeout:
    # treat as stale, recover
```

The `OR` is wrong. The code never refreshes mtime during a long-running tick (no heartbeat thread). Acquisition writes metadata once, then `yield`s. If the producer's tick runs longer than `LOCK_HEARTBEAT_TIMEOUT` (default 300 s), `age > timeout` becomes true while `alive` is also true.

Pangolin's tick is allowed to run up to 300 s (per its own `WARN_OVER_300S` flag in [pangolin-producer.py:885](pangolin/scripts/pangolin-producer.py#L885)), and the daemon's `tick_timeout=120` is itself best-effort on non-UNIX. So this is reachable in practice.

**Two fixes possible, pick one:**
- Tighten the predicate to `not alive AND age > timeout` (requires PID-based liveness alone to be reliable, which `os.kill(pid, 0)` is — `_process_alive` already handles `ESRCH` and `EPERM` correctly).
- Spawn a heartbeat thread that `os.utime(path, None)` every `timeout/3` seconds while the lock is held. The README *promises* this ("PID + heartbeat-mtime liveness check") but the implementation has no heartbeat — only the PID check is real.

### Combined effect

1a turns 1b into a duplicate-emission incident: the slow-but-alive holder gets its lock stolen, the new holder unlinks and re-creates the file, both run, both emit signals for the same candidate, both bump the daily `entries` counter. The legacy `acquire_lock()` in [pangolin-producer.py:163](pangolin/scripts/pangolin-producer.py#L163) was simpler precisely because it had none of this — and it's still there, doing the actual deduplication work. Worth asking the reviewer whether you should just keep the legacy lock and remove `scanner_lock` entirely from the daemon, or fix the helper.

### Test gap

[test_lock.py:38-53](_helpers/senpi_runtime_helpers/tests/test_lock.py#L38-L53) covers "stale + dead PID". There is no test for "stale mtime + alive PID" (1b), and no test for the recovery race (1a, would need two child processes). Both need to land before this can claim correctness.

---

## Section 2 — Producer behavioral regressions in `pangolin/scripts/`

The wrapper integration looks small (~50 lines changed), but it silently changes contracts that the rest of the producer depends on. None of these are caught by the helper unit tests — they only show up in the live producer.

### 2a. `mcporter_call` no longer returns `None` on failure — callers' fallback paths are dead

[pangolin_config.py:89-96](pangolin/scripts/pangolin_config.py#L89-L96):

```python
def mcporter_call(tool, retries=2, timeout=25, **params):
    """... If the wrapper raises, we let it propagate
    (we are testing the wrapper; silencing errors defeats the point)."""
    return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
```

**Old contract:** subprocess failure / timeout / JSON parse error → return `None`. Producer callers handle that with `if not r:` early-returns.

**New contract:** any failure → raise `urllib.error.HTTPError` / `URLError` / `SenpiClientError`. The function never returns `None` on failure anymore.

Callers that depend on the old contract:

| Site | Fallback | Status under new contract |
|---|---|---|
| [pangolin-producer.py:391](pangolin/scripts/pangolin-producer.py#L391) `if not ch:` in `get_account_value` | `return None, None, set()` | **Dead.** Exception bubbles out of `get_account_value`, no fallback fires. |
| [pangolin-producer.py:482](pangolin/scripts/pangolin-producer.py#L482) `if not raw:` in `scan_funding_extremes` | `return [], None` | **Dead.** Exception bubbles out, no candidates returned, no graceful skip. |
| [pangolin-producer.py:421](pangolin/scripts/pangolin-producer.py#L421) `if not r:` in `get_funding_regime` | `return None` | **Saved by the surrounding `try/except Exception: return None`.** OK by accident. |
| [pangolin-producer.py:436](pangolin/scripts/pangolin-producer.py#L436) `if not r:` in `get_funding_history` | `return None` | **Saved by the surrounding `try/except Exception: return None`.** OK by accident. |
| [pangolin-producer.py:494](pangolin/scripts/pangolin-producer.py#L494) `if sm_raw:` in `scan_funding_extremes` | continue with empty SM map | **Dead.** Exception abandons the whole tick. |

The author's stated intent ("we are testing the wrapper; silencing errors defeats the point") is reasonable for a *short* canary period. But the existing callers were written with `None` in mind, and they each had a reason to degrade gracefully — e.g. "SM map unavailable → fall back to empty map and continue scoring". The new behavior abandons the whole tick on any one failure.

A 20-year reviewer will ask: was each of these graceful-fallback paths re-evaluated, or were they all replaced with "abort the tick" by accident? Right now the answer is "by accident".

**Pick one explicitly:**
- Keep the abort-on-failure behavior, then *delete* the `if not r:` branches at all five call sites. Dead code that looks live is worse than no code.
- Restore the old contract: catch the wrapper exceptions inside `mcporter_call` and return `None`. Update the docstring to match. The callers stay correct.
- Make the choice per-site (some calls are "must succeed", some are "nice to have").

### 2b. `scanner_lock("pangolin-producer")` is global — multi-wallet collision

[pangolin-producer.py:929](pangolin/scripts/pangolin-producer.py#L929):

```python
producer_daemon(
    fn=_run_main_safely,
    interval_seconds=300,
    name="pangolin-producer",     # ← lock name does not include the wallet
    tick_timeout=120,
)
```

Inside [daemon.py:139](_helpers/senpi_runtime_helpers/daemon.py#L139), every tick takes `scanner_lock(name)` → `/tmp/senpi-pangolin-producer.lock`.

If the same host runs Pangolin for two wallets (two daemons, two `PANGOLIN_WALLET` values, two state dirs), they share **one** scanner lock. Whichever daemon ticks second gets `BlockingIOError` and emits `tick_status="skipped_locked"` forever.

The legacy [acquire_lock()](pangolin/scripts/pangolin-producer.py#L163) used `_STATE_DIR / "producer.lock"`, where `_STATE_DIR = SKILL_DIR / "state" / <wallet-hash>`. So per-wallet by construction. The new layer **regresses** that.

**Fix:** include the wallet in the lock name. Either:
```python
name=f"pangolin-producer-{_wallet_hash()}"
```
or pass `lock_dir=str(_STATE_DIR)` so the lock file lives next to the legacy one. Same effect — different daemons, different locks.

This is the kind of thing that won't bite you in dev (one wallet) and will bite you in prod the day someone deploys a second wallet.

### 2c. Two layers of locking — the daemon lock is redundant or wrong

The daemon now wraps each tick in `scanner_lock(...)`, **and** `main()` still calls the legacy `acquire_lock()` ([pangolin-producer.py:761](pangolin/scripts/pangolin-producer.py#L761)) inside the locked region. Two consequences:

1. The legacy lock can never fail in single-wallet single-daemon deployments — `scanner_lock` already serialized the tick. So the `"status": "skip", "reason": "previous run still active — cron reentrancy guard"` branch is unreachable from the daemon path.
2. In the multi-wallet case (see 2b), the legacy lock is the **only** thing that would have been correct, but the daemon's global `scanner_lock` blocks before the legacy lock even runs.

**Pick one:** keep the legacy per-wallet lock and drop `scanner_lock` from the daemon (or pass `name=…wallet…`); or rip out `acquire_lock` / `release_lock` entirely and rely on the (fixed — see Section 1) helper. Don't keep both.

### 2d. `tick_timeout=120` makes `WARN_OVER_300S` unreachable

[pangolin-producer.py:885](pangolin/scripts/pangolin-producer.py#L885):

```python
warn = "WARN_OVER_300S" if elapsed > 300 else None
```

[pangolin-producer.py:931](pangolin/scripts/pangolin-producer.py#L931):

```python
tick_timeout=120,       # 2 min per tick
```

On UNIX, SIGALRM kills the tick at 120 s, so `elapsed > 300` is unreachable. The warn flag is dead code on the only platform Pangolin runs on. Worse, the producer's own status output now claims `"warn": null` even when the tick was killed mid-run — the timeout aborts before this output statement executes.

**Pick one:**
- Raise `tick_timeout` to `300+` so the warn threshold has meaning, and let the daemon's per-tick alarm match the producer's own stated budget.
- Lower the warn threshold to e.g. `100` so it actually fires before the timeout, and adjust the constant name (`WARN_OVER_100S`).
- Delete the dead `WARN_OVER_300S` line.

Whichever you pick, the *two numbers must agree on what "too slow" means*. Right now they disagree by 2.5×.

### 2e. `push_signal` no longer returns `False` — semantics change is undocumented at the call site

[pangolin-producer.py:737-741](pangolin/scripts/pangolin-producer.py#L737-L741):

```python
cfg._wrapper_client.push_signal(
    address=PANGOLIN_WALLET,
    scanner=SCANNER_NAME,
    data=payload,
)
return True
```

The function now either returns `True` or raises. The single caller at [pangolin-producer.py:877](pangolin/scripts/pangolin-producer.py#L877) does:

```python
if push_signal(payload):
    pushed += 1
    mark_asset_emitted(c["token"])
    tc["entries"] = tc.get("entries", 0) + 1
    save_trade_counter(tc)
```

Under the old contract, a failed push left `pushed`/`entries` un-incremented, and `mark_asset_emitted` was not called → the candidate stayed un-cooled-down and was eligible to retry next tick. Under the new contract, an exception aborts the tick before any state mutation — same end state, *only because there is exactly one candidate per tick* (`for c in eligible[:1]`). If that ever changes to N candidates per tick, an exception on candidate #2 will leave candidate #1's state half-written (`mark_asset_emitted` called, `save_trade_counter` not called, depending on order). Atomicity assumption is now load-bearing.

**Fix:** drop the `if push_signal(payload):` wrapper since `push_signal` cannot return `False` anymore — it's misleading. Either:
```python
push_signal(payload)
pushed += 1
mark_asset_emitted(c["token"])
tc["entries"] = tc.get("entries", 0) + 1
save_trade_counter(tc)
```
or keep the bool contract by catching wrapper exceptions inside `push_signal` and returning `False`. The current "always returns True" is a lie that costs you nothing today and will cost you the day someone loops over multiple candidates.

### 2f. `_wrapper_client = SenpiClient()` runs at **import time**

[pangolin_config.py:51](pangolin/scripts/pangolin_config.py#L51):

```python
_wrapper_client = SenpiClient()
log_event("pangolin_wrapper_enabled", helpers_path=_helpers_path)
```

Importing `pangolin_config` now has two side effects: instantiate a network client, and emit a log line. Two issues:

- The module docstring claims "Import is hard-required — if it fails, the deployment is broken and we want to scream." But `SenpiClient()` does not validate `SENPI_AUTH_TOKEN` — a missing token sets `auth_token=""` and import succeeds silently. The first MCP call gets a 401. So the import-time scream the docstring promises does not exist.
- Any tooling that imports `pangolin_config` (tests, linters, REPL, an `import` from another module just to use `now_iso()`) now eagerly creates a client and writes to stderr. That's surprising.

**Fix:** move client creation behind a lazy accessor (`_get_client()` cached with `functools.lru_cache(1)`), and either fail fast on missing `SENPI_AUTH_TOKEN` or document explicitly that the first call is where auth surfaces.

---

## Section 3 — `SenpiClient` thread-safety and init-failure handling

`SenpiClient` is documented as "process-wide" ([client.py:113](_helpers/senpi_runtime_helpers/client.py#L113)) and is used from multiple threads via `parallel(...)` ([parallel.py:71](_helpers/senpi_runtime_helpers/parallel.py#L71)). The implementation does not actually handle multi-threaded use, and the recovery path on a half-initialized session is wrong.

### 3a. `_initialize_if_needed` is not thread-safe — duplicate `initialize` POSTs

[client.py:137-180](_helpers/senpi_runtime_helpers/client.py#L137-L180):

```python
def _initialize_if_needed(self, timeout: float) -> None:
    if self._session.initialized:
        return
    body = {... "id": self._session.alloc_id(), ...}
    ...
    with _post_json(self.mcp_url, body, self._mcp_headers(), timeout) as resp:
        sid = resp.headers.get("Mcp-Session-Id") ...
        if sid:
            self._session.session_id = sid
    ...
    self._session.initialized = True
```

The `initialized` check, the session-id write, and the `initialized = True` write are all unguarded. Run this from `parallel([...])` with 8 workers and a freshly constructed client:

- Threads T1..T8 each see `self._session.initialized == False`.
- T1..T8 each POST `initialize`. Server gets 8 init calls, returns 8 distinct `Mcp-Session-Id` headers.
- Each thread overwrites `self._session.session_id` with its own value as the writes interleave.
- Each thread POSTs `notifications/initialized` against *whichever session id was current at the moment its `_mcp_headers()` was called* — which may not be the one the server expects.
- T1..T8 each call `tools/call` against `self._session.session_id`. All but one of those sessions is orphaned on the server.

The server may tolerate this (most MCP servers do), but on a strict server you'll see intermittent 4xx and a stream of `[senpi_helpers] mcp_init_http_error` events whose root cause is unobvious.

**Fix:** wrap the body of `_initialize_if_needed` in a `threading.Lock`, with a double-checked `if self._session.initialized` *inside* the lock:

```python
self._init_lock = threading.Lock()
...
def _initialize_if_needed(self, timeout: float) -> None:
    if self._session.initialized:
        return
    with self._init_lock:
        if self._session.initialized:
            return
        ...  # existing body
        self._session.initialized = True
```

### 3b. `_MCPSession.alloc_id` is not atomic across threads

[client.py:40-42](_helpers/senpi_runtime_helpers/client.py#L40-L42):

```python
def alloc_id(self) -> int:
    self.next_id += 1
    return self.next_id
```

`x += 1` is not atomic in CPython — it's a load, an add, and a store. Two threads can both read `42`, both write `43`. JSON-RPC requires unique request IDs.

In practice today the consequences are minor: the client doesn't correlate responses to IDs (it just reads whatever comes back from the single in-flight `urlopen`), so duplicate IDs aren't observed. But the contract is still wrong, and the day someone adds proper ID correlation (or pipelines requests over keep-alive) it becomes a real bug.

**Fix:** `itertools.count()` is atomic enough for this and removes the field entirely:

```python
self._id_counter = itertools.count(1)
def alloc_id(self) -> int:
    return next(self._id_counter)
```

### 3c. Init-notification failure leaves a poisoned session id

[client.py:151-179](_helpers/senpi_runtime_helpers/client.py#L151-L179):

```python
try:
    with _post_json(self.mcp_url, body, ..., timeout) as resp:
        sid = resp.headers.get("Mcp-Session-Id") or ...
        if sid:
            self._session.session_id = sid     # ← side effect already taken
        _ = _read_response_body(resp)
    # Streamable-HTTP requires a `notifications/initialized` after init.
    note = {...}
    with _post_json(self.mcp_url, note, self._mcp_headers(), timeout) as resp:
        _ = resp.read()                         # ← if THIS raises, we're poisoned
except urllib.error.HTTPError as e:
    ...
    raise
...
self._session.initialized = True
```

If the `initialize` POST succeeds (so `self._session.session_id` gets set) but the `notifications/initialized` POST fails (network blip, server 5xx during the second hop), the function raises — `self._session.initialized` stays `False`, but `self._session.session_id` is already set to a session the server never actually heard us confirm.

On the next call, `_mcp_headers()` ([client.py:131-135](_helpers/senpi_runtime_helpers/client.py#L131-L135)) attaches that stale `Mcp-Session-Id`. The server either rejects the new `initialize` because the header refers to a half-baked session, or it adopts a session it never finished negotiating — server-dependent, and either way subtle.

**Fix:** treat init as all-or-nothing. If `notifications/initialized` fails, clear the partial state before re-raising:

```python
try:
    ...initialize...
    sid = resp.headers.get(...)
    ...notifications/initialized...
except (urllib.error.HTTPError, urllib.error.URLError):
    self._session.session_id = None
    self._session.initialized = False
    raise
self._session.session_id = sid
self._session.initialized = True
```

i.e. only commit the session_id once the *whole* handshake has succeeded.

### 3d. No connection reuse — every call pays a fresh TLS handshake

The whole motivation for this package is the cost of subprocess transport (250-300 MB transient RSS, 2.5-5 s per call — per [README.md:27](_helpers/senpi_runtime_helpers/README.md#L27)). Direct HTTPS removes that. But every `_post_json` call does:

```python
return urllib.request.urlopen(req, timeout=timeout)
```

`urlopen` opens a fresh TCP connection and a fresh TLS handshake every single call. For `mcp.prod.senpi.ai` over the public internet, that's 100-250 ms of unavoidable RTT × handshake-roundtrips, multiplied by every `mcp_call`. On a typical Pangolin tick that's:

- 1× `strategy_get_clearinghouse_state`
- 1× `market_get_funding_regime`
- 1× `market_list_instruments`
- 1× `leaderboard_get_markets`
- N× `market_get_funding_history` (one per candidate)

That's 4 + N handshakes per tick. Easy to spend 1–3 s per tick on TLS alone.

**Fix:** use `http.client.HTTPSConnection` (stdlib, keep-alive supported) or maintain one connection per (host, scheme) on the client and reuse it. Sketch:

```python
import http.client
from urllib.parse import urlsplit
class SenpiClient:
    def __init__(self, ...):
        u = urlsplit(self.mcp_url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        self._conn = cls(u.hostname, u.port or (443 if u.scheme == "https" else 80))
        self._conn_lock = threading.Lock()
```

Then `_post_json` does `request("POST", path, body, headers)` + `getresponse()` while holding `_conn_lock`. (The lock serializes requests across threads — fine because keep-alive is one-at-a-time anyway. For real parallelism, a small connection pool keyed by host.) Stays stdlib-only, matches the package's design constraints, gives you a 5-10× speedup on subsequent calls within a tick.

### 3e. Generic `except Exception` re-logs and re-raises — masks `SenpiClientError` separately

[client.py:228-237](_helpers/senpi_runtime_helpers/client.py#L228-L237):

```python
except Exception as e:
    duration_ms = int((time.time() - started) * 1000)
    log_event(
        "mcp_call",
        tool=tool,
        duration_ms=duration_ms,
        status="exception",
        error=str(e),
    )
    raise
```

`_unwrap_tool_result` raises `SenpiClientError("MCP error: ...")` ([client.py:97-98](_helpers/senpi_runtime_helpers/client.py#L97-L98)) when the JSON-RPC envelope contains an `error` field. That falls into this generic `except Exception` clause and gets logged with `status="exception"`. A senior reviewer will want a distinct status — `"protocol_error"` or `"server_error"` — because mixing transport-layer exceptions with server-reported tool errors makes log filtering useless.

Cheap fix: add an explicit `except SenpiClientError` clause above the generic `except Exception`, with `status="server_error"`.

### 3f. Header lookup is redundantly case-cased

[client.py:153](_helpers/senpi_runtime_helpers/client.py#L153):

```python
sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
```

`resp.headers` is an `email.message.Message` (via `http.client.HTTPResponse.msg`), and its `__getitem__` is case-insensitive in CPython. The second `.get(...)` is dead. Drop it. Trivial, but reviewers notice — it suggests the author wasn't sure how the type works.

---

## Section 4 — Config / docs drift

The package treats env-var configuration as the public surface, so doc/code drift here is a contract bug, not a comment bug.

### 4a. README's `SENPI_RUNTIME_API_PORT` default is wrong

[README.md:49](_helpers/senpi_runtime_helpers/README.md#L49):

```
| `SENPI_RUNTIME_API_PORT` | `18790` | Runtime API port (signals) |
```

[_config.py:40](_helpers/senpi_runtime_helpers/_config.py#L40):

```python
RUNTIME_API_PORT = _env_int("SENPI_RUNTIME_API_PORT", 8787)
```

The default in code is `8787`, the default in README is `18790`. The git log shows commit `33f0346 fix(_helpers): default SENPI_RUNTIME_API_PORT to 8787 (runtime-api default)` — the code was deliberately moved to `8787` but the README was not updated.

This will silently mis-route signals on any host that relies on the default. Operators who read the README and don't set the env var get connection refused on `18790`; operators who read the code get the actual default. The two sources of truth disagree, and the README is wrong.

**Fix:** update [README.md:49](_helpers/senpi_runtime_helpers/README.md#L49) to `8787`. Better, source the table from `_config.py` constants in CI so this can't drift again — even a tiny pytest that imports `_config` and asserts the README contains the right defaults would have caught this.

### 4b. `SENPI_HELPERS_LOCK_DIR` is read in `lock.py`, not exposed in `_config.py`

[README.md:56](_helpers/senpi_runtime_helpers/README.md#L56) lists `SENPI_HELPERS_LOCK_DIR` alongside the other `SENPI_HELPERS_*` env vars, implying it's part of the same configuration surface. But [_config.py](_helpers/senpi_runtime_helpers/_config.py) does not declare a `LOCK_DIR` constant. Instead [lock.py:42-45](_helpers/senpi_runtime_helpers/lock.py#L42-L45) reads it directly:

```python
def _lock_path(name: str, lock_dir: Optional[str]) -> Path:
    base = Path(lock_dir or os.environ.get("SENPI_HELPERS_LOCK_DIR") or _LOCK_DIR_FALLBACK)
```

Two problems:

1. **Inconsistent style.** Every other `SENPI_HELPERS_*` setting flows through `_config.py`'s `_env_str / _env_int / _env_float` helpers and lands as a module-level constant. `LOCK_DIR` is the only exception. Future readers grep `_config.py` to find configuration; they won't find this one.
2. **Tests can't override it cleanly.** Other settings can be exercised by patching `cfg.MAX_CONCURRENT` etc. `LOCK_DIR` requires either env-var manipulation or passing `lock_dir=` into `scanner_lock`. The tests work around it by passing `lock_dir=` ([test_lock.py:29](_helpers/senpi_runtime_helpers/tests/test_lock.py#L29)). That's fine for tests but reveals the asymmetry.

**Fix:** add to `_config.py`:

```python
LOCK_DIR = _env_str("SENPI_HELPERS_LOCK_DIR", "/tmp")
```

…and reference `cfg.LOCK_DIR` from `_lock_path`. Drop the `_LOCK_DIR_FALLBACK` constant — `_config.py` is the only thing that should know about defaults.

### 4c. `pangolin_config.py` module docstring still describes the old transport

[pangolin_config.py:1-23](pangolin/scripts/pangolin_config.py#L1-L23):

```python
"""PANGOLIN v2 — Shared MCP helper + atomic state I/O + output helpers.

v2 producer responsibilities are narrower than v1:
  - Fetch market data via MCP (market_list_instruments, ...)
  - Push signals via `openclaw senpi external-scanner ingest`
    (runtime owns execution)
...
"""
```

Stale. The whole point of this branch is that signals are no longer pushed via `openclaw senpi external-scanner ingest`. The module docstring is the first thing a 20-year reviewer reads, and it tells them something the code no longer does.

**Fix:** rewrite the bullet to "Push signals via the runtime API on `127.0.0.1` through `senpi_runtime_helpers.SenpiClient.push_signal`." Or trim the docstring to the structural part and drop the transport detail — `mcporter_call` has its own docstring already.

### 4d. `_logging._emit` lies about timestamp precision

[_logging.py:25-27](_helpers/senpi_runtime_helpers/_logging.py#L25-L27):

```python
payload = {
    "ts": time.time(),
    "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z",
    ...
```

The `iso` field always ends in `.000Z`. The format string suggests millisecond precision; the actual value is always exactly `.000`. Two costs:

- Anyone aggregating these logs by `iso` and bucketing on millisecond boundaries gets garbage.
- The `ts` field already gives sub-second precision (`time.time()` is a float). Producing `iso` with fake `.000` is strictly worse than either (a) producing real ms precision or (b) omitting the millisecond suffix.

**Fix:** either generate the ms component honestly:

```python
now = time.time()
ms = int((now - int(now)) * 1000)
"iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{ms:03d}Z",
```

…or drop `.000Z` entirely and let consumers parse `ts` if they need ms precision. Lying-with-zeros is the worst option.

### 4e. README's "logs" sample doesn't match the actual emitter format

[README.md:62-69](_helpers/senpi_runtime_helpers/README.md#L62-L69) shows:

```
[senpi_helpers] {"ts":..., "event":"mcp_call", "tool":"leaderboard_get_markets", "duration_ms":380, "status":"ok"}
```

The actual emitter ([_logging.py:24-40](_helpers/senpi_runtime_helpers/_logging.py#L24-L40)) writes:

```
[senpi_helpers] {"ts": 1777994835.204244, "iso": "2026-05-05T15:27:15.000Z", "pid": 71259, "event": "lock_acquired", "name": "test_daemon_timeout"}
```

Differences a reader will notice:

- The README omits `iso` and `pid`. They're always present.
- The README writes compact JSON (`"ts":...`). The actual output is `json.dumps(...)` default, which inserts spaces after `:` (`"ts": ...`).
- Field order in the README is `ts → event → tool` etc.; in the code, the order is `ts → iso → pid → event → ...` (Python dict insertion order from [_logging.py:24-29](_helpers/senpi_runtime_helpers/_logging.py#L24-L29)). Anyone running a regex over the logs will expect the README format and won't get it.

**Fix:** paste real output from the test run into the README, or use `json.dumps(payload, separators=(",", ":"))` to compact the output and match the README. The README is supposed to be "proof the fix works" — it can't be the proof if the format is invented.

### 4f. README's example `sys.path.insert` is inconsistent with the migration cookbook

[README.md:11-12](_helpers/senpi_runtime_helpers/README.md#L11-L12) (Quick start):

```python
sys.path.insert(0, "/data/workspace/skills/_helpers")
```

[README.md:86-89](_helpers/senpi_runtime_helpers/README.md#L86-L89) (Migration cookbook):

```python
_HELPERS = os.path.join(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"), "skills", "_helpers")
if _HELPERS not in sys.path:
    sys.path.insert(0, _HELPERS)
```

[pangolin_config.py:46-48](pangolin/scripts/pangolin_config.py#L46-L48) (actual implementation):

```python
_helpers_path = str(Path(WORKSPACE) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)
```

Three different snippets for the same thing. The Quick start hardcodes the path (will break in any deployment where `OPENCLAW_WORKSPACE` is overridden). The other two respect the env var. Pick one canonical form and use it everywhere — the env-respecting form is correct.

### 4g. Missing fields documented vs. actually accepted by `push_signal`

[client.py:307-336](_helpers/senpi_runtime_helpers/client.py#L307-L336):

```python
def push_signal(
    self,
    address: str,
    scanner: str,
    data: Optional[Dict[str, Any]] = None,
    asset: Optional[str] = None,
    direction: Optional[str] = None,
    score: Optional[float] = None,
    signal_type: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Maps 1:1 to `openclaw senpi external-scanner ingest`:
    - `address` ↔ `--address`
    - `scanner` ↔ `--scanner`
    - `data`    ↔ `--payload`
    """
```

The docstring claims a 1:1 mapping with three fields. The signature accepts seven. `asset`, `direction`, `score`, `signal_type` are nowhere in the docstring. A reviewer hitting this asks: "what runtime schema accepts these top-level fields, and which does the docstring's wire format match?"

If they're top-level fields per the runtime's `SignalItem` schema, document them. If they're `data`-payload conveniences that should have gone *inside* `data`, then the function is wrong: it's hoisting them to the top of the wire payload where the runtime won't read them. Either way, the docstring needs to match the signature, or the signature needs to shrink.

**Action:** open `senpi-trading-runtime/runtime-api/routes/signals.schema.ts` (the comment in [client.py:251-254](_helpers/senpi_runtime_helpers/client.py#L251-L254) references it) and confirm the `SignalItem` shape. Whichever fields the schema doesn't accept must move into `data`. The current "we accept seven, document three" is the worst of both worlds — if a producer passes `score=...` thinking it goes top-level and the runtime expects `data.score`, the score is silently dropped.

---

## Section 5 — Dead code / unused symbols

Each one is small. The reason a senior reviewer cares: dead code rots and lies. A symbol with no callers makes future readers think it was load-bearing for *something*, and they leave it alone "just in case." It also defeats grep — `grep _ShutdownSignal` returns hits, but those hits don't mean anything.

### 5a. `daemon.py` imports `errno` and `os`, neither is used

[daemon.py:18-19](_helpers/senpi_runtime_helpers/daemon.py#L18-L19):

```python
import errno
import os
```

`grep` shows zero references to either symbol elsewhere in the file (`errno` appears nowhere; `os` is referenced as an import line only). The bug bot will flag both. Drop them.

### 5b. `_ShutdownSignal` is defined and caught, but never raised

[daemon.py:33-34](_helpers/senpi_runtime_helpers/daemon.py#L33-L34):

```python
class _ShutdownSignal(BaseException):
    """Raised internally to break out of the current tick on SIGTERM/SIGINT."""
```

Caught at [daemon.py:147-149](_helpers/senpi_runtime_helpers/daemon.py#L147-L149):

```python
except _ShutdownSignal:
    tick_status = "interrupted"
    stop_event.set()
```

`grep raise` across the file shows: only `_TickTimeout`, `ValueError`. `_ShutdownSignal` is never raised. The signal handler at [daemon.py:42-45](_helpers/senpi_runtime_helpers/daemon.py#L42-L45) only sets `stop_event`; it doesn't `raise _ShutdownSignal()`.

Two options, depending on what was intended:

- **Option A — actually raise it.** SIGTERM/SIGINT received mid-tick currently does nothing until the tick ends and the interruptible sleep kicks in. If the tick is in `time.sleep(120)` or a network call, shutdown waits up to `tick_timeout` for SIGALRM. If you want SIGTERM to abort the tick *now*, the handler should raise `_ShutdownSignal()` (similar to how `_TickTimeout` works). Then the `except _ShutdownSignal` clause is real.
- **Option B — delete it.** The current behavior is "let the tick finish, then shut down between ticks" which is fine. Then the class, the `except` clause, and the docstring referring to it are all dead. Delete them.

Pick whichever matches the actual desired behavior. The current half-implementation is the worst case: the docstring promises a feature the code doesn't deliver.

### 5c. `lock.py`: `prev_started` is read and never used

[lock.py:99-101](_helpers/senpi_runtime_helpers/lock.py#L99-L101):

```python
prev_pid = int(meta.get("pid", -1))
prev_started = float(meta.get("started", 0))
```

`prev_pid` is used in the staleness check. `prev_started` is read, then never referenced. Dead local. Drop it. (If it was meant to back the staleness check instead of `mtime`, the bug from 1b changes shape — pick one source of truth.)

### 5d. `lock.py`: `os.utime` before `unlink` is wasted I/O

[lock.py:138-152](_helpers/senpi_runtime_helpers/lock.py#L138-L152):

```python
finally:
    try:
        # Heartbeat-touch on release so the next acquirer knows the lock was
        # properly released and not stale.
        os.utime(str(path), None)
    except OSError:
        pass
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        path.unlink()
    except FileNotFoundError:
        pass
```

The comment claims the touch is for "the next acquirer." But two lines later the file is unlinked. Once the file is gone, no one can read its mtime. The touch is meaningless. It also implies the code has a heartbeat semantics that it does not (see 1b).

**Fix:** delete the `os.utime` block. If you keep heartbeat semantics later (1b option B), the touch belongs in a *long-running* background thread during the held window, not at release time.

### 5e. `pangolin-producer.py`: `import subprocess` and `OPENCLAW_BIN` are dead

[pangolin-producer.py:114](pangolin/scripts/pangolin-producer.py#L114) and [pangolin-producer.py:129](pangolin/scripts/pangolin-producer.py#L129):

```python
import subprocess
...
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
```

`grep subprocess.` returns zero call sites. `grep OPENCLAW_BIN` finds only the docstring header line + the constant definition itself. Both are dead now that signal push went through the wrapper.

The remaining docstring header at [pangolin-producer.py:105](pangolin/scripts/pangolin-producer.py#L105) still says:

```
OPENCLAW_BIN      — optional, default "openclaw"
```

…which is the same lie as 4c — env var is documented, never read. Drop the import, the constant, and the doc line.

### 5f. `mcporter_call(retries=2, ...)`: `retries` is documented as ignored

[pangolin_config.py:89-96](pangolin/scripts/pangolin_config.py#L89-L96):

```python
def mcporter_call(tool, retries=2, timeout=25, **params):
    """... `retries` is accepted for caller-API compatibility but unused — the
    wrapper has its own timeout. ..."""
    return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
```

`grep "retries=" pangolin/` confirms no caller passes `retries`. So "caller-API compatibility" isn't even buying anything — there's no caller relying on it.

Two problems:

- The **wrapper does not retry** ([client.py:182-237](_helpers/senpi_runtime_helpers/client.py#L182-L237) — single-attempt, raise on failure). The docstring says "the wrapper has its own timeout" but the user asked about retries, not timeout. Old code retried twice on subprocess failure (line 96 of the old `mcporter_call`). Now: zero retries. The docstring obscures that with a non-sequitur about timeouts.
- The argument is a vestigial parameter. Drop it. Anyone reading the signature thinks "ah, configurable retry count" and is wrong.

**Fix:** drop `retries` from the signature. If retries are still needed (the old code thought they were), implement them in the wrapper, not in the producer. If they're not needed, say so explicitly: "we drop the retries v1 had because the wrapper's transport has lower variance and one tick failure is recovered by the next tick at +5min."

### 5g. `parallel.py`: `_BoundedExecutor._adjust` return value is discarded on the release path

[parallel.py:33-37](_helpers/senpi_runtime_helpers/parallel.py#L33-L37):

```python
def _adjust(self, in_flight_delta: int = 0, waiting_delta: int = 0) -> Tuple[int, int]:
    with self._lock:
        self._in_flight += in_flight_delta
        self._waiting += waiting_delta
        return self._in_flight, self._waiting
```

[parallel.py:67](_helpers/senpi_runtime_helpers/parallel.py#L67):

```python
self._adjust(in_flight_delta=-1)
```

The acquire path uses both return values; the release path discards them. That's fine, but `_adjust` always pays the tuple-construction cost. Cheap, but signals a function that's trying to do two things (mutate + report). Either:

- Split into `_adjust` (mutates, returns nothing) and `_snapshot` (returns), or
- Accept the tuple cost as the price of one symmetric helper.

Trivial. Mention it for completeness; not a blocker.

### 5h. `test_daemon.py` imports `threading` but doesn't use it

[test_daemon.py:5](_helpers/senpi_runtime_helpers/tests/test_daemon.py#L5):

```python
import threading
```

`grep threading.` in the file finds nothing. (`os.path` is used at line 9 — that import stays.) `test_parallel.py` uses both. Drop the `threading` import in `test_daemon.py` only — the bug bot will flag it.

---

## Section 6 — `cache.py` correctness and growth

The cache is small (94 lines), but it lives in a long-running daemon that calls it many times per tick. Long-lived state is unforgiving.

### 6a. Unbounded growth — no size cap, no TTL eviction

[cache.py:23-24](_helpers/senpi_runtime_helpers/cache.py#L23-L24):

```python
_lock = threading.Lock()
_store: Dict[str, Tuple[float, Any]] = {}
```

[cache.py:62-65](_helpers/senpi_runtime_helpers/cache.py#L62-L65):

```python
value = client.mcp_call(tool, timeout=timeout, **arguments)
with _lock:
    _store[key] = (time.time(), value)
log_event("cache_miss", tool=tool, ttl_s=ttl_value)
```

There is no size cap. There is no eviction. Entries past their TTL are read-skipped (the `now - entry[0] <= ttl_value` check at [cache.py:58](_helpers/senpi_runtime_helpers/cache.py#L58) returns False) but **never deleted** — they sit in `_store` forever until `clear_cache()` is called.

Pangolin's `mcporter_call` cache key includes the call's arguments. `market_get_funding_history(asset=...)` is called once per candidate per tick. With 5–10 candidates per tick, 12 ticks per hour, 24 hours: 1,400–3,500 entries per day for that one tool. Multiply by every cached tool. Each entry is the full JSON response — easily 1–100 KB. Net: a producer that runs for a week accumulates hundreds of MB of dead cache entries.

The README sells this as a *per-tick* cache ([README.md:30](_helpers/senpi_runtime_helpers/README.md#L30)) — "same tool + same args within `TICK_CACHE_TTL`s reuses the result". But nothing enforces "tick" boundaries, and the producer doesn't call `clear_cache()` between ticks.

**Fix, three options:**

1. **`OrderedDict` + LRU cap.** Use `collections.OrderedDict`, evict from the front when `len(_store) > MAX_ENTRIES`. Add `SENPI_HELPERS_TICK_CACHE_MAX_ENTRIES` (default ~512). Cheapest.
2. **Opportunistic TTL sweep.** On every miss, sweep entries with `now - ts > ttl` while holding the lock. O(n) on each miss, but n is small if the cap is in place. Combine with #1.
3. **Document the call contract.** If the intent is genuinely per-tick, then `producer_daemon` should call `clear_cache()` between ticks (e.g. in [daemon.py](_helpers/senpi_runtime_helpers/daemon.py#L138-L156) right before `fn()`). Currently it does not. README and behavior diverge.

Best path: pick #1 + #3. The cap is a hard ceiling on memory; the per-tick clear keeps the cache *actually* per-tick.

### 6b. `_make_key` uses `default=str` — distinct objects collide

[cache.py:27-33](_helpers/senpi_runtime_helpers/cache.py#L27-L33):

```python
def _make_key(tool: str, kwargs: Dict[str, Any]) -> str:
    try:
        encoded = json.dumps(kwargs, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = repr(sorted(kwargs.items()))
    h = hashlib.sha1(f"{tool}|{encoded}".encode("utf-8")).hexdigest()[:16]
    return f"{tool}:{h}"
```

Two issues stacked:

- **`default=str` silently coerces non-JSON-serialisable values** to their `str()`. Two distinct objects with the same `__str__` collide on cache key. For the current MCP tool args (scalars and lists of scalars), this is fine. The day someone passes a `datetime` object as a kwarg expecting it to round-trip through MCP, `str(dt1) == str(dt2)` for two different timestamps with sub-second-truncated `__str__` and you get a stale cache hit on a different request.
- **The fallback uses `repr(sorted(kwargs.items()))`.** `sorted` on dict items orders by key (a string), then by value. If the value is unsortable across types (e.g. one item has `value=None`, another has `value=5`), Python 3 raises `TypeError`. The whole `_make_key` call then raises, propagating up through `cached_mcp_call`. Cache is unreachable for that call; producers trying to use it just hit MCP every time. Slightly worse than not having the cache.

**Fix:** restrict the cache to JSON-only kwargs. If `json.dumps` fails (no `default=`), bypass the cache and call `client.mcp_call` directly with a logged event:

```python
try:
    encoded = json.dumps(kwargs, sort_keys=True)
except (TypeError, ValueError):
    log_event("cache_skipped", tool=tool, reason="non_jsonable_args")
    return client.mcp_call(tool, timeout=timeout, **arguments)
```

That removes the silent-collision risk and the unsortable-fallback crash. SHA1 truncated to 16 hex chars is 64 bits — collision risk at producer scale is fine.

### 6c. Lookup-then-write race — duplicate MCP calls under contention

[cache.py:56-66](_helpers/senpi_runtime_helpers/cache.py#L56-L66):

```python
with _lock:
    entry = _store.get(key)
    if entry is not None and now - entry[0] <= ttl_value:
        log_event("cache_hit", ...)
        return entry[1]

value = client.mcp_call(tool, timeout=timeout, **arguments)
with _lock:
    _store[key] = (time.time(), value)
```

The lock is held for the lookup and for the store, but not across the MCP call itself (correct — you don't want to block all callers behind one slow network request). The consequence: if two threads in `parallel(...)` ask for the same `(tool, args)` simultaneously and both miss, **both** issue the MCP request. Whichever finishes second overwrites the first.

This is a "thundering herd" against the MCP backend. For short bursts of parallel calls to the same key (Pangolin's per-candidate funding history loop where two candidates happen to share the same query), it doubles the work the cache was supposed to prevent.

**Fix:** per-key future. On miss, install a `threading.Event` placeholder under `_lock`, release the lock, do the MCP call, set the value and signal the event. Other threads that see the placeholder wait on the event instead of issuing duplicate calls. Standard pattern, ~20 lines:

```python
_pending: Dict[str, threading.Event] = {}
...
with _lock:
    entry = _store.get(key)
    if entry and now - entry[0] <= ttl: return entry[1]
    pending = _pending.get(key)
    if pending is None:
        pending = threading.Event()
        _pending[key] = pending
        is_owner = True
    else:
        is_owner = False
if not is_owner:
    pending.wait()
    return _store[key][1]    # owner has populated it
try:
    value = client.mcp_call(tool, timeout=timeout, **arguments)
    with _lock:
        _store[key] = (time.time(), value)
    return value
finally:
    pending.set()
    with _lock:
        _pending.pop(key, None)
```

Optional. If `parallel` is rare and same-key collisions are rarer still, this is over-engineered. Mention it; defer until a metric says otherwise.

### 6d. `tick_cache(client)` returns a callable whose signature lies

[cache.py:69-85](_helpers/senpi_runtime_helpers/cache.py#L69-L85):

```python
def tick_cache(client: Any, ttl: Optional[float] = None) -> Callable[..., Any]:
    @functools.wraps(client.mcp_call)
    def call(tool: str, timeout: Optional[float] = None, **arguments: Any) -> Any:
        return cached_mcp_call(client, tool, ttl=ttl, timeout=timeout, **arguments)
    return call
```

`functools.wraps(client.mcp_call)` copies `mcp_call`'s `__name__`, `__doc__`, and `__wrapped__` onto the inner `call`. But the inner `call`'s actual signature *differs* from `mcp_call` only in the docstring narrative — they're functionally identical. The `wraps` is more of a "this is a transparent wrapper around mcp_call" hint than something the reader needs.

The only practical consequence: if a producer does `inspect.signature(mcp)` after `mcp = tick_cache(client)`, they get `mcp_call`'s signature, which happens to be correct. Fine in practice.

But the docstring on `mcp_call` mentions things that don't apply here ("Raises `urllib.error.URLError`"). The user calling `mcp(...)` may also hit cache-related exceptions. Trivial. Either drop the `wraps` or write a real docstring on `call`.

### 6e. `cache_miss` event misses `duration_ms` and `cache_hit` misses `tool` count

[cache.py:59](_helpers/senpi_runtime_helpers/cache.py#L59) and [cache.py:65](_helpers/senpi_runtime_helpers/cache.py#L65):

```python
log_event("cache_hit", tool=tool, age_s=round(now - entry[0], 2))
...
log_event("cache_miss", tool=tool, ttl_s=ttl_value)
```

The README ([README.md:64-69](_helpers/senpi_runtime_helpers/README.md#L64-L69)) advertises these events as "proof the fix works" — the operator is supposed to filter Railway logs for them. Two minor inconsistencies:

- `cache_hit` reports `age_s` (cosmetic). `cache_miss` reports `ttl_s` (the *cap*, not anything that happened). Neither carries `duration_ms`. The MCP call that happened in the miss path is logged separately by `mcp_call`, but that means correlating a cache miss with its underlying call requires two log lines and a timestamp join.
- `cache_hit` gives no signal of *what was avoided*. To answer "how much did the cache save us?" the operator would compute `count(cache_hit) × mean(mcp_call.duration_ms)`. Useful but indirect.

Trivial. Add `duration_ms` (the elapsed time of the cache lookup itself, sub-millisecond, mostly noise) or — more useful — emit a single `cache_summary` event per N calls with hit count, miss count, mean miss latency. Defer if you don't operationally need it yet.

### 6f. Module-level `_store` persists across re-imports — test pollution risk

[cache.py:24](_helpers/senpi_runtime_helpers/cache.py#L24):

```python
_store: Dict[str, Tuple[float, Any]] = {}
```

`_store` lives at module scope, so all `SenpiClient` instances in the same process share one cache. The test [test_cache.py:25-26](_helpers/senpi_runtime_helpers/tests/test_cache.py#L25-L26) handles this with `setUp` calling `clear_cache()`. Good. But:

- If two distinct producers ever ran in the same process (unlikely now, conceivable if the openclaw harness consolidates), they share a cache key namespace. `tool="strategy_get_clearinghouse_state", args={"strategy_wallet": "0xA"}` and the same args from a different producer collide on the same key. Same value, OK. But it does mean *any* producer in the process can read another's cached responses. For tools with auth-scoped output (which `strategy_get_clearinghouse_state` is), that's a privacy boundary cross.
- A senior reviewer will probably ask: should this be per-`SenpiClient` instance, not per-process? The README implies process scope. The implementation matches. But the *contract* should be explicit, ideally on `SenpiClient` itself: "I share a cache with every other client in this process." That's a surprising default.

**Fix (minimal):** keep module scope, add a docstring note on `SenpiClient` that the cache is process-wide. **Fix (cleaner):** move `_store` onto the client; pass `client._cache` into `cached_mcp_call`. One-line API change to `cached_mcp_call(client, ...)` — already takes the client. Then the cache key namespace is naturally bounded by the client.

---

## Section 7 — Misc / low priority

Each of these is small in isolation. Together they're the texture a 20-year reviewer reads as "first-draft code that hasn't been polished yet." Worth fixing before the PR; none are blockers.

### 7a. `_BoundedExecutor._last_warn_emitted` is read/written without `_lock`

[parallel.py:31](_helpers/senpi_runtime_helpers/parallel.py#L31), [parallel.py:43-45](_helpers/senpi_runtime_helpers/parallel.py#L43-L45):

```python
self._last_warn_emitted = 0.0
...
if now - self._last_warn_emitted < 5.0:
    return
self._last_warn_emitted = now
```

Read and write are outside `_lock`. Two threads can both pass the `< 5.0` check at the same time and both emit the warn. Worst case: duplicate warnings. Not a correctness bug, just a sloppy concurrency boundary. Move the read/write inside `_lock` (it's already cheap — already used for `_in_flight` / `_waiting`).

### 7b. `parallel` spawns one OS thread per call

[parallel.py:110-116](_helpers/senpi_runtime_helpers/parallel.py#L110-L116):

```python
for i, fn in enumerate(calls):
    t = threading.Thread(target=worker, args=(i, fn), daemon=True)
    threads.append(t)
    t.start()

for t in threads:
    t.join()
```

If `len(calls) == 1000`, you spawn 1000 OS threads even though only `max_concurrent=8` actually run at once. Each thread costs ~8 MB of stack on Linux (default `pthread_create` stack). That's potentially 8 GB of address space for nothing — and on macOS/Linux without overcommit tuning, you'll hit `cannot create thread` long before 1000.

Producer use cases today are tens of calls per tick, so this is theoretical. But the docstring says "Beyond `max_concurrent` calls **queue**, never reject" — readers will assume the implementation is queue-bounded. It is *concurrency*-bounded but *thread*-unbounded.

**Fix:** use `concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent)`. The pool's internal queue does the actual queuing; you spawn at most `max_concurrent` threads regardless of call count. Stdlib, drop-in:

```python
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=cap) as ex:
    futures = [ex.submit(fn) for fn in calls]
    results = [f.result() if not f.exception() else f.exception() for f in futures]
```

### 7c. `parallel` results returns `Exception` objects in-place — easy to misuse

[parallel.py:86-88](_helpers/senpi_runtime_helpers/parallel.py#L86-L88) (docstring):

```
Returns:
    List of results in the same order as `calls`. Failed calls return the
    Exception object (when `raise_first_exception=False`).
```

The caller has to `isinstance(r, BaseException)` to distinguish success from failure. If the underlying tool ever legitimately returns a `BaseException`-subclass (e.g. some MCP tool starts returning structured error objects), the caller's check breaks. More importantly, the call site looks like `for r in results: process(r)` — silently calling `.foo()` on an `Exception` instance gives `AttributeError`, which is then *not* the original error. Debugging trail is broken.

**Fix:** return `(ok: bool, value: Any)` tuples, or return a `results` object with `.successful` / `.failures` lists. The current shape is the worst of both worlds — neither clean exceptions nor clean results.

### 7d. `parallel(raise_first_exception=True)` raises *after* every thread joins

[parallel.py:115-131](_helpers/senpi_runtime_helpers/parallel.py#L115-L131):

```python
for t in threads:
    t.join()

duration_ms = ...
log_event("parallel_complete", ...)

if raise_first_exception:
    for r in results:
        if isinstance(r, BaseException):
            raise r
```

The flag's name suggests "fail fast." The implementation is "wait for all to finish, *then* raise the first exception by index." If 1 of 1000 calls fails fast at t=0.1s and 999 take 30s each, you'll wait 30s before the exception surfaces.

**Fix two options:**
- Rename to `raise_after_completion` so the name matches the behavior, or
- Actually fail fast: use `ThreadPoolExecutor` + `concurrent.futures.wait(..., return_when=FIRST_EXCEPTION)`, cancel pending futures, raise.

Pick whichever the actual use case wants. Don't ship the misleading name.

### 7e. `client.py`: `_post_json` declared return type is the private `urllib.request.addinfourl`

[client.py:45-50](_helpers/senpi_runtime_helpers/client.py#L45-L50):

```python
def _post_json(
    url: str,
    body: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
) -> "urllib.request.addinfourl":
```

`urllib.request.addinfourl` is documented as returned-but-not-public. Use `http.client.HTTPResponse` or just leave it untyped — it's a private helper. Type-checkers (mypy / pyright) will have warnings to give about this.

Same for [client.py:60](_helpers/senpi_runtime_helpers/client.py#L60). Trivial.

### 7f. `client.py`: empty / non-JSON responses silently return `{}`

[client.py:60-88](_helpers/senpi_runtime_helpers/client.py#L60-L88):

```python
def _read_response_body(resp) -> Dict[str, Any]:
    ...
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            ...
        return {}                          # ← SSE with no parseable data
    if not text:
        return {}                          # ← empty body
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except json.JSONDecodeError:
        return {}                          # ← invalid JSON
```

Three failure modes all collapse into `{}`. `_unwrap_tool_result` then returns `result=None`. The caller sees `None` and assumes "tool returned nothing", not "we got back a malformed response."

**Fix:** raise `SenpiClientError("malformed response: ...")` in each of those three branches, so the failure surfaces. Currently they're indistinguishable from a tool that genuinely returned an empty result.

### 7g. `client.py`: `push_signals` validation is partial

[client.py:258-266](_helpers/senpi_runtime_helpers/client.py#L258-L266):

```python
if not isinstance(items, list) or not items:
    raise SenpiClientError("push_signals() requires a non-empty list")
for i, it in enumerate(items):
    if not isinstance(it, dict):
        raise SenpiClientError(f"item[{i}] must be a dict")
    if "address" not in it or "scanner" not in it:
        raise SenpiClientError(
            f"item[{i}] missing required fields: address, scanner"
        )
```

Validates presence but not types. `address=None` or `address=42` slips through and gets serialized into the JSON body. The runtime API will presumably 400 it, so failure shows up — but late and with a confusing trail (Python serialized `null`, runtime says "address must be a string", operator wonders why the producer ever tried).

Cheap improvement: add `isinstance(it["address"], str) and it["address"].startswith("0x")` and `isinstance(it["scanner"], str)` checks. Better: leave validation entirely to the runtime — but then drop the `not in` checks too. Don't half-validate.

### 7h. `_post_json`: no Content-Length / connection-close handling

[client.py:45-57](_helpers/senpi_runtime_helpers/client.py#L45-L57):

```python
def _post_json(...):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for k, v in headers.items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout)
```

`urlopen` adds Content-Length itself based on `data` length, fine. But every request defaults to `Connection: close` because the underlying `http.client` defaults are server-default-on-no-keep-alive. (Tied to 3d — keep-alive would solve this.)

Also: if the server sends `Connection: close` and the client doesn't drain the body before context-exit, urllib does the right thing. But if the response is large (SSE stream) and the caller parses early-return, the unconsumed body is dropped — fine for a one-shot — *not* fine if you ever switch to keep-alive. Worth a doc note when 3d is implemented.

### 7i. `daemon.py`: drift comment is overconfident

[daemon.py:170-174](_helpers/senpi_runtime_helpers/daemon.py#L170-L174):

```python
# Sleep until the next interval boundary (no drift accumulation).
elapsed = time.time() - tick_started_at
sleep_for = max(0.0, interval_seconds - elapsed)
```

The comment says "no drift accumulation". What it actually does: each tick targets `interval_seconds` from the *previous tick's start*, not from a fixed boundary. A single overrun shifts the schedule permanently — `tick_n` starts at `tick_(n-1)_start + max(interval, tick_(n-1)_duration)`. Future ticks land off the original wall-clock boundaries.

If you want true non-drifting cron-like cadence, target `start_loop + n * interval_seconds`:

```python
target = start_loop + tick_count * interval_seconds
sleep_for = max(0.0, target - time.time())
```

If the current "shift on overrun" behavior is intentional (and it might be — it gives the producer breathing room after a slow tick), the comment is misleading and should say "single-overrun shift, no further accumulation."

### 7j. `test_lock.test_busy_when_another_process_holds_lock` leaks PIPE handles

Test output observed during run:

```
ResourceWarning: unclosed file <_io.TextIOWrapper name=6 encoding='UTF-8'>
ResourceWarning: unclosed file <_io.TextIOWrapper name=3 encoding='UTF-8'>
```

[test_lock.py:69-85](_helpers/senpi_runtime_helpers/tests/test_lock.py#L69-L85):

```python
proc = subprocess.Popen(
    [sys.executable, "-c", script],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
try:
    line = proc.stdout.readline().strip()
    ...
finally:
    proc.terminate()
    proc.wait(timeout=5)
```

`proc.stdout` and `proc.stderr` are file objects that aren't closed. Python warns at GC. Either use `subprocess.Popen` as a context manager (Python 3.2+):

```python
with subprocess.Popen(..., stdout=PIPE, stderr=PIPE, text=True) as proc:
    ...
```

…or explicitly `proc.stdout.close(); proc.stderr.close()` in the finally. The bug bot will flag this.

### 7k. `client.py`: redundant header-case lookup

(Already noted in 3f for completeness — `resp.headers` is case-insensitive in CPython, so the second `.get("mcp-session-id")` at [client.py:153](_helpers/senpi_runtime_helpers/client.py#L153) never fires. Drop it.)

---

## Summary

| Section | Findings | Severity |
|---|---|---|
| 1 — Lock correctness | 2 | **Critical** — concurrent holders, lock theft. Must fix. |
| 2 — Producer regressions | 6 | **High** — fallback paths broken, multi-wallet collision, dead warn. |
| 3 — `SenpiClient` | 6 | **High** — thread-safety, init poisoning, perf. Fix before scaling. |
| 4 — Docs/config drift | 7 | **Medium** — README is wrong about the default port. Will mis-route. |
| 5 — Dead code | 8 | **Medium** — bug bot will flag. Cheap to remove. |
| 6 — Cache | 6 | **Medium** — unbounded memory in long-running daemons. |
| 7 — Misc | 11 | **Low** — polish. None blocking. |

**Total: 46 findings.**

### Order to fix

1. **Block PR on these.** 1a, 1b, 2a, 2b, 2d, 4a. They each break a real contract today.
2. **Fix in the same PR if possible.** 3a, 3c, 5a-h. Cheap, won't reopen review surface.
3. **Follow-up PR.** 3d (keep-alive), 6a (cache cap), 7b (`ThreadPoolExecutor`). Each is a meaningful refactor; deserves its own diff and benchmark.
4. **Investigate.** 4g — confirm `SignalItem` schema before producers ship `score=...` to silent /dev/null.

### What's right with this PR

To not be all stick: the design choices are good. Stdlib-only is the right call for a wrapper that has to ship inside the openclaw image with no extra deps. `tick_cache(client)` returning a callable instead of a global is an idiomatic Python pattern. Splitting `cached_mcp_call` (function) from `tick_cache` (factory) gives the right two affordances. The `[senpi_helpers]` log-prefix discipline is excellent — it makes the migration story Railway-grep-able, which is the first thing operators will want. The README's "what problem each helper solves" table is exactly the documentation a senior reviewer wants to read first.

The bones are solid. The issues above are polish, edge cases, and one nasty correctness bug in `lock.py`.


---
