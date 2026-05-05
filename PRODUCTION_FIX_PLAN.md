# Production-Readiness Fix Plan — `wrapped-skills` + `runtime-phase-2-api`

**Goal:** make Pangolin work fine in production. Fix all 46 code-review findings in scope today; defer 4 heavy refactors to a follow-up PR with their own benchmarks.

**Strategy:** sequence work from low-risk → high-risk so partial completion always yields a defensible state. Test box stays runnable throughout.

---

## Bucket A — README + docs (8 items, ~30 min)

Lowest risk. Ship first so other work can build on accurate docs.

| # | Action | File |
|---|---|---|
| 4a | Update default to `8787` | `_helpers/senpi_runtime_helpers/README.md` |
| 4b | Add `LOCK_DIR = _env_str("SENPI_HELPERS_LOCK_DIR", "/tmp")` to `_config.py`, drop `_LOCK_DIR_FALLBACK` from `lock.py` | `_config.py`, `lock.py` |
| 4c | Rewrite docstring — describe wrapper.push_signal transport, drop legacy CLI mention | `pangolin_config.py` |
| 4d | Generate real ms in `iso` (`f".{ms:03d}Z"`) | `_logging.py` |
| 4e | Replace README log sample with real captured output (matching `_logging` field order) | `_helpers/senpi_runtime_helpers/README.md` |
| 4f | Pick one canonical sys.path snippet (env-respecting form), use everywhere | README + `pangolin_config.py` |
| 4g | Document all 7 `push_signal` kwargs with reference to `signals.schema.ts`. Note: top-level `score` is 0..1 confidence; producer composite stays in `data.score`. | `client.py` |
| 6d | Drop `@functools.wraps(client.mcp_call)` on `tick_cache.call` — write a real docstring | `cache.py` |

---

## Bucket B — PR-blockers (5 items, ~75 min)

Real correctness contracts.

| # | Fix |
|---|---|
| 1a | Stale recovery: don't `unlink`. `os.open(path, O_RDWR\|O_CREAT)`, then `flock(LOCK_EX\|LOCK_NB)`. If flock fails, raise `BlockingIOError`. If succeeds, overwrite metadata in place via `_write_metadata_inplace`. Inode stays stable, no race. |
| 1b | Tighten predicate to `not alive` only. Drop the mtime-based staleness for now (no heartbeat thread to back it). PID-alive via `os.kill(pid, 0)` is reliable. Update README to remove "heartbeat" claim. |
| 2a | Restore `None` contract: catch `(urllib.error.URLError, urllib.error.HTTPError, SenpiClientError)` inside `mcporter_call`, return `None`. Docstring says so. Producer's existing fallback paths stay live. |
| 2b | `producer_daemon(name=f"pangolin-producer-{wallet_hash}")` — derive 12-char SHA256 of lowercased wallet, identical to `_wallet_state_dir()`. |
| 2d | Set `tick_timeout=360` so `WARN_OVER_300S` fires before SIGALRM. Aligns the producer's two budgets. |

---

## Bucket C — Dead code (8 items, ~25 min)

Small, mechanical, no behavior change.

