"""producer_daemon — interval, lock-skip on overlap, error isolation, max_ticks."""

import os
import sys
import time
import unittest

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers.daemon import producer_daemon


class DaemonTests(unittest.TestCase):
    def test_runs_max_ticks_and_returns(self) -> None:
        calls = []

        def fn() -> None:
            calls.append(time.time())

        ticks = producer_daemon(
            fn=fn,
            interval_seconds=0.05,
            name="test_daemon_basic",
            tick_timeout=5.0,
            max_ticks=3,
            install_signal_handlers=False,
        )
        self.assertEqual(ticks, 3)
        self.assertEqual(len(calls), 3)
        # Spacings should be roughly interval-aligned (>= interval - jitter).
        for i in range(1, len(calls)):
            gap = calls[i] - calls[i - 1]
            self.assertGreaterEqual(gap, 0.04)  # leave generous slack for CI

    def test_exception_in_tick_does_not_kill_loop(self) -> None:
        attempts = {"n": 0}

        def fn() -> None:
            attempts["n"] += 1
            if attempts["n"] == 2:
                raise RuntimeError("simulated tick failure")

        ticks = producer_daemon(
            fn=fn,
            interval_seconds=0.02,
            name="test_daemon_err_isolate",
            tick_timeout=5.0,
            max_ticks=4,
            install_signal_handlers=False,
        )
        self.assertEqual(ticks, 4)
        self.assertEqual(attempts["n"], 4)

    def test_long_tick_compresses_sleep(self) -> None:
        """If a tick exceeds the interval, the next tick fires ASAP (no negative sleep)."""
        gaps = []
        last = {"t": None}

        def fn() -> None:
            now = time.time()
            if last["t"] is not None:
                gaps.append(now - last["t"])
            last["t"] = now
            time.sleep(0.10)  # tick takes longer than interval

        ticks = producer_daemon(
            fn=fn,
            interval_seconds=0.02,  # interval << tick duration
            name="test_daemon_long_tick",
            tick_timeout=5.0,
            max_ticks=3,
            install_signal_handlers=False,
        )
        self.assertEqual(ticks, 3)
        # Gaps should track the tick duration (~100ms) rather than balloon.
        for g in gaps:
            self.assertGreaterEqual(g, 0.10)
            self.assertLess(g, 0.30)

    def test_tick_timeout_aborts_call_and_continues_loop(self) -> None:
        if not hasattr(__import__("signal"), "SIGALRM"):
            self.skipTest("SIGALRM unsupported on this platform")
        timeouts = {"n": 0}

        def fn() -> None:
            try:
                time.sleep(2.0)  # would exceed tick_timeout
            except BaseException:
                timeouts["n"] += 1
                raise

        ticks = producer_daemon(
            fn=fn,
            interval_seconds=0.02,
            name="test_daemon_timeout",
            tick_timeout=0.05,  # 50ms
            max_ticks=2,
            install_signal_handlers=False,
        )
        self.assertEqual(ticks, 2)
        self.assertGreaterEqual(timeouts["n"], 2)


if __name__ == "__main__":
    unittest.main()
