"""Self-describing state files written by `producer_daemon` and read by the
`senpi-helpers` CLI.

Three files per daemon, all under `${SENPI_HELPERS_STATE_DIR}/<name>/`:

- `pid.json`        — pid, start_time_iso, log_path, wallet, scanner,
                       interval_seconds, tick_timeout, version,
                       cmdline_fingerprint, start_time_jiffies. Removed on
                       clean exit; reader checks PID liveness (and recycle
                       guard) when present.
- `boot.json`       — argv, script_path, cwd, env_snapshot, log_path.
                       Everything needed for `senpi-helpers restart` to
                       re-exec. Persists across stop/restart cycles.
- `heartbeat.json`  — last_tick_iso, last_tick_status, last_tick_code,
                       tick_count, error_count. Rewritten after every tick.

All writes are atomic (temp file + `os.replace`). All writes are tolerant —
state-file I/O errors must NEVER crash the daemon; they are logged and the
daemon keeps ticking. Operators trade a missing state file for a running
producer, not the other way around.

The default state dir is `/data/.openclaw/senpi-helpers/` so the files
survive a container restart on Railway (matching where openclaw state lives).
Override with `SENPI_HELPERS_STATE_DIR` on hosts without a `/data` volume.

Schema versioning protocol
--------------------------
Each file has its own `"schema"` integer field. Schemas evolve INDEPENDENTLY
— bumping boot.json's schema does not require touching pid.json. Readers
must tolerate older AND newer schemas (forward-compat), failing soft on
fields they don't recognize.

Current supported schemas:
    pid.json:       {1, 2}    # 2 adds cmdline_fingerprint + start_time_jiffies
    boot.json:      {1, 2}    # 2 adds log_path + interpreter-first argv
    heartbeat.json: {1}

When `_read_json` encounters a schema number outside the supported set, it
logs a one-line warning and returns the data anyway — callers should access
fields defensively (`.get(...)`) rather than assume a particular shape. We
never delete a file just because its schema is unrecognised.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from ._logging import log_event


_DEFAULT_STATE_DIR = "/data/.openclaw/senpi-helpers"


# ─── boot.json schema versioning ────────────────────────────────────────────
#
# Schema 2 (current):
#   argv = [sys.executable, "-u", *sys.argv]
#   - sys.executable is the absolute path to the running interpreter.
#   - "-u" forces unbuffered stdout/stderr on relaunch. The operator
#     playbook launches daemons as `nohup python3 -u script.py &`; we
#     can't capture the original "-u" because interpreter flags are
#     consumed before the script's sys.argv is populated. Hard-coding
#     "-u" on capture is harmless and matches the operator expectation
#     (real-time log lines).
#
# Schema 1 (legacy, pre-2026-05):
#   argv = list(sys.argv)
#   - Missing the interpreter. `manage.relaunch_daemon` then tried to
#     `execve` the .py file directly, which failed with EACCES whenever
#     the script wasn't `+x`. The operator playbook never demanded `+x`
#     because the interpreter was explicit on the original command line.
#   - `manage._normalize_argv` migrates legacy argv on read, prepending
#     `[sys.executable, "-u"]`. The next `write_boot` from the relaunched
#     daemon rewrites the file as schema 2 — migration is one-shot.
#
# Why we don't capture interpreter flags beyond "-u":
#   sys.argv inside Python excludes interpreter flags (`-u`, `-O`,
#   `-X dev`, `-W ...`). They've already been consumed by the interpreter
#   before user code runs and cannot be recovered from inside the script.
#   For producers, only "-u" matters in practice (log readability).
#   Setting PYTHONUNBUFFERED=1 in env has the same effect; operators who
#   need it can put it in their launch env and it will land in
#   env_snapshot below.

_PYTHON_INTERPRETER_RE = re.compile(
    r"^(python|pypy)(\d+(\.\d+)?)?(\.exe)?$"
)


def looks_like_python_interpreter(path: str) -> bool:
    """True if `path`'s basename matches a python/pypy interpreter pattern.

    Matches: python, python3, python3.11, python.exe, python3.exe,
             pypy, pypy3.
    Does NOT match: python3.11-config, python-pip, myscript.py, cat,
                    empty string, anything ending in .py.

    Used by `manage._normalize_argv` to detect legacy boot.json argv
    (where argv[0] is the script, not the interpreter).
    """
    if not path:
        return False
    return bool(_PYTHON_INTERPRETER_RE.match(os.path.basename(path).lower()))

# Env-var prefixes / suffixes that the boot snapshot should capture. Skill
# names (PANGOLIN, KODIAK, ...) follow the per-skill convention each skill
# documents in its own SKILL.md / launch recipe. Auth tokens are always
# excluded — see `_SENSITIVE_KEYS`.
_BOOT_ENV_PREFIXES = (
    "SENPI_HELPERS_",
    "PANGOLIN_",
    "KODIAK_",
    "POLAR_",
    "CHEETAH_",
    "WOLVERINE_",
    "TURBINE_",
)
_BOOT_ENV_SUFFIXES = ("_WALLET", "_WALLET_ADDRESS", "_DECISION_MODEL")
_BOOT_ENV_EXACT = ("WALLET_ADDRESS", "OPENCLAW_WORKSPACE", "STRATEGY_ADDRESS")
_SENSITIVE_KEYS = frozenset({
    "SENPI_AUTH_TOKEN",
    "OPENCLAW_GATEWAY_TOKEN",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
})


# ─── PID-recycle guard ──────────────────────────────────────────────────────
#
# `os.kill(pid, 0)` reports any pid that exists in the kernel's process table
# as "alive" — including a recycled pid pointing at an unrelated process. In
# Docker/Railway containers where pid_max defaults to 32k, recycling on a
# long-running box is plausible. A `senpi-helpers stop` that signals the
# recycled pid would SIGTERM a stranger.
#
# Defence in depth: record two cheap fingerprints in pid.json:
#   - `cmdline_fingerprint`: sha256 of /proc/<pid>/cmdline (the daemon's full
#     argv with NUL separators). Stable across the daemon's lifetime; almost
#     certainly different from any unrelated process that lands on the same
#     pid.
#   - `start_time_jiffies`: field 22 of /proc/<pid>/stat. Monotonically
#     increasing per-process. Two processes with the same pid cannot share
#     this value unless one was killed and another spawned in the same jiffy
#     (essentially impossible).
#
# `pid_alive_and_matches(pid, fingerprint, jiffies)` verifies both before
# returning True. Callers that don't have the fingerprints (older pid.json,
# non-Linux dev hosts) fall back to plain `pid_alive`.


def _read_proc_field(pid: int, field_name: str) -> Optional[str]:
    """Read /proc/<pid>/<field_name>. Returns None on any read failure
    (file missing, permissions, non-Linux)."""
    try:
        with open(f"/proc/{pid}/{field_name}", "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except (OSError, FileNotFoundError):
        return None


def read_proc_environ(pid: int) -> Optional[Dict[str, str]]:
    """Parse `/proc/<pid>/environ` into a dict.

    `/proc/<pid>/environ` is NUL-separated `KEY=value` entries (the
    process's startup env, captured by the kernel at exec time). Returns
    a dict; None if /proc isn't readable (non-Linux, missing pid, permission
    denied). Skips malformed entries (no '=' or invalid key chars) silently
    so a corrupt environ doesn't crash the helper.

    Used by `senpi-helpers start --inherit-env-from <pid|openclaw>` so the
    operator doesn't have to manually `cat /proc/<pid>/environ | tr ...`
    to bootstrap the daemon's env with auth tokens.
    """
    raw = _read_proc_field(pid, "environ")
    if raw is None:
        return None
    out: Dict[str, str] = {}
    for entry in raw.split("\0"):
        if "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        # Defensive: skip keys that aren't valid identifier-like names. A
        # corrupt entry shouldn't poison the final env dict.
        if not key or not all(c.isalnum() or c == "_" for c in key):
            continue
        out[key] = value
    return out


def cmdline_fingerprint_for_pid(pid: int) -> Optional[str]:
    """Hex sha256 of /proc/<pid>/cmdline, or None on Linux-less hosts."""
    raw = _read_proc_field(pid, "cmdline")
    if raw is None:
        return None
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def start_time_jiffies_for_pid(pid: int) -> Optional[int]:
    """Field 22 of /proc/<pid>/stat — the process's start time in jiffies
    since boot. Returns None on Linux-less hosts or unreadable stat file.

    `/proc/<pid>/stat` line format: `pid (comm) state ppid pgrp session ...`.
    The comm field can contain spaces and parens; field 22 is the 21st
    AFTER the closing paren of (comm).
    """
    raw = _read_proc_field(pid, "stat")
    if raw is None:
        return None
    # Find the last `)` so we skip past `comm` reliably.
    rparen = raw.rfind(")")
    if rparen < 0:
        return None
    tail = raw[rparen + 1:].strip().split()
    # After ")": state(1) ppid(2) ... starttime(22). starttime is index 19
    # in the post-paren split (1-indexed → 0-indexed -1 -1 since state was
    # field 3 of the whole line).
    try:
        return int(tail[19])
    except (IndexError, ValueError):
        return None


def pid_alive(pid: Optional[int]) -> bool:
    """Cheap liveness check. Canonical implementation shared by
    `lock.py`, `cli.py`, and `manage.py` — they all need to ask the same
    question ("is this pid alive?") and previously each kept a private copy.

    The basic check is `os.kill(pid, 0)`. On Linux, we ALSO filter out
    zombies (`State: Z`) and dead-pending-reap (`State: X`): `signal(0)`
    succeeds on those because the kernel still has the pid slot, but the
    process has already exited. Without this filter, `stop_pid` would
    poll until its SIGTERM timeout, escalate to SIGKILL (a no-op on the
    already-dead zombie), and falsely return `STOP_KILL_FAILED` after
    the kernel-grace window — even though the daemon DID stop cleanly.

    Production trigger: daemon launched via `nohup … &`, parent shell
    exits, daemon reparented to PID 1. On Railway boxes PID 1 is the
    `node src/server.js` Express wrapper, which doesn't reap orphan
    zombies. Zombies accumulate; the senpi-helpers CLI was previously
    blind to them.

    Treats EPERM as "alive" (process exists, but we lack permission to
    signal it — that case is non-zero on shared-host setups where the
    daemon runs under a different uid). Returns False on None / 0 /
    negative / non-int inputs so callers don't have to type-guard before
    passing the pid from `pid.json` (which can be None on schema drift).
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    # signal(0) succeeded — process exists in the kernel's table. On Linux,
    # consult /proc/<pid>/status to filter zombies. Non-Linux: trust signal(0).
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    # Example: "State:\tZ (zombie)" — split gives ["State:", "Z", ...].
                    state_char = line.split()[1] if len(line.split()) >= 2 else ""
                    return state_char not in ("Z", "X")
    except (FileNotFoundError, PermissionError, OSError):
        # /proc not available (non-Linux) or pid raced exit + reap. Trust signal(0).
        pass
    return True


