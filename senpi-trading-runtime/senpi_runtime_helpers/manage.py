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
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .state import (
    pid_alive as is_pid_alive,  # canonical PID-liveness check
    looks_like_python_interpreter,  # heuristic for legacy argv normalization
)


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


# ─── Relaunch (the back half of `restart`) ──────────────────────────────────
#
# `stop_pid` (above) is called with the OLD daemon's pid (read from pid.json).
# It targets a SPECIFIC pid and CANNOT signal the helper process itself —
# the helper's pid is never recorded in pid.json. So `restart` cannot
# accidentally kill itself by PID. End-to-end coverage of this invariant is
# in tests/test_restart_integration.py::test_restart_does_not_target_self_pid.


# Relaunch outcome codes. Stable strings for JSON consumers.
RELAUNCH_OK = "relaunched"
RELAUNCH_SCRIPT_MISSING = "script_missing"
RELAUNCH_LOG_OPEN_FAILED = "log_open_failed"
RELAUNCH_SPAWN_FAILED = "spawn_failed"


def _normalize_argv(argv: List[str]) -> Tuple[List[str], bool]:
    """Migrate legacy boot.json argv (script-only) to interpreter-first form.

    Schema 1 (legacy):  ["/path/script.py"]
    Schema 2 (modern):  [sys.executable, "-u", "/path/script.py"]

    Returns `(normalized_argv, was_normalized)`.

    Migration rule: if argv[0] doesn't look like a python interpreter AND
    it ends with `.py`, prepend `[sys.executable, "-u"]`. Idempotent — a
    modern argv passes through unchanged. Non-.py argv[0] also passes
    through (we don't try to "fix" launches we don't recognize).

    Why this is needed: the operator playbook launches daemons as
    `nohup python3 -u script.py &`. sys.argv inside that python is just
    ["/path/script.py"] — interpreter and "-u" aren't observable from
    inside the script. Schema-1 boot.json captured only sys.argv, inheriting
    that gap. Popen([".py"]) requires the script to be `+x`, which the
    operator's launch never required (interpreter was explicit on the
    command line). We can't go back and fix existing boot.json on the
    production fleet — so we migrate on read.

    The new daemon, once it starts, calls `write_boot` and writes a fresh
    schema-2 boot.json; migration is one-shot per daemon.
    """
    if not argv:
        return argv, False
    first = argv[0]
    if looks_like_python_interpreter(first):
        return argv, False
    if first.endswith(".py"):
        return [sys.executable, "-u", *argv], True
    # Non-.py, non-python argv[0]: pass through. Could be a compiled binary
    # or a wrapper script that DOES have +x. Not our place to second-guess.
    return argv, False


def relaunch_daemon(
    *,
    argv: List[str],
    cwd: Optional[str],
    log_path: str,
    env: Optional[Dict[str, str]] = None,
    popen_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Re-exec a daemon as a detached process. Used by `restart`.

    Args:
        argv: the daemon's argv (recorded in boot.json). For schema-2 boot
            files this is `[interpreter, "-u", script, ...args]`; for
            schema-1 (legacy) it's `[script, ...args]`. Legacy argv is
            migrated transparently via `_normalize_argv`.
        cwd: working directory for the new process (recorded in boot.json).
            None falls back to the current process's CWD.
        log_path: path to the daemon's stderr log file. Opened in append
            mode and used for BOTH stdout and stderr of the new process,
            so the new daemon's events land in the same file the previous
            one did — `senpi-helpers stats` continues to work without
            re-detection.
        env: environment for the new process. None = inherit caller's
            env (the canonical path for `restart`, so wallet / auth /
            decision-model changes since the daemon was started take
            effect). Tests pass an explicit dict.
        popen_factory: dependency-injected Popen for testability.

    Detached via `start_new_session=True` (POSIX setsid) so the new
    process survives the CLI's exit. `stdin=DEVNULL` to prevent the
    daemon from blocking on a closed parent stdin.

    Returns a result dict with outcome, pid, error, argv_normalized,
    argv_used. `argv_used` is what Popen was actually called with — a
    schema-1→2 migration is observable to callers via `argv_normalized`.
    """
    # Resolve popen_factory at call time so test monkeypatches of
    # subprocess.Popen take effect. The historical default-arg form bound
    # subprocess.Popen at import time and silently bypassed test mocks.
    if popen_factory is None:
        popen_factory = subprocess.Popen

    if not argv:
        return {"outcome": RELAUNCH_SPAWN_FAILED, "pid": None,
                "error": "argv is empty",
                "argv_normalized": False, "argv_used": []}

    # Existence check: argv[0] may be the script (schema 1) or the
    # interpreter (schema 2). Resolve it against the daemon's recorded
    # `cwd` BEFORE checking — Popen will run with that cwd, so a relative
    # path like `./producer.py` is valid relative to it even though it
    # wouldn't exist relative to the CLI's current cwd. Falling back to
    # os.getcwd() preserves the old behavior when cwd is None.
    argv0 = argv[0]
    if not os.path.isabs(argv0):
        argv0 = os.path.normpath(os.path.join(cwd or os.getcwd(), argv0))
    if not os.path.exists(argv0):
        return {"outcome": RELAUNCH_SCRIPT_MISSING, "pid": None,
                "error": f"argv[0] not found on disk: {argv0}",
                "argv_normalized": False, "argv_used": list(argv)}

    normalized_argv, was_normalized = _normalize_argv(argv)

    try:
        # Open log file BEFORE spawning so an unwritable log path surfaces
        # before we've half-launched a daemon with no place to send output.
        log_fd = open(log_path, "a")
    except OSError as e:
        return {"outcome": RELAUNCH_LOG_OPEN_FAILED, "pid": None,
                "error": f"cannot open {log_path}: {e}",
                "argv_normalized": was_normalized,
                "argv_used": list(normalized_argv)}

    proc = None
    spawn_error: Optional[str] = None
    try:
        proc = popen_factory(
            normalized_argv,
            cwd=cwd or None,
            env=env if env is not None else os.environ.copy(),
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as e:  # noqa: BLE001 — function contract: every failure
        # mode encoded in the result dict, so a stray TypeError from a
        # bad-args caller (or any other exception subclass we didn't list)
        # must NOT escape and crash `cmd_restart`. Broader catch over
        # (OSError, ValueError) closes that gap.
        spawn_error = f"Popen failed: {e}"
    finally:
        # Popen dup'd the fd into the child; close our reference exactly
        # once. Single close site means the error path doesn't have to
        # remember to close before returning — `finally` handles all paths
        # (success, spawn failure, unexpected exception subclass).
        try:
            log_fd.close()
        except OSError:
            pass

    if spawn_error is not None:
        return {"outcome": RELAUNCH_SPAWN_FAILED, "pid": None,
                "error": spawn_error,
                "argv_normalized": was_normalized,
                "argv_used": list(normalized_argv)}
    return {"outcome": RELAUNCH_OK, "pid": proc.pid, "error": None,
            "argv_normalized": was_normalized,
            "argv_used": list(normalized_argv)}
