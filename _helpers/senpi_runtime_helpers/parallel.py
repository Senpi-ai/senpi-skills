"""parallel — concurrency-bounded fan-out for independent MCP calls.

Backed by `concurrent.futures.ThreadPoolExecutor` so 1000 calls don't
spawn 1000 OS threads — at most `max_concurrent` worker threads are
created and the executor's internal queue holds the rest.

When queue depth crosses `warn_queue_depth`, emits a warning event so
the producer author notices their tick is hammering MCP.

Stdlib-only.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Optional, Tuple

from . import _config as cfg
from ._logging import log_event


_WARN_THROTTLE_SECONDS = 5.0


class _WarnGate:
    """Throttles parallel_queue_warn events across calls so a producer tick
    that fires several parallel() invocations doesn't flood logs. The gate
    must be module-scoped — a per-call instance would always start with
    `_last_emitted=0.0` and let the very next call through unconditionally.
    Read+write of the last-emitted timestamp is serialized."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_emitted = 0.0

    def should_emit(self) -> bool:
        now = time.time()
        with self._lock:
            if now - self._last_emitted < _WARN_THROTTLE_SECONDS:
                return False
            self._last_emitted = now
            return True


# Module-scoped so the throttle window persists across parallel() calls.
_WARN_GATE = _WarnGate()


def parallel(
    calls: List[Callable[[], Any]],
    max_concurrent: Optional[int] = None,
    warn_queue_depth: Optional[int] = None,
    raise_after_completion: bool = False,
) -> List[Tuple[bool, Any]]:
    """Run independent calls in parallel, concurrency-bounded.

    Args:
        calls: list of zero-arg callables. Each typically wraps a
            `client.mcp_call(...)`.
        max_concurrent: cap on worker threads. The pool's internal
            queue holds calls beyond this; pending calls wait, never
            reject. Defaults to env SENPI_HELPERS_MAX_CONCURRENT.
        warn_queue_depth: emit `parallel_queue_warn` log event when
            this many calls have been submitted to the pool but not
            yet finished. Soft signal; no rejection.
        raise_after_completion: if True, after every call has finished,
            raise the first exception encountered (in input order).
            **Note:** waits for all calls — not fail-fast.

    Returns:
        `List[Tuple[bool, Any]]` — same order as `calls`. Each element
        is `(True, return_value)` on success, `(False, exception)` on
        failure. Tuple shape avoids the footgun where a tool that
        legitimately returns an Exception subclass would be
        misclassified as a failure.
    """
    if not calls:
        return []
    cap = max_concurrent if max_concurrent is not None else cfg.MAX_CONCURRENT
    cap = max(1, cap)
    warn_threshold = warn_queue_depth if warn_queue_depth is not None else cfg.QUEUE_WARN_DEPTH

    submitted_count = len(calls)

    if submitted_count >= warn_threshold and _WARN_GATE.should_emit():
        log_event(
            "parallel_queue_warn",
            queue_depth=submitted_count,
            max_concurrent=cap,
            warn_threshold=warn_threshold,
            note="too many MCP calls submitted in this tick — consider caching or batching",
        )

    started = time.time()

    def worker(idx: int, fn: Callable[[], Any]) -> Tuple[int, bool, Any]:
        try:
            return idx, True, fn()
        except Exception as e:  # noqa: BLE001 — surfaced to caller in tuple
            return idx, False, e

    results: List[Tuple[bool, Any]] = [(False, None)] * submitted_count
    # Manage the pool manually instead of `with ThreadPoolExecutor(...)`.
    # The context manager calls `shutdown(wait=True)` unconditionally on
    # exit, which means a `_TickTimeout` BaseException raised in the main
    # thread by producer_daemon's SIGALRM handler would still block here
    # until every worker's MCP call returned — defeating the per-tick
    # wall-clock budget. We need the budget to be hard.
    pool = ThreadPoolExecutor(max_workers=cap, thread_name_prefix="senpi-parallel")
    exited_cleanly = False
    try:
        futures = [pool.submit(worker, i, fn) for i, fn in enumerate(calls)]
        for fut in futures:
            idx, ok, value = fut.result()
            results[idx] = (ok, value)
            if not ok:
                log_event(
                    "parallel_call_failed",
                    index=idx,
                    error=str(value)[:200] if isinstance(value, BaseException) else None,
                )
        exited_cleanly = True
    finally:
        # Clean exit → wait for any drained workers (there shouldn't be any
        # since fut.result() above already collected each one).
        # Exception (incl. BaseException like _TickTimeout) → cancel queued
        # work and return immediately. In-flight workers continue in the
        # background; they don't write to `results` directly (the main
        # thread does, via the for-loop above) so leaking them is safe.
        # cancel_futures requires Python 3.9+; cfg states 3.10+ is required.
        pool.shutdown(wait=exited_cleanly, cancel_futures=not exited_cleanly)

    duration_ms = int((time.time() - started) * 1000)
    failed = sum(1 for ok, _ in results if not ok)
    log_event(
        "parallel_complete",
        total=submitted_count,
        failed=failed,
        max_concurrent=cap,
        duration_ms=duration_ms,
    )

    if raise_after_completion:
        for ok, v in results:
            if not ok and isinstance(v, BaseException):
                raise v

    return results
