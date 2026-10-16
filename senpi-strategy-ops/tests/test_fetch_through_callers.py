"""The fork fallback has to survive the two functions that actually call it.

Both callers rebuild the path from the id they passed:

    deploy.py:171   _fetch.fetch_package(sid, tmp, ref=ref)   # contract: writes <dest_root>/<id>
                    if not (tmp / sid / "strategy.yaml").is_file(): raise ...
    close.py:225    _fetch.fetch_package(sid, dest_root, ref=a.ref)
                    pkg = _pkg.load(dest_root / sid)

An earlier cut of the fallback wrote the TEMPLATE's name instead, so a download that fully
succeeded was invisible to both: `deploy.py` reported "fetched tree has no strategy.yaml" — false,
and pointing away from the cause — and swallowed it in the `except`. The unit tests all passed
because every one of them called `fetch_package` directly. That is the gap this file closes.

Run: python3 -m pytest senpi-strategy-ops/tests/test_fetch_through_callers.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import _fetch  # noqa: E402
import deploy  # noqa: E402

TREE = {"tree": [
    {"type": "blob", "path": "strategies/athena/strategy.yaml"},
    {"type": "blob", "path": "strategies/athena/phalanx/runtime.yaml"},
    {"type": "blob", "path": "strategies/zec/strategy.yaml"},
]}


@pytest.fixture
def remote(monkeypatch):
    def _get(host, path, accept, timeout):
        if host == "api.github.com":
            return 200, json.dumps(TREE).encode()
        return 200, b"id: athena\n"
    monkeypatch.setattr(_fetch, "_get", _get)


@pytest.mark.parametrize("sid", ["athena", "ignasteamsenpi-athena", "john-doe-athena"])
def test_deploy_fetch_fresh_returns_a_package_dir(tmp_path, remote, sid):
    got = deploy._fetch_fresh(sid, None, lambda m: None, tmp_path)
    assert got is not None, f"{sid}: _fetch_fresh swallowed a successful download"
    assert got.name == sid, f"{sid}: returned {got.name!r} — the caller rebuilds the path from the id"
    assert (got / "strategy.yaml").is_file()


def test_deploy_fetch_fresh_still_refuses_a_non_fork(tmp_path, remote):
    """`zec-fade` has no `strategies/fade/`; the caller must get None, not someone else's package."""
    assert deploy._fetch_fresh("zec-fade", None, lambda m: None, tmp_path) is None


def test_close_py_reconstruction_finds_the_package(tmp_path, remote):
    """close.py does `_pkg.load(dest_root / sid)` after the fetch — mirror that exact shape."""
    sid = "ignasteamsenpi-athena"
    _fetch.fetch_package(sid, tmp_path)
    assert (tmp_path / sid / "strategy.yaml").is_file(), (
        "close.py rebuilds <dest_root>/<sid> and would raise BadPackage")
