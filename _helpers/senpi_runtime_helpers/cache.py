"""tick_cache — per-process MCP response memoization with TTL.

Two scanners often query the same MCP tool (e.g. clearinghouse_state)
multiple times per tick. The cache holds responses for `TICK_CACHE_TTL`
seconds so duplicate calls within a tick reuse the result.

`clear_cache()` resets across ticks if the producer wants explicit boundaries.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import hashlib
import json
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from . import _config as cfg
from ._logging import log_event


_lock = threading.Lock()
_store: Dict[str, Tuple[float, Any]] = {}


def _make_key(tool: str, kwargs: Dict[str, Any]) -> str:
    try:
        encoded = json.dumps(kwargs, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = repr(sorted(kwargs.items()))
    h = hashlib.sha1(f"{tool}|{encoded}".encode("utf-8")).hexdigest()[:16]
    return f"{tool}:{h}"


def cached_mcp_call(
    client: Any,
    tool: str,
    ttl: Optional[float] = None,
    timeout: Optional[float] = None,
    **arguments: Any,
) -> Any:
    """Call `client.mcp_call(tool, **arguments)` with per-process memoization.

    Args:
        client: a `SenpiClient` instance (or anything with `mcp_call(tool, **kwargs)`).
        tool: MCP tool name.
        ttl: cache freshness in seconds. Defaults to env SENPI_HELPERS_TICK_CACHE_TTL.
        timeout: forwarded to `client.mcp_call`.
        **arguments: forwarded to `client.mcp_call`.
    """
    ttl_value = ttl if ttl is not None else cfg.TICK_CACHE_TTL
    key = _make_key(tool, arguments)
    now = time.time()

    with _lock:
        entry = _store.get(key)
        if entry is not None and now - entry[0] <= ttl_value:
            log_event("cache_hit", tool=tool, age_s=round(now - entry[0], 2))
            return entry[1]

    value = client.mcp_call(tool, timeout=timeout, **arguments)
    with _lock:
        _store[key] = (time.time(), value)
    log_event("cache_miss", tool=tool, ttl_s=ttl_value)
    return value


def tick_cache(client: Any, ttl: Optional[float] = None) -> Callable[..., Any]:
    """Returns a callable that wraps `client.mcp_call` with per-process TTL cache.

    Example:
        from senpi_runtime_helpers import SenpiClient, tick_cache

        client = SenpiClient()
        mcp = tick_cache(client)
        markets = mcp("leaderboard_get_markets", limit=100)  # MCP call
        markets = mcp("leaderboard_get_markets", limit=100)  # cache hit

    The returned `call(tool, timeout=None, **arguments)` invokes
    `cached_mcp_call(client, tool, ttl=ttl, timeout=timeout, **arguments)`.
    Repeated calls with identical (tool, arguments) return the cached value
    until `ttl` elapses or `clear_cache()` is invoked.
    """
    def call(tool: str, timeout: Optional[float] = None, **arguments: Any) -> Any:
        return cached_mcp_call(client, tool, ttl=ttl, timeout=timeout, **arguments)
    return call


def clear_cache() -> None:
    """Drop all cached responses. Call between independent ticks if you want explicit boundaries."""
    with _lock:
        n = len(_store)
        _store.clear()
    if n:
        log_event("cache_cleared", entries=n)
