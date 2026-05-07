"""scanner_lock — fcntl lock with PID-aliveness stale recovery.

If a previous holder died holding the file (kill -9, OOM, container restart),
the lock file persists but `flock` is auto-released by the kernel. The next
caller's `flock(LOCK_EX | LOCK_NB)` succeeds; this module just records the
new holder's metadata via in-place write so the inode stays stable.

If a metadata-claiming holder is still alive AND `flock` is held by it,
the new caller's `flock` fails with `BlockingIOError` — surfaced verbatim.

The unlink-then-open pattern is deliberately not used: it would create a
new inode and let two callers flock different inodes for the same path.
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
    heartbeat_timeout: Optional[float] = None,  # noqa: ARG001 — kept for API compat; unused
) -> Iterator[None]:
    """Acquire an exclusive lock for the named scanner.

    Implementation: open the lock file with `O_CREAT` (create if absent,
    do NOT truncate or unlink anything that exists), then attempt
    `fcntl.flock(LOCK_EX | LOCK_NB)`.

    - If `flock` succeeds, the kernel guarantees no other process holds
      it. If the file already existed with stale metadata, that means a
      previous holder died — the kernel auto-released its flock. We
      overwrite metadata in place via `_write_metadata_inplace` (keeps
      inode stable).
    - If `flock` fails (`BlockingIOError`), a live process is holding
      the lock; we propagate the error. The caller decides whether to
      skip this tick or retry later.

    PID-aliveness in metadata is informational only — used in the
    `lock_acquired` event for observability, not for stealing locks.
    The previous "alive AND mtime > timeout" predicate could steal a
    lock from a slow but alive holder, and was removed.

    Args:
        name: scanner identifier; lock file lives at
            `<lock_dir>/senpi-<name>.lock`. Include a wallet hash in
            `name` when the host runs multiple wallets.
        lock_dir: optional override; defaults to `cfg.LOCK_DIR`.
        heartbeat_timeout: legacy parameter, accepted but unused.

    Raises:
        BlockingIOError: another live process holds the lock.
    """
    path = _lock_path(name, lock_dir)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Inspect metadata for an informative log line — the BlockingIOError
            # is the authoritative result (a live process owns the flock).
            meta = _read_lock_metadata(path)
            prev_pid = int(meta.get("pid", -1)) if meta else -1
            log_event(
                "lock_busy",
                name=name,
                prev_pid=prev_pid,
                prev_alive=_process_alive(prev_pid) if prev_pid > 0 else False,
            )
            # Don't os.close(fd) here — the outer finally at the end of this
            # function handles cleanup for both success and failure paths.
            # A redundant close here would race with another thread that
            # might have just opened a file and received the same fd number.
            raise

        # We hold the flock. If metadata existed, the previous holder must be
        # dead (kernel released its flock). Surface that for ops visibility.
        prior = _read_lock_metadata(path)
        if prior is not None:
            log_event(
                "lock_recovered_after_crash",
                name=name,
                prev_pid=int(prior.get("pid", -1)),
                prev_alive=_process_alive(int(prior.get("pid", -1))),
            )

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
            # Do NOT unlink the lock file. The unlink-then-open pattern
            # documented as "deliberately not used" at the top of this file
            # applies to release as well as acquire: between LOCK_UN and
            # unlink, another process can open the still-existing inode and
            # flock it; a third process arriving after the unlink would then
            # open with O_CREAT and get a brand-new inode, flocking it
            # independently — two processes holding flocks on different
            # inodes for the same path. Leaving the file in place keeps every
            # opener pointing at the same inode.
            log_event("lock_released", name=name, pid=os.getpid())
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
