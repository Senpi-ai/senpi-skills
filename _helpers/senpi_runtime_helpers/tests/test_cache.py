"""Cache — hit/miss + clear."""

import os
import sys
import time
import unittest

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers.cache import cache_summary, cached_mcp_call, clear_cache, tick_cache


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def mcp_call(self, tool, timeout=None, **arguments):
        self.calls += 1
        return {"tool": tool, "args": arguments, "n": self.calls}


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        # Each test makes a fresh client → fresh per-client cache, no
        # cross-test pollution.
        self.client = _FakeClient()

    def test_hit_after_miss(self) -> None:
        client = _FakeClient()
        a = cached_mcp_call(client, "leaderboard_get_markets", limit=100)
        b = cached_mcp_call(client, "leaderboard_get_markets", limit=100)
        self.assertEqual(a, b)
        self.assertEqual(client.calls, 1)  # second call served from cache

    def test_different_args_miss(self) -> None:
        client = _FakeClient()
        a = cached_mcp_call(client, "x", limit=10)
        b = cached_mcp_call(client, "x", limit=20)
        self.assertNotEqual(a, b)
        self.assertEqual(client.calls, 2)

    def test_ttl_expiry(self) -> None:
        client = _FakeClient()
        cached_mcp_call(client, "x", ttl=0.01, limit=10)
        time.sleep(0.05)
        cached_mcp_call(client, "x", ttl=0.01, limit=10)
        self.assertEqual(client.calls, 2)

    def test_decorator(self) -> None:
        client = _FakeClient()
        mcp = tick_cache(client)
        mcp("x", limit=10)
        mcp("x", limit=10)
        self.assertEqual(client.calls, 1)

    def test_clear_drops_entries(self) -> None:
        client = _FakeClient()
        cached_mcp_call(client, "x", limit=10)
        clear_cache(client)
        cached_mcp_call(client, "x", limit=10)
        self.assertEqual(client.calls, 2)

    def test_summary_counts_hits_and_misses(self) -> None:
        client = _FakeClient()
        cached_mcp_call(client, "x", limit=10)   # miss
        cached_mcp_call(client, "x", limit=10)   # hit
        cached_mcp_call(client, "y", limit=20)   # miss
        s = cache_summary(client)
        self.assertEqual(s["hits"], 1)
        self.assertEqual(s["misses"], 2)
        self.assertEqual(s["size"], 2)

    def test_per_client_isolation(self) -> None:
        c1 = _FakeClient()
        c2 = _FakeClient()
        cached_mcp_call(c1, "x", limit=10)
        cached_mcp_call(c2, "x", limit=10)  # different client → not a hit
        self.assertEqual(c1.calls, 1)
        self.assertEqual(c2.calls, 1)

    def test_thundering_herd_coalesced_to_one_call(self) -> None:
        """N parallel callers missing the same key should issue ONE MCP call.

        The owner makes the call; waiters block on the per-key event and read
        the result once it lands.
        """
        import threading
        client = _FakeClient()
        # Make mcp_call slow so concurrent callers all see the miss.
        original = client.mcp_call
        gate = threading.Event()

        def slow_mcp_call(tool, timeout=None, **arguments):
            gate.wait(2.0)  # wait for all threads to register before returning
            return original(tool, timeout=timeout, **arguments)

        client.mcp_call = slow_mcp_call

        results = [None] * 8
        threads = []
        def worker(i):
            results[i] = cached_mcp_call(client, "x", limit=10)

        for i in range(8):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        # Give all threads a moment to register as waiters.
        import time as _t
        _t.sleep(0.2)
        gate.set()
        for t in threads:
            t.join()

        # Owner made one call; waiters reused the result.
        self.assertEqual(client.calls, 1)
        # All threads got the same value.
        first = results[0]
        for r in results:
            self.assertEqual(r, first)
        s = cache_summary(client)
        self.assertEqual(s["misses"], 1)
        self.assertGreaterEqual(s["coalesced"], 7)  # 8 callers, 1 owner, >=7 waiters

    def test_waiter_does_not_return_stale_when_owner_raises(self) -> None:
        """Coalesce + owner exception must not silently serve stale data.

        Scenario: cache holds an expired entry from an earlier tick. A burst
        of N callers miss; the first becomes owner, the rest wait. The owner
        raises mid-flight. Without the fix, waiters wake up, see the stale
        entry still in cache.store (the owner's exception cleared `pending`
        but not the prior `cache.store[key]`), and return it — silently
        violating the TTL contract. With the fix, waiters re-check TTL and
        fall through to issue their own MCP call.
        """
        import threading

        client = _FakeClient()
        # Warm the cache with a short TTL, then expire it.
        cached_mcp_call(client, "x", ttl=0.01, limit=10)
        time.sleep(0.05)
        # cache.store still holds the (now stale) entry until next refresh.
        self.assertEqual(client.calls, 1)

        # Replace mcp_call with one that:
        #   - the OWNER (first caller after the wait) raises
        #   - subsequent callers (waiters that fall through) return a new value
        owner_arrived = threading.Event()
        release_owner = threading.Event()
        call_log: list = []

        def mcp_call_with_owner_raise(tool, timeout=None, **arguments):
            call_log.append(threading.current_thread().name)
            # First call (owner) blocks until release, then raises.
            if len(call_log) == 1:
                owner_arrived.set()
                release_owner.wait(2.0)
                raise RuntimeError("simulated owner failure")
            # Subsequent calls (waiters that fell through) succeed.
            return {"tool": tool, "args": arguments, "fresh": True}

        client.mcp_call = mcp_call_with_owner_raise

        # Spin owner + 3 waiters concurrently. All miss because the entry
        # is past TTL.
        results: list = [None] * 4
        errors: list = [None] * 4
        threads = []

        def worker(i):
            try:
                results[i] = cached_mcp_call(client, "x", ttl=0.01, limit=10)
            except Exception as e:
                errors[i] = e

        # Start the owner first; let it register as the in-flight owner.
        owner_thread = threading.Thread(target=worker, args=(0,), name="owner")
        owner_thread.start()
        owner_arrived.wait(2.0)
        # Now launch waiters; they will register as coalesced waiters.
        for i in range(1, 4):
            t = threading.Thread(target=worker, args=(i,), name=f"waiter-{i}")
            threads.append(t)
            t.start()
        # Brief settle to ensure waiters are blocked on pending.wait().
        time.sleep(0.05)
        release_owner.set()
        owner_thread.join(timeout=3.0)
        for t in threads:
            t.join(timeout=3.0)

        # Owner raised — its result slot has the exception.
        self.assertIsInstance(errors[0], RuntimeError)
        # Waiters re-checked TTL on wake and fell through to fresh calls.
        for i in range(1, 4):
            self.assertIsNone(errors[i], f"waiter-{i} unexpectedly raised")
            self.assertIsNotNone(results[i], f"waiter-{i} returned None")
            self.assertTrue(
                results[i].get("fresh") is True,
                f"waiter-{i} returned stale data: {results[i]!r}",
            )

    def test_lru_eviction_when_cap_exceeded(self) -> None:
        from senpi_runtime_helpers import _config as cfg
        original = cfg.TICK_CACHE_MAX_ENTRIES
        cfg.TICK_CACHE_MAX_ENTRIES = 2
        try:
            client = _FakeClient()
            cached_mcp_call(client, "a")
            cached_mcp_call(client, "b")
            cached_mcp_call(client, "c")  # should evict "a"
            s = cache_summary(client)
            self.assertEqual(s["size"], 2)
            self.assertEqual(s["evictions"], 1)
        finally:
            cfg.TICK_CACHE_MAX_ENTRIES = original


if __name__ == "__main__":
    unittest.main()