def pid_alive_and_matches(
    pid: Optional[int],
    *,
    expected_fingerprint: Optional[str] = None,
    expected_jiffies: Optional[int] = None,
) -> bool:
    """Strict liveness check that guards against pid recycling.

    Returns True iff:
      - `pid_alive(pid)` (pid exists in kernel's process table), AND
      - if `expected_fingerprint` was given, /proc/<pid>/cmdline hashes to
        the same value, AND
      - if `expected_jiffies` was given, /proc/<pid>/stat field 22 is the
        same.

    When `expected_*` are None (older pid.json before this fingerprinting
    landed, or a non-Linux dev host) the function degrades to `pid_alive`.
    Behavior preserved across the schema bump — callers can opt in gradually.
    """
    if not pid_alive(pid):
        return False
    assert isinstance(pid, int)
    if expected_fingerprint is not None:
        actual = cmdline_fingerprint_for_pid(pid)
        if actual is not None and actual != expected_fingerprint:
            return False  # different process; pid recycled
    if expected_jiffies is not None:
        actual = start_time_jiffies_for_pid(pid)
        if actual is not None and actual != expected_jiffies:
            return False
    return True


def _now_iso() -> str:
    """UTC ISO-8601 with millisecond precision, matching `_logging.py`."""
    now = time.time()
    ms = int((now - int(now)) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{ms:03d}Z"


def get_state_dir(state_dir: Optional[str] = None) -> Path:
    """Resolve the state directory. Explicit override > env var > default.

    Env var is re-read on every call so tests can monkeypatch
    `os.environ` without reimporting the module.
    """
    if state_dir:
        return Path(state_dir)
    return Path(os.environ.get("SENPI_HELPERS_STATE_DIR", _DEFAULT_STATE_DIR))


def daemon_dir(name: str, state_dir: Optional[str] = None) -> Path:
    """Per-daemon subdir. Created on first write."""
    return get_state_dir(state_dir) / name


# ─── Log path discovery ─────────────────────────────────────────────────────


def detect_log_path() -> Optional[str]:
    """Best-effort discovery of where the daemon's stderr is redirected.

    Priority:
      1. `SENPI_HELPERS_LOG_PATH` env var (operator-set explicit override).
      2. `/proc/self/fd/2` symlink target — works on Linux when the daemon
         was started with `nohup python3 -u … > /tmp/foo.log 2>&1 &`
         (the canonical redirect-to-file launch form).

    Returns None when stderr is not redirected to a regular file (e.g.
    interactive terminal, pipe, socket, deleted-file fd). Callers (the
    daemon's startup, `senpi-helpers stats`) should fall back to
    `/tmp/<name>.log` and warn rather than silently degrade.
    """
    env_override = os.environ.get("SENPI_HELPERS_LOG_PATH", "").strip()
    if env_override:
        return env_override
    try:
        target = os.readlink("/proc/self/fd/2")
    except (OSError, FileNotFoundError):
        return None
    # Drop non-file destinations. `/proc/self/fd/2` resolves to strings like
    # `/dev/pts/0` (TTY), `pipe:[123]`, `socket:[…]`, or `<path> (deleted)`.
    if not target.startswith("/"):
        return None
    if target.startswith("/dev/") or target.startswith("/proc/"):
        return None
    if target.endswith(" (deleted)"):
        return None
    return target


def ensure_daemon_stderr_redirected(name: str) -> Optional[str]:
    """Ensure the daemon's stderr/stdout point at a regular file.

    Called once at daemon startup, BEFORE write_pid / write_boot. If the
    daemon was launched without explicit redirection (e.g. by openclaw's
    `exec` tool, whose fd 2 is a pipe back to the agent), the helpers
    state files would record `log_path = None`, breaking `senpi-helpers
    stats` and `restart` for the rest of the daemon's life.

    Behavior:
      - If stderr already points at a real file, leave it alone and return
        that path. This is the operator playbook case
        (`nohup python3 -u … > /tmp/foo.log 2>&1 &`).
      - Otherwise, on Linux open `/tmp/<name>.log` in append mode and dup2
        it onto fd 1 and fd 2 so the existing file handle is preserved by
        the kernel after we close ours. Return that path.
      - On non-Linux (dev/macOS), return None without rewriting fds. The
        path that motivates this function — openclaw on Linux containers
        with stderr-is-a-pipe — only exists in production, and dup2'ing
        in a unit-test or interactive shell would silently capture the
        test runner's own stdout. Production behavior is preserved; dev
        UX is the deliberate trade-off.
      - If even the fallback can't be opened (read-only /tmp, permissions),
        return None and let the caller log a state_write_failed-style event.

    The fallback path is deterministic so operators can find it without
    consulting state files. The /tmp/<name>.log pattern is also what the
    error message in `cmd_restart` already documents.
    """
    existing = detect_log_path()
    if existing:
        return existing

    # Linux-only: dup2'ing stdout/stderr in a non-daemon process (e.g. a
    # unit-test runner) would hijack the parent's output. detect_log_path
    # also relies on /proc/self/fd/2, which only exists on Linux — so on
    # dev machines we return None and let callers handle it explicitly.
    if not sys.platform.startswith("linux"):
        return None

    fallback = f"/tmp/{name}.log"
    try:
        # Open append so multiple daemon starts under the same name don't
        # truncate each other's history.
        fd = os.open(fallback, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError as e:
        log_event(
            "ensure_log_path_failed",
            path=fallback,
            error=f"{type(e).__name__}: {e}"[:200],
        )
        return None
    try:
        os.dup2(fd, 1)  # stdout
        os.dup2(fd, 2)  # stderr
    finally:
        # If stdin/stdout/stderr were closed by the caller (e.g. launched
        # via `python3 script.py >&- 2>&-`), os.open could return fd 0, 1,
        # or 2 as the lowest free descriptor. After our dup2 onto 1 and 2,
        # those descriptors point at the log file we just opened.
        # Unconditionally closing `fd` in that case would close the log
        # file out from under our redirected streams. Guard: only close
        # our temporary fd if it's NOT one of the standard streams.
        if fd > 2:
            os.close(fd)
    log_event(
        "log_path_fallback_applied",
        name=name,
        log_path=fallback,
        reason="stderr was not a regular file at daemon startup",
    )
    return fallback


# ─── Env snapshot ───────────────────────────────────────────────────────────


def _sanitized_env_snapshot() -> Dict[str, str]:
    """Filtered env-var snapshot for `boot.json`.

    Captures only the keys an operator would want for debugging a restart —
    skill wallets, decision-model picks, helper config. Excludes auth
    tokens and anything else that would be a credential leak if `boot.json`
    were shared in a bug report.

    This snapshot is informational only — `restart` re-execs under the
    CLI's CURRENT process env, not the captured snapshot. The captured
    env helps the operator notice "I restarted but a wallet var was
    different now" — it is never trusted as authoritative.
    """
    out: Dict[str, str] = {}
    for k, v in os.environ.items():
        if k in _SENSITIVE_KEYS:
            continue
        if (
            k.startswith(_BOOT_ENV_PREFIXES)
            or k.endswith(_BOOT_ENV_SUFFIXES)
            or k in _BOOT_ENV_EXACT
        ):
            out[k] = v
    return dict(sorted(out.items()))


# ─── Atomic write + tolerant error handling ─────────────────────────────────


def _atomic_write(path: Path, payload: Dict[str, Any]) -> bool:
    """Write `payload` as JSON to `path` via temp file + `os.replace`.

    Returns True on success, False on failure. Failures are logged but
    NEVER raised: the daemon must keep ticking even if its state dir
    has gone read-only or the volume is full. The state files are
    observability — they are not on the critical signal-emission path.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log_event(
            "state_write_failed",
            path=str(path),
            stage="mkdir",
            error=f"{type(e).__name__}: {e}"[:200],
        )
        return False

    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    except OSError as e:
        log_event(
            "state_write_failed",
            path=str(path),
            stage="mkstemp",
            error=f"{type(e).__name__}: {e}"[:200],
        )
        return False

    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, str(path))
        return True
    except OSError as e:
        log_event(
            "state_write_failed",
            path=str(path),
            stage="write_replace",
            error=f"{type(e).__name__}: {e}"[:200],
        )
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


# What schemas each file may legitimately have. Extend this set when bumping;
# readers warn loudly on values outside it so future migrations are visible.
_SUPPORTED_SCHEMAS: Dict[str, FrozenSet[int]] = {
    "pid.json": frozenset({1, 2}),
    "boot.json": frozenset({1, 2}),
    "heartbeat.json": frozenset({1}),
}


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    # Soft schema check: warn but still return. Callers use .get(...) and
    # tolerate missing fields, so a future-schema-2-on-old-CLI scenario
    # degrades gracefully instead of crashing.
    expected = _SUPPORTED_SCHEMAS.get(path.name)
    if expected is not None:
        actual = data.get("schema")
        if actual is not None and actual not in expected:
            log_event(
                "state_unknown_schema",
                path=str(path),
                got_schema=actual,
                supported=sorted(expected),
            )
    return data


# ─── Public API: write ──────────────────────────────────────────────────────


def write_pid(
    name: str,
    *,
    wallet: Optional[str],
    scanner: Optional[str],
    interval_seconds: float,
    tick_timeout: float,
    log_path: Optional[str],
    version: str,
    state_dir: Optional[str] = None,
) -> bool:
    """Write `pid.json` for `name`. Idempotent — overwrites any prior file.

    Called once at daemon start, after argument validation. The file is
    deleted on clean exit via `clear_pid` so a missing `pid.json` is the
    canonical "not running" signal for the CLI.

    Schema 2 (current) adds `cmdline_fingerprint` + `start_time_jiffies`
    so the CLI can detect pid recycling — see `pid_alive_and_matches`.
    Schema 1 (legacy) lacked these; readers degrade to plain pid_alive.
    """
    pid = os.getpid()
    payload = {
        "schema": 2,
        "name": name,
        "pid": pid,
        "start_time_iso": _now_iso(),
        "wallet": wallet,
        "scanner": scanner,
        "interval_seconds": interval_seconds,
        "tick_timeout": tick_timeout,
        "log_path": log_path,
        "version": version,
        # Fingerprints — both None on non-Linux dev hosts; callers tolerate.
        "cmdline_fingerprint": cmdline_fingerprint_for_pid(pid),
        "start_time_jiffies": start_time_jiffies_for_pid(pid),
    }
    return _atomic_write(daemon_dir(name, state_dir) / "pid.json", payload)


def write_boot(name: str, *, state_dir: Optional[str] = None) -> bool:
    """Write `boot.json` for `name`. Captures full launch shape (schema 2).

    Called once at daemon start, before the producer's user code runs, so
    sys.argv reflects the original launch and can't have been mutated.
    `senpi-helpers restart` (and `start`) reads this file to re-exec the
    producer.

    Schema 2 captures argv as `[sys.executable, "-u", *sys.argv]` so the
    relaunch can `Popen(argv)` without needing the script to be `+x`.
    See the schema-versioning comment near the top of this file for the
    full rationale (in particular: why "-u" is hard-coded and why we
    don't capture other interpreter flags).

    Schema 2 also records `log_path` here (in addition to pid.json). The
    daemon's `pid.json` is removed on graceful exit, so restart/stats
    have nowhere to find log_path otherwise. boot.json persists, so the
    relaunch path always has a log target.
    """
    script_path = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else None
    # Prepend interpreter + "-u" so the relaunch path doesn't require the
    # script to be executable. If sys.argv is empty (extremely unusual —
    # we still want a sensible boot.json), record just the interpreter.
    if sys.argv:
        argv = [sys.executable, "-u", *sys.argv]
    else:
        argv = [sys.executable, "-u"]
    payload = {
        "schema": 2,
        "name": name,
        "argv": argv,
        "script_path": script_path,
        "cwd": os.getcwd(),
        "env_snapshot": _sanitized_env_snapshot(),
        "log_path": detect_log_path(),
        "captured_at_iso": _now_iso(),
    }
    return _atomic_write(daemon_dir(name, state_dir) / "boot.json", payload)


def write_heartbeat(
    name: str,
    *,
    tick: int,
    status: str,
    code: Optional[str],
    duration_ms: int,
    error: Optional[str],
    tick_count: int,
    error_count: int,
    state_dir: Optional[str] = None,
) -> bool:
    """Write `heartbeat.json` for `name`. Called after every tick.

    `tick_count` is the total ticks attempted by this daemon process so far
    (includes ok / error / timeout / skipped_locked). `error_count` is the
    subset where `status != "ok"`.
    """
    payload = {
        "schema": 1,
        "name": name,
        "last_tick_iso": _now_iso(),
        "last_tick": tick,
        "last_tick_status": status,
        "last_tick_code": code,
        "last_tick_duration_ms": duration_ms,
        "last_tick_error": error,
        "tick_count": tick_count,
        "error_count": error_count,
    }
    return _atomic_write(daemon_dir(name, state_dir) / "heartbeat.json", payload)


def clear_pid(name: str, *, state_dir: Optional[str] = None) -> None:
    """Remove `pid.json` for `name` on clean daemon exit.

    Best-effort — silent on FileNotFoundError (already gone) and OSError
    (volume unmounted). The CLI's `health` falls back to PID-liveness
    detection if `pid.json` exists for a dead PID.
    """
    p = daemon_dir(name, state_dir) / "pid.json"
    try:
        p.unlink()
    except (FileNotFoundError, OSError):
        pass


# ─── Public API: read ───────────────────────────────────────────────────────


def read_pid(name: str, *, state_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return _read_json(daemon_dir(name, state_dir) / "pid.json")


def read_boot(name: str, *, state_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return _read_json(daemon_dir(name, state_dir) / "boot.json")


def read_heartbeat(name: str, *, state_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return _read_json(daemon_dir(name, state_dir) / "heartbeat.json")


def list_daemons(*, state_dir: Optional[str] = None) -> List[str]:
    """List daemon names — one per subdirectory of the state dir.

    Returns an empty list when the state dir does not exist (no daemon
    has ever started under this host). Sorted for stable CLI output.
    """
    base = get_state_dir(state_dir)
    try:
        return sorted(p.name for p in base.iterdir() if p.is_dir())
    except (FileNotFoundError, OSError):
        return []
