"""Cache — hit/miss + clear."""

import os
import sys
import time
import unittest

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers.cache import cached_mcp_call, clear_cache, tick_cache


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def mcp_call(self, tool, timeout=None, **arguments):
        self.calls += 1
        return {"tool": tool, "args": arguments, "n": self.calls}


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

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
        clear_cache()
        cached_mcp_call(client, "x", limit=10)
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
