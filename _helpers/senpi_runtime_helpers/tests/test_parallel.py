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


if __name__ == "__main__":
    unittest.main()
