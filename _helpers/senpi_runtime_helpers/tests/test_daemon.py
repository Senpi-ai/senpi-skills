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
            alive_check=None,  # opt out — this test doesn't need a runtime
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
            alive_check=None,
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
            alive_check=None,
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
            alive_check=None,
        )
        self.assertEqual(ticks, 2)
        self.assertGreaterEqual(timeouts["n"], 2)

    def test_alive_check_false_at_boot_skips_all_ticks(self) -> None:
        """If alive_check returns False before tick 1, daemon must not run any tick."""
        ticks_run = {"n": 0}

        def fn() -> None:
            ticks_run["n"] += 1

        returned_ticks = producer_daemon(
            fn=fn,
            interval_seconds=0.02,
            name="test_alive_boot_false",
            tick_timeout=1.0,
            max_ticks=10,
            install_signal_handlers=False,
            alive_check=lambda: False,
        )
        self.assertEqual(returned_ticks, 0)
        self.assertEqual(ticks_run["n"], 0)

    def test_alive_check_false_mid_loop_breaks_gracefully(self) -> None:
        """alive_check returning False between ticks must stop the loop cleanly."""
        ticks_run = {"n": 0}
        probes = {"n": 0}

        def fn() -> None:
            ticks_run["n"] += 1

        def probe() -> bool:
            probes["n"] += 1
            # First probe (boot) returns True; flip False on the next probe.
            return probes["n"] <= 1

        returned_ticks = producer_daemon(
            fn=fn,
            interval_seconds=0.02,
            name="test_alive_mid_false",
            tick_timeout=1.0,
            max_ticks=10,
            install_signal_handlers=False,
            alive_check=probe,
            alive_check_interval_seconds=0.02,  # probe between every tick
        )
        # Boot probe (True) → tick 1 runs → probe (False) → exit before tick 2.
        self.assertEqual(ticks_run["n"], 1)
        self.assertEqual(returned_ticks, 1)

    def test_alive_check_transient_error_does_not_terminate(self) -> None:
        """Exceptions from alive_check are swallowed; daemon stays alive."""
        ticks_run = {"n": 0}
        probes = {"n": 0}

        def fn() -> None:
            ticks_run["n"] += 1

        def flaky_probe() -> bool:
            probes["n"] += 1
            raise ConnectionError("simulated /health flake")

        returned_ticks = producer_daemon(
            fn=fn,
            interval_seconds=0.02,
            name="test_alive_transient",
            tick_timeout=1.0,
            max_ticks=3,
            install_signal_handlers=False,
            alive_check=flaky_probe,
            alive_check_interval_seconds=0.02,
        )
        # Probes raised on every call but daemon kept ticking until max_ticks.
        self.assertEqual(returned_ticks, 3)
        self.assertEqual(ticks_run["n"], 3)
        self.assertGreaterEqual(probes["n"], 3)

    def test_no_wallet_no_alive_check_raises(self) -> None:
        """Opt-out semantics: omitting `wallet` AND not passing `alive_check`
        must raise a clear ValueError (not silently disable the check)."""
        def fn() -> None:
            pass

        with self.assertRaises(ValueError) as ctx:
            producer_daemon(
                fn=fn,
                interval_seconds=0.02,
                name="test_no_wallet_no_optout",
                tick_timeout=1.0,
                max_ticks=1,
                install_signal_handlers=False,
            )
        self.assertIn("wallet", str(ctx.exception).lower())
        self.assertIn("alive_check=None", str(ctx.exception))

    def test_tick_timeout_does_not_count_lock_acquire_release(self) -> None:
        """SIGALRM must scope only fn() — not scanner_lock acquire/release.

        Regression: in the original layout `_arm_tick_alarm` ran BEFORE
        entering `scanner_lock`, so a slow lock acquire ate the tick-timeout
        budget and could even fire the alarm mid-flock. This test simulates a
        slow lock by patching `scanner_lock` to sleep on entry; with the fix,
        a fast `fn()` body must complete without a `_TickTimeout` even when
        lock entry takes longer than `tick_timeout`.
        """
        if not hasattr(__import__("signal"), "SIGALRM"):
            self.skipTest("SIGALRM unsupported on this platform")
        import contextlib
        from senpi_runtime_helpers import daemon as daemon_mod

        slow_lock_entries = {"n": 0}

        @contextlib.contextmanager
        def slow_lock(name, lock_dir=None):
            slow_lock_entries["n"] += 1
            time.sleep(0.10)  # simulate slow flock + metadata write
            yield

        original = daemon_mod.scanner_lock
        daemon_mod.scanner_lock = slow_lock
        try:
            calls = {"n": 0}

            def fn() -> None:
                calls["n"] += 1  # cheap; well under tick_timeout

            ticks = producer_daemon(
                fn=fn,
                interval_seconds=0.02,
                name="test_lock_outside_alarm",
                tick_timeout=0.05,  # smaller than the slow lock entry (0.10s)
                max_ticks=2,
                install_signal_handlers=False,
                alive_check=None,
            )
        finally:
            daemon_mod.scanner_lock = original

        # If SIGALRM was bracketing the lock, both ticks would have raised
        # _TickTimeout before fn() ever ran. With the fix, fn() runs cleanly.
        self.assertEqual(ticks, 2)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(slow_lock_entries["n"], 2)

    def test_alive_check_cadence_computed_from_interval(self) -> None:
        """alive_check_interval_seconds determines tick-multiple cadence."""
        probes = {"n": 0}

        def fn() -> None:
            pass

        def probe() -> bool:
            probes["n"] += 1
            return True

        # interval=0.02 s, alive_check_interval=0.10 s → n = round(0.10/0.02) = 5.
        # 6 ticks total: probe at boot + after tick 5 = 2 probes.
        producer_daemon(
            fn=fn,
            interval_seconds=0.02,
            name="test_alive_cadence",
            tick_timeout=1.0,
            max_ticks=6,
            install_signal_handlers=False,
            alive_check=probe,
            alive_check_interval_seconds=0.10,
        )
        self.assertEqual(probes["n"], 2)


if __name__ == "__main__":
    unittest.main()
