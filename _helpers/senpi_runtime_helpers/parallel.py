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
    """Throttles parallel_queue_warn events so a single tick with N >> cap
    calls doesn't flood logs. Read+write of the last-emitted timestamp is
    serialized."""

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

    warn_gate = _WarnGate()
    submitted_count = len(calls)

    if submitted_count >= warn_threshold and warn_gate.should_emit():
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
    with ThreadPoolExecutor(max_workers=cap, thread_name_prefix="senpi-parallel") as pool:
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
