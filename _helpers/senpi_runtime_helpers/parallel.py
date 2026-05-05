"""parallel — concurrency-bounded fan-out for independent MCP calls.

Beyond `max_concurrent` in-flight calls, additional calls **queue** (never reject).
When queue depth crosses `warn_queue_depth`, emits a warning event so the
producer author notices their tick is hammering MCP. Author can then add
caching, batching, or coarser granularity.

Pure threads + Semaphore — no asyncio dependency, works in every Python script.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import threading
import time
from typing import Any, Callable, List, Optional, Tuple

from . import _config as cfg
from ._logging import log_event


class _BoundedExecutor:
    """Threaded fan-out with a semaphore. Tasks queue freely; concurrency is capped."""

    def __init__(self, max_concurrent: int, warn_queue_depth: int) -> None:
        self.max_concurrent = max(1, max_concurrent)
        self.warn_queue_depth = warn_queue_depth
        self._sem = threading.Semaphore(self.max_concurrent)
        self._in_flight = 0
        self._waiting = 0
        self._lock = threading.Lock()
        self._last_warn_emitted = 0.0

    def _adjust(self, in_flight_delta: int = 0, waiting_delta: int = 0) -> None:
        """Mutate the in-flight / waiting counters. Use `_snapshot` to read."""
        with self._lock:
            self._in_flight += in_flight_delta
            self._waiting += waiting_delta

    def _adjust_and_snapshot(self, in_flight_delta: int = 0, waiting_delta: int = 0) -> Tuple[int, int]:
        """Mutate then return current (in_flight, waiting). One critical section."""
        with self._lock:
            self._in_flight += in_flight_delta
            self._waiting += waiting_delta
            return self._in_flight, self._waiting

    def _maybe_warn(self, waiting: int) -> None:
        if waiting < self.warn_queue_depth:
            return
        now = time.time()
        if now - self._last_warn_emitted < 5.0:
            return
        self._last_warn_emitted = now
        log_event(
            "parallel_queue_warn",
            queue_depth=waiting,
            max_concurrent=self.max_concurrent,
            warn_threshold=self.warn_queue_depth,
            note="too many MCP calls queued in this tick — consider caching or batching",
        )

    def run_call(self, fn: Callable[[], Any]) -> Tuple[bool, Any]:
        """Run a single call. Acquires semaphore (queues if needed), runs, releases."""
        _in_flight, waiting = self._adjust_and_snapshot(waiting_delta=1)
        self._maybe_warn(waiting)
        self._sem.acquire()
        try:
            self._adjust(in_flight_delta=1, waiting_delta=-1)
            try:
                result = fn()
                return True, result
            except Exception as e:  # noqa: BLE001 — surfaced to caller in tuple
                return False, e
        finally:
            self._adjust(in_flight_delta=-1)
            self._sem.release()


def parallel(
    calls: List[Callable[[], Any]],
    max_concurrent: Optional[int] = None,
    warn_queue_depth: Optional[int] = None,
    raise_first_exception: bool = False,
) -> List[Any]:
    """Run independent calls in parallel, concurrency-bounded.

    Args:
        calls: list of zero-arg callables. Each typically wraps a `client.mcp_call(...)`.
        max_concurrent: cap on in-flight calls. Defaults to env SENPI_HELPERS_MAX_CONCURRENT.
        warn_queue_depth: emit warning when >= this many calls queue. Defaults from env.
        raise_first_exception: if True, raise the first exception any call raised.
            If False (default), exceptions are returned in-place at the corresponding index.

    Returns:
        List of results in the same order as `calls`. Failed calls return the
        Exception object (when `raise_first_exception=False`).
    """
    if not calls:
        return []
    cap = max_concurrent if max_concurrent is not None else cfg.MAX_CONCURRENT
    warn = warn_queue_depth if warn_queue_depth is not None else cfg.QUEUE_WARN_DEPTH
    executor = _BoundedExecutor(cap, warn)

    results: List[Any] = [None] * len(calls)
    threads: List[threading.Thread] = []
    started = time.time()

    def worker(idx: int, fn: Callable[[], Any]) -> None:
        ok, value = executor.run_call(fn)
        results[idx] = value
        if not ok:
            log_event(
                "parallel_call_failed",
                index=idx,
                error=str(value)[:200] if isinstance(value, BaseException) else None,
            )

    for i, fn in enumerate(calls):
        t = threading.Thread(target=worker, args=(i, fn), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    duration_ms = int((time.time() - started) * 1000)
    failed = sum(1 for r in results if isinstance(r, BaseException))
    log_event(
        "parallel_complete",
        total=len(calls),
        failed=failed,
        max_concurrent=cap,
        duration_ms=duration_ms,
    )

    if raise_first_exception:
        for r in results:
            if isinstance(r, BaseException):
                raise r

    return results
