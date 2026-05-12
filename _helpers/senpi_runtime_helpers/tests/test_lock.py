"""Lock — happy path, contention, and stale-lock recovery."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Allow tests to find the package whether run from inside the package or repo root.
_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers.lock import scanner_lock, _lock_path, _read_lock_metadata


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_acquire_and_release(self) -> None:
        with scanner_lock("test1", lock_dir=self.tmpdir):
            path = _lock_path("test1", self.tmpdir)
            self.assertTrue(path.exists())
            meta = _read_lock_metadata(path)
            self.assertIsNotNone(meta)
            self.assertEqual(meta["pid"], os.getpid())
        # File persists across release on purpose. Unlinking after LOCK_UN
        # would let a second opener acquire flock on the still-existing inode
        # while a third opener (arriving after the unlink) creates a new
        # inode via O_CREAT and flocks it independently — two processes
        # would then hold flocks on different inodes for the same path.
        # Leaving the file in place keeps every opener pointing at the same
        # inode. Re-acquisition after release must still succeed.
        self.assertTrue(_lock_path("test1", self.tmpdir).exists())
        with scanner_lock("test1", lock_dir=self.tmpdir):
            pass

    def test_recovers_stale_dead_pid(self) -> None:
        # Plant a lock file claiming a dead PID.
        path = _lock_path("test2", self.tmpdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            import json

            json.dump({"pid": 999999999, "started": 0.0, "name": "test2"}, f)
        # Mark mtime as old too.
        os.utime(str(path), (0, 0))

        # Should auto-recover and acquire.
        with scanner_lock("test2", lock_dir=self.tmpdir):
            meta = _read_lock_metadata(path)
            self.assertIsNotNone(meta)
            self.assertEqual(meta["pid"], os.getpid())

    def test_busy_when_another_process_holds_lock(self) -> None:
        # Spawn a child process that holds the lock; parent should fail to acquire.
        import subprocess
        import textwrap

        script = textwrap.dedent(f"""
            import sys, time, os
            sys.path.insert(0, {repr(_HELPERS_PARENT)})
            from senpi_runtime_helpers.lock import scanner_lock

            with scanner_lock("test3", lock_dir={repr(self.tmpdir)}):
                # Signal readiness then sleep so parent has time to attempt acquire.
                print("HELD", flush=True)
                time.sleep(2.0)
        """)
        # Popen as a context manager auto-closes stdout/stderr file objects.
        with subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as proc:
            try:
                # Wait for child to signal "HELD".
                line = proc.stdout.readline().strip()
                self.assertEqual(line, "HELD")
                with self.assertRaises(BlockingIOError):
                    with scanner_lock("test3", lock_dir=self.tmpdir):
                        pass
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_no_false_recovery_log_on_same_pid_reacquire(self) -> None:
        """Re-acquiring the lock from the same PID (the producer_daemon
        common case) must NOT emit `lock_recovered_after_crash`. The lock
        file persists across releases by design; without the same-PID guard,
        every tick after the first would log a phantom crash event."""
        from senpi_runtime_helpers import _logging
        events: list[dict] = []
        original = _logging.log_event

        def capture(event: str, **fields):
            events.append({"event": event, **fields})

        _logging.log_event = capture
        try:
            with scanner_lock("test_norecov", lock_dir=self.tmpdir):
                pass
            with scanner_lock("test_norecov", lock_dir=self.tmpdir):
                pass
        finally:
            _logging.log_event = original

        recoveries = [e for e in events if e["event"] == "lock_recovered_after_crash"]
        self.assertEqual(
            recoveries,
            [],
            f"unexpected crash-recovery events: {recoveries}",
        )


if __name__ == "__main__":
    unittest.main()
