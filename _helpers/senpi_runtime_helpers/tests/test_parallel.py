"""parallel — concurrency cap + queue + result ordering."""

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
    def test_returns_in_order(self) -> None:
        calls = [(lambda i=i: i * 10) for i in range(5)]
        results = parallel(calls, max_concurrent=4)
        self.assertEqual(results, [0, 10, 20, 30, 40])

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
        self.assertLessEqual(active["peak"], cap)

    def test_exceptions_returned_in_place(self) -> None:
        def bad():
            raise RuntimeError("boom")

        def good():
            return "yay"

        results = parallel([good, bad, good], max_concurrent=2)
        self.assertEqual(results[0], "yay")
        self.assertIsInstance(results[1], RuntimeError)
        self.assertEqual(results[2], "yay")

    def test_raise_first_exception_flag(self) -> None:
        def bad():
            raise ValueError("oops")

        with self.assertRaises(ValueError):
            parallel([bad], max_concurrent=1, raise_first_exception=True)

    def test_empty_input(self) -> None:
        self.assertEqual(parallel([], max_concurrent=4), [])


if __name__ == "__main__":
    unittest.main()
