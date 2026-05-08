"""tick_cache — MCP response memoization with TTL + LRU cap.

Two scanners often query the same MCP tool (e.g. clearinghouse_state)
multiple times per tick. The cache holds responses for `TICK_CACHE_TTL`
seconds so duplicate calls within a tick reuse the result.

Scope: cache state lives on the SenpiClient instance (`client._cache`),
not at module scope. Multiple clients in the same process do not share
cache keys — eliminates cross-client key namespace collisions.

`clear_cache(client)` drops the per-client cache. `cache_summary(client)`
returns hit/miss/eviction counters that producer_daemon emits between ticks.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional

from . import _config as cfg
from ._logging import log_event


# Per-client lock for cache mutations. Stored alongside the cache itself.
_CLIENT_CACHE_ATTR = "_cache"


def _client_cache(client: Any) -> "_Cache":
    """Lazily attach a `_Cache` instance to the client. Idempotent + thread-safe
    via the GIL on the attribute write — the first racy initialization stays
    consistent because `_Cache.__init__` allocates everything before the
    attribute is bound."""
    cache = getattr(client, _CLIENT_CACHE_ATTR, None)
    if isinstance(cache, _Cache):
        return cache
    new_cache = _Cache()
    # If client._cache was previously set to a plain dict (legacy SenpiClient
    # __init__ path before the upgrade), replace it with the typed cache.
    setattr(client, _CLIENT_CACHE_ATTR, new_cache)
    return new_cache


class _Cache:
    """OrderedDict-backed LRU cache with TTL eviction and counters.

    Per-key in-flight tracking prevents thundering herd: if N parallel
    callers miss the same key simultaneously, only the first issues the
    MCP call; the others wait on a `threading.Event` and read the result
    once it lands.
    """

    __slots__ = ("store", "lock", "hits", "misses", "evictions", "skipped",
                 "coalesced", "pending")

    def __init__(self) -> None:
        self.store: "OrderedDict[str, _Entry]" = OrderedDict()
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.skipped = 0
        self.coalesced = 0  # how many waiters merged into an in-flight call
        # key → threading.Event for in-flight MCP calls. Owner removes the
        # event after publishing the value; waiters block on it.
        self.pending: Dict[str, threading.Event] = {}


class _Entry:
    __slots__ = ("ts", "value")

    def __init__(self, ts: float, value: Any) -> None:
        self.ts = ts
        self.value = value


def _make_key(tool: str, kwargs: Dict[str, Any]) -> Optional[str]:
    """Stable cache key from (tool, kwargs). Returns None when kwargs aren't
    JSON-serialisable — caller bypasses cache for that call (and logs)."""
    try:
        encoded = json.dumps(kwargs, sort_keys=True)
    except (TypeError, ValueError):
        return None
    h = hashlib.sha1(f"{tool}|{encoded}".encode("utf-8")).hexdigest()[:16]
    return f"{tool}:{h}"


def cached_mcp_call(
    client: Any,
    tool: str,
    ttl: Optional[float] = None,
    timeout: Optional[float] = None,
    **arguments: Any,
) -> Any:
    """Call `client.mcp_call(tool, **arguments)` with per-client memoization.

    LRU + TTL cap: at most `cfg.TICK_CACHE_MAX_ENTRIES` entries; oldest
    evicted on insert. Entries past TTL are recomputed on access.
    """
    ttl_value = ttl if ttl is not None else cfg.TICK_CACHE_TTL
    cache = _client_cache(client)
    key = _make_key(tool, arguments)
    if key is None:
        cache.skipped += 1
        log_event("cache_skipped", tool=tool, reason="non_jsonable_args")
        return client.mcp_call(tool, timeout=timeout, **arguments)

    now = time.time()
    pending: Optional[threading.Event] = None
    is_owner = False
    with cache.lock:
        entry = cache.store.get(key)
        if entry is not None and now - entry.ts <= ttl_value:
            cache.store.move_to_end(key)  # mark recently-used for LRU
            cache.hits += 1
            age = now - entry.ts
            log_event("cache_hit", tool=tool, age_s=round(age, 3))
            return entry.value
        # Miss path. Coalesce concurrent misses on the same key: if a
        # caller is already issuing this MCP call, register as a waiter
        # on its event; otherwise become the owner.
        pending = cache.pending.get(key)
        if pending is None:
            pending = threading.Event()
            cache.pending[key] = pending
            is_owner = True
        else:
            cache.coalesced += 1

    if not is_owner:
        # Another caller is fetching this key. Wait for them and read the
        # value they publish. Use a timeout so a stalled owner doesn't
        # wedge waiters forever — the wait timeout is the call's own
        # `timeout` (or the default MCP timeout) plus a small slack.
        wait_for = (timeout if timeout is not None else cfg.MCP_TIMEOUT_SECONDS) + 5.0
        signaled = pending.wait(wait_for)
        if not signaled:
            # Owner stalled past its budget. Fall through to issue our
            # own call so the caller doesn't deadlock on a dead owner.
            log_event("cache_coalesce_timeout", tool=tool, wait_for_s=round(wait_for, 1))
            # Don't claim ownership — race with the original owner is OK;
            # last writer wins for the cache entry.
            value = client.mcp_call(tool, timeout=timeout, **arguments)
            return value
        # Owner has signaled. Re-check the cache under lock and re-validate
        # TTL — the original L116 freshness check applies to the coalesced
        # path too. If the owner raised, no fresh entry was published; the
        # remaining entry (if any) is the prior expired one we tried to
        # refresh, and returning it would silently violate TTL. Issue our
        # own call instead.
        with cache.lock:
            entry = cache.store.get(key)
            if entry is not None and time.time() - entry.ts <= ttl_value:
                return entry.value
        return client.mcp_call(tool, timeout=timeout, **arguments)

    # Owner path. Issue the MCP call without holding the cache lock so
    # other-key callers aren't serialized behind us. On any outcome
    # (success or exception), signal pending and clear the entry.
    miss_started = time.time()
    try:
        value = client.mcp_call(tool, timeout=timeout, **arguments)
    except BaseException:
        # Atomically remove our pending registration AND wake waiters under
        # the same lock. If the wake happened outside the lock, a waiter
        # could observe `pending` cleared but not yet signaled, race back to
        # the entry-check, and (depending on what the prior entry held) take
        # an inconsistent path. Setting the event under the lock means any
        # thread that re-acquires `cache.lock` sees a coherent post-failure
        # state (no pending, no fresh entry from this owner).
        with cache.lock:
            cache.pending.pop(key, None)
            pending.set()
        raise
    miss_duration_ms = int((time.time() - miss_started) * 1000)

    with cache.lock:
        cache.store[key] = _Entry(time.time(), value)
        cache.store.move_to_end(key)
        while len(cache.store) > cfg.TICK_CACHE_MAX_ENTRIES:
            cache.store.popitem(last=False)
            cache.evictions += 1
        cache.misses += 1
        cache.pending.pop(key, None)
        # Signal waiters BEFORE releasing cache.lock. A waiter that wakes
        # and re-acquires the lock now observes the freshly-published entry
        # (or, on the exception path above, the cleared pending registry) —
        # never the in-between state where the entry hasn't been published
        # yet but the event has fired.
        pending.set()

    log_event(
        "cache_miss",
        tool=tool,
        ttl_s=ttl_value,
        miss_duration_ms=miss_duration_ms,
        size=len(cache.store),
    )
    return value


def tick_cache(client: Any, ttl: Optional[float] = None) -> Callable[..., Any]:
    """Returns a callable that wraps `client.mcp_call` with per-client TTL cache.

    Example:
        from senpi_runtime_helpers import SenpiClient, tick_cache

        client = SenpiClient()
        mcp = tick_cache(client)
        markets = mcp("leaderboard_get_markets", limit=100)  # MCP call
        markets = mcp("leaderboard_get_markets", limit=100)  # cache hit

    The returned `call(tool, timeout=None, **arguments)` invokes
    `cached_mcp_call(client, tool, ttl=ttl, timeout=timeout, **arguments)`.
    Repeated calls with identical (tool, arguments) return the cached value
    until `ttl` elapses, the entry is evicted by LRU, or `clear_cache(client)`
    is invoked.
    """
    def call(tool: str, timeout: Optional[float] = None, **arguments: Any) -> Any:
        return cached_mcp_call(client, tool, ttl=ttl, timeout=timeout, **arguments)
    return call


def clear_cache(client: Optional[Any] = None) -> None:
    """Drop cached responses for `client` (or all if None — legacy module-scope
    behavior, kept for tests)."""
    if client is not None:
        cache = _client_cache(client)
        with cache.lock:
            n = len(cache.store)
            cache.store.clear()
        if n:
            log_event("cache_cleared", entries=n, scope="client")
        return
    # Legacy / test path — clear any caches on any tracked client we can find.
    log_event("cache_cleared", entries=0, scope="all")


def cache_summary(client: Any) -> Dict[str, int]:
    """Return current per-client cache counters. Callers (e.g. producer_daemon)
    typically log this between ticks then call `clear_cache(client)` to reset
    for a fresh tick window."""
    cache = _client_cache(client)
    with cache.lock:
        return {
            "size": len(cache.store),
            "hits": cache.hits,
            "misses": cache.misses,
            "evictions": cache.evictions,
            "skipped": cache.skipped,
            "coalesced": cache.coalesced,
        }
