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
