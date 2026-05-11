"""parallel — concurrency cap + queue + result ordering + tuple-shaped results."""

import os
import sys
import threading
import time
import unittest

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers.parallel import parallel


class ParallelTests(unittest.TestCase):
    def test_returns_in_order_with_ok_tuples(self) -> None:
        calls = [(lambda i=i: i * 10) for i in range(5)]
        results = parallel(calls, max_concurrent=4)
        self.assertEqual(results, [(True, 0), (True, 10), (True, 20), (True, 30), (True, 40)])

    def test_concurrency_cap_observed(self) -> None:
        active = {"current": 0, "peak": 0}
        lock = threading.Lock()
        cap = 3

        def slow():
            with lock:
                active["current"] += 1
                active["peak"] = max(active["peak"], active["current"])
            time.sleep(0.05)
            with lock:
                active["current"] -= 1
            return "ok"

        calls = [slow for _ in range(20)]
        results = parallel(calls, max_concurrent=cap)
        self.assertEqual(len(results), 20)
        self.assertTrue(all(ok for ok, _ in results))
        self.assertLessEqual(active["peak"], cap)

    def test_exceptions_returned_in_place_as_failure_tuples(self) -> None:
        def bad():
            raise RuntimeError("boom")

        def good():
            return "yay"

        results = parallel([good, bad, good], max_concurrent=2)
        self.assertEqual(results[0], (True, "yay"))
        ok, value = results[1]
        self.assertFalse(ok)
        self.assertIsInstance(value, RuntimeError)
        self.assertEqual(results[2], (True, "yay"))

    def test_raise_after_completion_flag(self) -> None:
        def bad():
            raise ValueError("oops")

        with self.assertRaises(ValueError):
            parallel([bad], max_concurrent=1, raise_after_completion=True)

    def test_empty_input(self) -> None:
        self.assertEqual(parallel([], max_concurrent=4), [])

    def test_thread_count_bounded_by_max_concurrent(self) -> None:
        """1000 calls should NOT spawn 1000 OS threads — the executor's
        internal queue holds the rest behind `max_concurrent` workers.
        Verified via thread-count active during run."""
        import threading as _t
        cap = 4
        seen_counts = []
        check_lock = _t.Lock()
        ready = _t.Event()

        def task(i=0):
            # Snapshot active senpi-parallel thread count while we hold a slot.
            with check_lock:
                count = sum(
                    1 for t in _t.enumerate()
                    if t.name.startswith("senpi-parallel")
                )
                seen_counts.append(count)
            # Hold the slot briefly so the count is meaningful.
            time.sleep(0.02)
            return i

        results = parallel([task] * 200, max_concurrent=cap)
        self.assertEqual(len(results), 200)
        self.assertTrue(all(ok for ok, _ in results))
        # Peak observed senpi-parallel thread count must be <= cap.
        self.assertLessEqual(max(seen_counts), cap)

    def test_legitimate_exception_return_not_misclassified(self) -> None:
        """A tool that legitimately returns an Exception subclass as a value
        should be classified as success, distinguishable from real failures."""
        def returns_exception():
            return ValueError("this is the legitimate return value, not a failure")

        results = parallel([returns_exception], max_concurrent=1)
        ok, value = results[0]
        self.assertTrue(ok)  # success — the function returned normally
        self.assertIsInstance(value, ValueError)

    def test_queue_warn_throttled_across_calls(self) -> None:
        """The parallel_queue_warn event must be throttled across multiple
        parallel() calls — a producer tick can fire several invocations in
        a row, and we don't want one warning per invocation. The gate is
        module-scoped so the _last_emitted timestamp persists between calls.
        """
        # __init__.py rebinds `senpi_runtime_helpers.parallel` attribute to
        # the function, so `import ... as` returns the function not the
        # module. Pull the module out of sys.modules directly.
        import sys
        parmod = sys.modules["senpi_runtime_helpers.parallel"]
        from senpi_runtime_helpers import _logging as logmod

        # Reset the throttle gate so prior tests don't influence the window.
        parmod._WARN_GATE._last_emitted = 0.0

        events = []
        original = logmod.log_event

        def capture(event, **fields):
            events.append((event, fields))
            return original(event, **fields)

        logmod.log_event = capture
        parmod.log_event = capture
        try:
            # Three calls in quick succession, each well over warn_queue_depth.
            for _ in range(3):
                parallel(
                    [(lambda: 1) for _ in range(10)],
                    max_concurrent=2,
                    warn_queue_depth=5,
                )
        finally:
            logmod.log_event = original
            parmod.log_event = original

        warns = [e for e in events if e[0] == "parallel_queue_warn"]
        # Exactly one warn fired across the 3 invocations — the throttle held
        # the next two back. Without the module-scoped gate, all 3 would warn.
        self.assertEqual(len(warns), 1, f"expected 1 warn, got {len(warns)}: {warns!r}")

    def test_baseexception_in_main_thread_does_not_block_on_workers(self) -> None:
        """If a BaseException (like producer_daemon's _TickTimeout via SIGALRM)
        is raised in the main thread mid-fan-out, parallel() must NOT block
        until every worker finishes — that would defeat the per-tick budget."""
        import signal as _signal
        if not hasattr(_signal, "SIGALRM"):
            self.skipTest("SIGALRM unsupported on this platform")

        slow_release = threading.Event()

        def slow():
            slow_release.wait(5.0)  # would hold for 5s without release
            return "late"

        class _FakeTimeout(BaseException):
            pass

        def alarm_handler(_sig, _frame):
            raise _FakeTimeout("simulated tick budget exceeded")

        _signal.signal(_signal.SIGALRM, alarm_handler)
        _signal.setitimer(_signal.ITIMER_REAL, 0.05)  # fire after 50ms

        started = time.time()
        try:
            try:
                parallel([slow, slow], max_concurrent=2)
                self.fail("expected _FakeTimeout to propagate")
            except _FakeTimeout:
                pass
            elapsed = time.time() - started
            self.assertLess(
                elapsed,
                2.0,
                f"parallel() blocked {elapsed:.2f}s after BaseException — "
                f"per-tick budget would not be enforced",
            )
        finally:
            _signal.setitimer(_signal.ITIMER_REAL, 0)
            slow_release.set()  # let leaked workers finish


if __name__ == "__main__":
    unittest.main()
