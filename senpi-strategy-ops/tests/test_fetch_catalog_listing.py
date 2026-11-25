"""The catalog listing is GitHub's tree API, and unauthenticated that is 60 calls an hour per IP.

A conditional request answered 304 still counts against that limit, so the listing is kept on the
box for ten minutes instead, in the same file the runtime's catalog fetch uses. When GitHub refuses,
an older listing is used; with none, the error says when the limit resets.

Run: python3 -m pytest senpi-strategy-ops/tests/test_fetch_catalog_listing.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import _fetch  # noqa: E402

TREE = {"tree": [
    {"type": "blob", "path": "strategies/hawk/strategy.yaml"},
    {"type": "blob", "path": "strategies/hawk/main/runtime.yaml"},
]}
RESET = 1795613511  # 13:31:51 UTC


class Remote:
    def __init__(self):
        self.tree_status = 200
        self.tree_headers = {}
        self.listings = 0
        self.now = 1_000_000.0

    def get(self, host, path, accept, timeout):
        if host == "api.github.com":
            self.listings += 1
            body = json.dumps(TREE).encode() if self.tree_status == 200 else b'{"message":"API rate limit exceeded"}'
            return self.tree_status, body, self.tree_headers
        return 200, b"x: 1\n", {}


@pytest.fixture
def remote(monkeypatch):
    r = Remote()
    monkeypatch.setattr(_fetch, "_get", r.get)
    monkeypatch.setattr(_fetch, "_now", lambda: r.now)
    return r


def test_ten_fetches_inside_ten_minutes_list_the_catalog_once(tmp_path, remote):
    for i in range(10):
        assert (_fetch.fetch_package("hawk", tmp_path / str(i)) / "strategy.yaml").exists()
        remote.now += 30
    assert remote.listings == 1
    remote.now += 600
    _fetch.fetch_package("hawk", tmp_path / "later")
    assert remote.listings == 2


def test_a_refusal_uses_the_last_listing_however_old(tmp_path, remote):
    _fetch.fetch_package("hawk", tmp_path / "a")
    remote.now += 86_400
    remote.tree_status, remote.tree_headers = 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(RESET)}
    assert (_fetch.fetch_package("hawk", tmp_path / "b") / "strategy.yaml").exists()
    assert remote.listings == 2


@pytest.mark.parametrize("status", [403, 429])
def test_a_refusal_with_no_listing_says_when_the_limit_resets(tmp_path, remote, status):
    remote.tree_status, remote.tree_headers = status, {"x-ratelimit-reset": str(RESET)}
    remote.now = RESET - 42 * 60
    with pytest.raises(_fetch.FetchError) as e:
        _fetch.fetch_package("hawk", tmp_path)
    assert f"HTTP {status}" in str(e.value)
    assert "resets at 13:31 UTC, in 42 minutes" in str(e.value)


def test_the_listing_file_is_the_one_the_runtime_reads(tmp_path, remote):
    """Same directory, name and fields as senpi-trading-runtime src/deploy/batch/catalog-fetch.ts."""
    _fetch.fetch_package("hawk", tmp_path)
    f = os.path.join(os.environ["SENPI_CATALOG_CACHE_DIR"], "catalog-tree-Senpi-ai_senpi-skills_main.json")
    listing = json.load(open(f))
    assert listing == {"repo": "Senpi-ai/senpi-skills", "ref": "main", "fetchedAt": 1_000_000_000,
                       "paths": ["strategies/hawk/strategy.yaml", "strategies/hawk/main/runtime.yaml"]}