| # | Action |
|---|---|
| 5a | Drop `import errno, os` from `daemon.py` |
| 5b | Delete `_ShutdownSignal` class + its `except` clause (signal handler only sets `stop_event`; fine) |
| 5c | Drop `prev_started = float(meta.get("started", 0))` from `lock.py` |
| 5d | Drop `os.utime(...)` before `unlink` in lock release path |
| 5e | Drop `import subprocess` + `OPENCLAW_BIN` constant in `pangolin-producer.py` |
| 5f | Drop `retries=2` param from `mcporter_call` signature (no caller; wrapper doesn't retry anyway) |
| 5g | Split `_adjust` into `_adjust_mutate` (no return) + `_snapshot` (returns); use right one at each call site |
| 5h | Drop unused `import threading` from `test_daemon.py` |

---

## Bucket D — Helper polish (15 items, ~90 min)

| # | Fix |
|---|---|
| 3a | Wrap `_initialize_if_needed` body in `threading.Lock()` with double-checked `if self._session.initialized` inside |
| 3b | Replace `next_id += 1` with `itertools.count(1)` |
| 3c | If `notifications/initialized` raises after `initialize` succeeded, clear `session_id = None`, `initialized = False` before re-raising |
| 3e | Add explicit `except SenpiClientError` clause in `mcp_call`'s try, log `status="server_error"` separately from `status="exception"` |
| 3f / 7k | Drop the redundant lowercase `mcp-session-id` lookup |
| 6b | `_make_key`: drop `default=str`. Wrap `json.dumps` in try; on `TypeError`, log `cache_skipped` event and bypass cache (return direct `client.mcp_call`). Drop `repr(sorted(...))` fallback. |
| 6e | Add `cache_summary` event emitted from `producer_daemon` after each tick: hit/miss counts, mean miss `duration_ms`. Wire via cache module-level counters. |
| 6f | Move `_store` onto `SenpiClient` instance (`self._cache: dict`); pass `client._cache` from `cached_mcp_call`. Document scope. |
| 7a | Move `_last_warn_emitted` read/write inside `_lock` (already used for in_flight/waiting) |
| 7c | Change `parallel` to return `(ok, value)` tuples; update `cached_mcp_call` + `pangolin-scanner.py` (none currently use parallel directly — wrapper-only impact) |
| 7e | Type `_post_json` return as `http.client.HTTPResponse \| Any` or simply omit annotation |
| 7f | `_read_response_body` raises `SenpiClientError("malformed response")` on each of: empty SSE, empty body, JSON decode error |
| 7g | `push_signals`: `isinstance(it["address"], str)` and `it["address"].startswith("0x")` and `isinstance(it["scanner"], str)`; reject pre-POST |
| 7h | Doc note on `_post_json`: today connection-closes per request; keep-alive coming in 3d |
| 7i | Rewrite drift-comment to honest: "single-overrun shift, no further accumulation" |
| 7j | `subprocess.Popen` as context manager in `test_lock` to close PIPE handles |

---

## Bucket E — Skill polish (4 items, ~30 min)

| # | Fix |
|---|---|
| 2c | Drop legacy `acquire_lock`/`release_lock` from `pangolin-producer.py`. With 2b fixed, `scanner_lock` is per-wallet and sufficient. |
| 2e | Drop `if push_signal(payload):` wrapper at the call site since `push_signal` now always returns `True` or raises. Update `push_signal` to be `def push_signal(payload) -> None`. Wrap atomic state mutations in `try`/`except SenpiClientError` if any tick may emit > 1 signal. |
| 2f | Move `_wrapper_client = SenpiClient()` behind `@functools.lru_cache(maxsize=1)` accessor `_get_wrapper_client()`. Validate `SENPI_AUTH_TOKEN` non-empty on first access; raise loud. Drop the import-time log_event. |
| 2f' | Drop `score` kwarg from Pangolin's `cfg._wrapper_client.push_signal(...)` call (pangolin's composite stays in data; top-level score = 0..1 confidence only — see 4g) |

---

## Bucket F — Medium effort (4 items, ~90 min)

Doable today, none architectural.

| # | Fix |
|---|---|
| 6a | `OrderedDict` + `MAX_ENTRIES` cap (default 512, env override `SENPI_HELPERS_TICK_CACHE_MAX_ENTRIES`). On miss, evict oldest if over cap. Opportunistic TTL sweep on every miss (cheap when cap is in place). Hard ceiling on long-running memory. |
| 6c | Per-key future: on cache miss, install `threading.Event` placeholder under `_lock`; subsequent threads on same key wait. Eliminates thundering herd. |
| 7d | Either rename `raise_first_exception` → `raise_after_completion` (matches behavior), OR migrate `parallel` to `ThreadPoolExecutor` + `concurrent.futures.wait(return_when=FIRST_EXCEPTION)`. **Pick rename** — the refactor is 7b's territory. |

---

## Deferred (3 items)

Each deserves its own PR with benchmark in the description. Tracking-only today.

| # | Why deferred | Tracking |
|---|---|---|
| 3d | Keep-alive HTTPS — needs `http.client.HTTPSConnection` + connection pool + thread safety for the pool. Real refactor with measurable speedup; benchmark before/after on Pangolin tick wall-clock. | Open issue: "Helper: keep-alive HTTPS connection pool" |
| 7b | `ThreadPoolExecutor` migration — `parallel` semantics need a callable-level test that 1000 calls don't spawn 1000 threads. Pair with 7d. | Open issue: "Helper: bounded thread pool for parallel()" |
| (Investigate) 4g — schema confirmation | DONE — answered from code (see Bucket A). | n/a |

---

## Test + ship sequence

1. **Land Bucket A** (docs only) → run 23 unit tests → commit, push.
2. **Land Bucket C** (dead code) → tests → commit, push.
3. **Land Bucket B** (blockers) → tests → **commit per blocker** so reviewer can read each independently → push.
4. **Land Bucket D** (helper polish) → tests → commit per logical group (3-cluster, 6-cluster, 7-cluster) → push.
5. **Land Bucket E** (skill polish) → tests → commit, push.
6. **Land Bucket F** (medium) → tests → commit per fix → push.
7. **Sync test box** to latest `wrapped-skills` (one runtime delete + reinstall, restart daemon).
8. **Watch 30 min** of pangolin-producer ticks. Confirm: clean lock acquire/release, no thundering-herd warnings, daemon doesn't leak memory (RSS stable across ticks).
9. **TG message** when green.

---

## Targets

- All 46 findings: 42 fixed in code, 3 deferred to follow-up issues, 1 (4g schema) closed via doc.
- Wall time: ~6 hours focused.
- Test box: unbroken throughout.
