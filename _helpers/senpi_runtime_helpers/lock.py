"""scanner_lock — liveness-aware fcntl lock.

Detects stale locks (process killed, crashed, OOM) by reading the holder PID
and a heartbeat mtime. If the holder is dead OR has not refreshed mtime within
LOCK_HEARTBEAT_TIMEOUT, the lock is forcibly cleared and re-acquired.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import contextlib
import errno
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Iterator, Optional

from . import _config as cfg
from ._logging import log_event


def _process_alive(pid: int) -> bool:
    """Cheap liveness check via signal(0). Returns True if process exists and we have permission to signal it."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it. Still alive.
        return True
    except OSError as e:
        return e.errno != errno.ESRCH


def _lock_path(name: str, lock_dir: Optional[str]) -> Path:
    base = Path(lock_dir or cfg.LOCK_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"senpi-{name}.lock"


def _read_lock_metadata(path: Path) -> Optional[dict]:
    try:
        with path.open("r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_metadata_inplace(fd: int, payload: dict) -> None:
    """Write payload onto the *already-locked* fd. Keeps the inode + flock state stable.

    Using `os.replace` to write metadata would swap the inode out from under the flock,
    letting subsequent opens flock on the new inode while the original holder stays
    blocked on the orphaned inode. In-place truncate+write keeps every concurrent
    opener of `path` pointing at the same flocked inode.
    """
    encoded = json.dumps(payload).encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    while encoded:
        n = os.write(fd, encoded)
        encoded = encoded[n:]
    try:
        os.fsync(fd)
    except OSError:
        pass


@contextlib.contextmanager
def scanner_lock(
    name: str,
    lock_dir: Optional[str] = None,
    heartbeat_timeout: Optional[float] = None,
) -> Iterator[None]:
    """Acquire an exclusive lock for the named scanner. Auto-recovers stale locks.

    Args:
        name: scanner identifier; becomes the lock filename (`senpi-<name>.lock`).
        lock_dir: directory to hold the lock file. Defaults to env SENPI_HELPERS_LOCK_DIR or /tmp.
        heartbeat_timeout: seconds; if held lock's mtime is older than this and
            its PID is also dead, the lock is forcibly cleared. Defaults to
            SENPI_HELPERS_LOCK_HEARTBEAT_TIMEOUT (300s).

    Raises:
        BlockingIOError: another live process holds the lock.
    """
    timeout = heartbeat_timeout if heartbeat_timeout is not None else cfg.LOCK_HEARTBEAT_TIMEOUT
    path = _lock_path(name, lock_dir)

    # Pre-check existing metadata for stale recovery.
    meta = _read_lock_metadata(path)
    if meta:
        prev_pid = int(meta.get("pid", -1))
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        age = time.time() - mtime
        alive = _process_alive(prev_pid)
        if not alive or age > timeout:
            log_event(
                "lock_stale_recovered",
                name=name,
                prev_pid=prev_pid,
                prev_age_s=int(age),
                prev_alive=alive,
            )
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log_event("lock_busy", name=name)
            os.close(fd)
            raise
        payload = {
            "pid": os.getpid(),
            "started": time.time(),
            "name": name,
        }
        _write_metadata_inplace(fd, payload)
        log_event("lock_acquired", name=name, pid=os.getpid())
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            log_event("lock_released", name=name, pid=os.getpid())
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
