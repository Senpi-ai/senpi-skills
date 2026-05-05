"""producer_daemon — long-lived scheduler that fires a producer on a fixed interval.

Replaces `openclaw cron add` + `agentTurn` (LLM per tick) with a plain Python
loop that calls the producer's `run()` directly. No LLM, no node subprocess
forest, no fork-storm risk under overlap.

Each tick is wrapped in `scanner_lock(name)` so:
- A previous tick that took longer than the interval is still running →
  next tick is skipped cleanly.
- A previous tick that crashed → next tick recovers via stale-lock detection.

Per-tick wall-clock timeout via SIGALRM (UNIX). SIGTERM / SIGINT trigger a
graceful drain (current tick finishes, then loop exits).
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import signal
import threading
import time
from contextlib import suppress
from typing import Callable, Optional

from ._logging import log_event
from .lock import scanner_lock


_DEFAULT_TICK_TIMEOUT = 60.0


class _TickTimeout(BaseException):
    """Raised internally when a single tick exceeds its wall-clock budget."""


def _install_shutdown_handlers(stop_event: threading.Event) -> None:
    def handler(signum: int, _frame: object) -> None:
        if not stop_event.is_set():
            log_event("daemon_signal", signum=signum, action="graceful_shutdown_requested")
            stop_event.set()

    with suppress(ValueError):  # only installable on the main thread
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)


def _arm_tick_alarm(seconds: float) -> bool:
    """Install SIGALRM to enforce per-tick wall-clock budget. Returns True if armed."""
    if not hasattr(signal, "SIGALRM"):
        return False

    def alarm_handler(_signum: int, _frame: object) -> None:
        raise _TickTimeout(f"tick exceeded {seconds:g}s")

    try:
        signal.signal(signal.SIGALRM, alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        return True
    except (ValueError, OSError):
        return False


def _disarm_tick_alarm() -> None:
    if hasattr(signal, "SIGALRM"):
        with suppress(ValueError, OSError):
            signal.setitimer(signal.ITIMER_REAL, 0)


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> bool:
    """Sleep up to `seconds`, but return early (False) if stop_event fires."""
    return not stop_event.wait(seconds)


def producer_daemon(
    fn: Callable[[], None],
    interval_seconds: float,
    name: str,
    tick_timeout: Optional[float] = None,
    lock_dir: Optional[str] = None,
    max_ticks: Optional[int] = None,
    install_signal_handlers: bool = True,
) -> int:
    """Run `fn` every `interval_seconds` until SIGTERM / SIGINT, returning tick count.

    Args:
        fn: zero-arg callable. One tick = one call to `fn`.
        interval_seconds: seconds between tick *starts* (not between end and next start).
            If a tick takes longer than the interval, the next tick fires immediately
            after the current one finishes (scanner_lock prevents overlap).
        name: identifier used by `scanner_lock` and log fields.
        tick_timeout: per-tick wall-clock budget in seconds. Default 60s.
            On UNIX, enforced via SIGALRM (raises `_TickTimeout` to abort the call).
            On platforms without SIGALRM, this is best-effort (logged only).
        lock_dir: where the scanner_lock file lives. Defaults to env or /tmp.
        max_ticks: cap on number of ticks (mostly for tests). None = unbounded.
        install_signal_handlers: whether to wire SIGTERM/SIGINT for graceful shutdown.
            False is useful in tests where the test runner installs its own.

    Returns:
        Total number of ticks attempted (succeeded + failed + skipped).
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")
    if tick_timeout is None:
        tick_timeout = _DEFAULT_TICK_TIMEOUT
    if tick_timeout <= 0:
        raise ValueError("tick_timeout must be > 0 if provided")

    stop_event = threading.Event()
    if install_signal_handlers:
        _install_shutdown_handlers(stop_event)

    log_event(
        "daemon_started",
        name=name,
        interval_seconds=interval_seconds,
        tick_timeout=tick_timeout,
        max_ticks=max_ticks,
    )

    tick_count = 0
    start_loop = time.time()

    try:
        while not stop_event.is_set():
            tick_count += 1
            tick_started_at = time.time()
            log_event("daemon_tick_started", name=name, tick=tick_count)

            armed = _arm_tick_alarm(tick_timeout)
            tick_status = "ok"
            tick_err: Optional[str] = None
            try:
                with scanner_lock(name, lock_dir=lock_dir):
                    fn()
            except _TickTimeout as e:
                tick_status = "timeout"
                tick_err = str(e)
            except BlockingIOError:
                # scanner_lock raised — another live process holds the lock.
                tick_status = "skipped_locked"
            except Exception as e:  # noqa: BLE001 — log and keep looping
                tick_status = "error"
                tick_err = f"{type(e).__name__}: {e}"
            finally:
                if armed:
                    _disarm_tick_alarm()

            duration_ms = int((time.time() - tick_started_at) * 1000)
            log_event(
                "daemon_tick_finished",
                name=name,
                tick=tick_count,
                status=tick_status,
                duration_ms=duration_ms,
                **({"error": tick_err} if tick_err else {}),
            )

            if max_ticks is not None and tick_count >= max_ticks:
                break

            # Sleep until the next interval boundary (no drift accumulation).
            elapsed = time.time() - tick_started_at
            sleep_for = max(0.0, interval_seconds - elapsed)
            if sleep_for > 0:
                _interruptible_sleep(sleep_for, stop_event)

    finally:
        log_event(
            "daemon_stopping",
            name=name,
            tick_count=tick_count,
            uptime_seconds=int(time.time() - start_loop),
        )

    return tick_count
