"""Process-control primitives used by `senpi-helpers stop` / `restart`.

Lives separately from `cli.py` so the control logic can be unit-tested
without going through argparse.

Design:

- **Poll, don't sleep.** `stop_pid` sends SIGTERM and polls every
  `poll_interval` seconds for the process to exit. If the daemon stops
  in 2 seconds, the function returns in ~2 seconds — the timeout is a
  ceiling, not a fixed wait.
- **Escalate to SIGKILL on timeout.** SIGKILL cannot be caught, so it
  is the last resort. Use a short post-SIGKILL grace window to confirm
  the process actually exits (kernel reaps after the signal is
  delivered, not synchronously when sent).
- **Pure return values.** All outcomes are encoded in the dict so
  callers (the CLI, future automation) can branch without parsing
  human text.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import os
import signal
import time
from typing import Any, Dict, Optional


# How long to wait after SIGKILL before declaring the process is unkillable.
# The kernel reaps immediately but kill() returns before the post-reap state
# is observable; a short poll window catches reasonably-fast cleanup without
# blocking the operator for tens of seconds on system-level pathology.
_POST_KILL_GRACE_SECONDS = 5.0


# Stop outcome codes. Stable strings so `stop --json` consumers can branch
# without checking exit codes.
STOP_ALREADY_DEAD = "already_dead"
STOP_TERM_OK = "stopped_via_sigterm"
STOP_KILL_OK = "stopped_via_sigkill"
STOP_KILL_FAILED = "still_alive_after_sigkill"
STOP_PERMISSION_DENIED = "permission_denied"
STOP_INVALID_PID = "invalid_pid"


def is_pid_alive(pid: Optional[int]) -> bool:
    """Cheap liveness check via signal(0). Mirrors `lock.py._process_alive` and
    `cli._is_pid_alive` — duplicated here so this module has no `cli` dep."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def stop_pid(
    pid: int,
    *,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.25,
) -> Dict[str, Any]:
    """Stop process `pid`. SIGTERM first, poll, escalate to SIGKILL on timeout.

    Args:
        pid: target process id.
        timeout_seconds: ceiling on the SIGTERM wait. If the process is
            still alive after this many seconds, SIGKILL is sent. Default
            30 s — long enough for the daemon's longest tick to finish on
            most producers but short enough that an unresponsive process
            doesn't block the operator for a full tick_timeout window.
        poll_interval: how often to check liveness during the wait.
            Default 250 ms — fast enough that a quick exit returns in
            ~250 ms; cheap enough that the loop is negligible CPU.

    Returns a dict with:
        outcome:        one of STOP_* constants
        elapsed_seconds: float, total wall-clock taken
        sigterm_sent:   bool
        sigkill_sent:   bool
        error:          optional string, populated for non-success outcomes

    Never raises — every failure mode is reflected in the `outcome` key.
    """
    result: Dict[str, Any] = {
        "outcome": "",
        "elapsed_seconds": 0.0,
        "sigterm_sent": False,
        "sigkill_sent": False,
        "error": None,
    }

    if not isinstance(pid, int) or pid <= 0:
        result["outcome"] = STOP_INVALID_PID
        result["error"] = f"invalid pid: {pid!r}"
        return result

    if not is_pid_alive(pid):
        result["outcome"] = STOP_ALREADY_DEAD
        return result

    started = time.time()

    # ─── Phase 1: SIGTERM + poll ────────────────────────────────────────
    try:
        os.kill(pid, signal.SIGTERM)
        result["sigterm_sent"] = True
    except ProcessLookupError:
        # Lost a race — process exited between our liveness check and the
        # signal. Treat as success since the daemon is, in fact, stopped.
        result["outcome"] = STOP_ALREADY_DEAD
        result["elapsed_seconds"] = round(time.time() - started, 3)
        return result
    except PermissionError as e:
        result["outcome"] = STOP_PERMISSION_DENIED
        result["error"] = str(e)
        return result

    deadline = started + timeout_seconds
    while time.time() < deadline:
        if not is_pid_alive(pid):
            result["outcome"] = STOP_TERM_OK
            result["elapsed_seconds"] = round(time.time() - started, 3)
            return result
        time.sleep(poll_interval)

    # ─── Phase 2: SIGKILL escalation ────────────────────────────────────
    try:
        os.kill(pid, signal.SIGKILL)
        result["sigkill_sent"] = True
    except ProcessLookupError:
        # Process died between the timeout and the SIGKILL — treat as
        # SIGTERM success (the polling loop just missed it).
        result["outcome"] = STOP_TERM_OK
        result["elapsed_seconds"] = round(time.time() - started, 3)
        return result
    except PermissionError as e:
        result["outcome"] = STOP_PERMISSION_DENIED
        result["error"] = str(e)
        return result

    # Brief poll window for the kernel to reap the killed process.
    kill_deadline = time.time() + _POST_KILL_GRACE_SECONDS
    while time.time() < kill_deadline:
        if not is_pid_alive(pid):
            result["outcome"] = STOP_KILL_OK
            result["elapsed_seconds"] = round(time.time() - started, 3)
            return result
        time.sleep(poll_interval)

    # SIGKILL went out and the process is STILL alive. Kernel-level
    # pathology — zombie reaping stuck, kernel-uninterruptible state, etc.
    # Surface clearly; no further action the CLI can take.
    result["outcome"] = STOP_KILL_FAILED
    result["elapsed_seconds"] = round(time.time() - started, 3)
    result["error"] = (
        "process still alive after SIGKILL + grace period; "
        "may be stuck in uninterruptible system call"
    )
    return result


def stop_outcome_was_success(outcome: str) -> bool:
    """Did the stop result in the process being not-running?

    Used by the CLI to decide exit code (0 = success). The only failure
    outcomes are PERMISSION_DENIED, INVALID_PID, and KILL_FAILED.
    """
    return outcome in (STOP_ALREADY_DEAD, STOP_TERM_OK, STOP_KILL_OK)
