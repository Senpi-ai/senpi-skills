"""Self-describing state files written by `producer_daemon` and read by the
`senpi-helpers` CLI.

Three files per daemon, all under `${SENPI_HELPERS_STATE_DIR}/<name>/`:

- `pid.json`        — pid, start_time_iso, log_path, wallet, scanner,
                       interval_seconds, tick_timeout, version. Removed on
                       clean exit; reader checks PID liveness when present.
- `boot.json`       — argv, script_path, cwd, env_snapshot. Everything
                       needed for `senpi-helpers restart` to re-exec.
- `heartbeat.json`  — last_tick_iso, last_tick_status, last_tick_code,
                       tick_count, error_count. Rewritten after every tick.

All writes are atomic (temp file + `os.replace`). All writes are tolerant —
state-file I/O errors must NEVER crash the daemon; they are logged and the
daemon keeps ticking. Operators trade a missing state file for a running
producer, not the other way around.

The default state dir is `/data/.openclaw/senpi-helpers/` so the files
survive a container restart on Railway (matching where openclaw state lives).
Override with `SENPI_HELPERS_STATE_DIR` on hosts without a `/data` volume.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._logging import log_event


_DEFAULT_STATE_DIR = "/data/.openclaw/senpi-helpers"

# Env-var prefixes / suffixes that the boot snapshot should capture. Skill
# names (PANGOLIN, KODIAK, ...) are the per-skill convention from
# `runtime-deployment.md` § "Per-skill env-var matrix". Auth tokens are
# always excluded — see `_SENSITIVE_KEYS`.
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


def pid_alive(pid: Optional[int]) -> bool:
    """Cheap liveness check via signal(0). Canonical implementation shared by
    `lock.py`, `cli.py`, and `manage.py` — they all need to ask the same
    question ("is this pid alive?") and previously each kept a private copy.

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
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


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
         (the canonical recipe in `runtime-deployment.md`).

    Returns None when stderr is not redirected to a regular file (e.g.
    interactive terminal, pipe, socket, deleted-file fd). The
    `senpi-helpers stats` command surfaces this as a clear "log path
    unknown" error rather than silently aggregating nothing.
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


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


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
    """
    payload = {
        "schema": 1,
        "name": name,
        "pid": os.getpid(),
        "start_time_iso": _now_iso(),
        "wallet": wallet,
        "scanner": scanner,
        "interval_seconds": interval_seconds,
        "tick_timeout": tick_timeout,
        "log_path": log_path,
        "version": version,
    }
    return _atomic_write(daemon_dir(name, state_dir) / "pid.json", payload)


def write_boot(name: str, *, state_dir: Optional[str] = None) -> bool:
    """Write `boot.json` for `name`. Captures argv + sanitized env snapshot.

    Called once at daemon start. `senpi-helpers restart` reads this file
    to re-exec the producer; if it's missing or `script_path` no longer
    exists on disk, restart fails with a clear message.
    """
    script_path = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else None
    payload = {
        "schema": 1,
        "name": name,
        "argv": list(sys.argv),
        "script_path": script_path,
        "cwd": os.getcwd(),
        "env_snapshot": _sanitized_env_snapshot(),
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
