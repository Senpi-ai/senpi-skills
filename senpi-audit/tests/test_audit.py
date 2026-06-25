#!/usr/bin/env python3
"""Offline engine test — runs audit.run() against a recorded MCP fixture (no network)."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import audit  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "audit_fixture.json")


def _client():
    with open(FIXTURE) as f:
        return audit._FixtureClient(json.load(f))


def test_recent_summary():
    r = audit.run(_client(), "default")
    assert r["summary"]["total"] == 3
    assert r["summary"]["by_action"]["create"] == 2 and r["summary"]["by_action"]["update"] == 1
    assert len(r["summary"]["failures"]) == 1            # the failed SOL short
    e = r["entries"][0]
    assert e["tool"] == "create_position" and e["reason"].startswith("Opened BTC")


def test_strategy_history():
    r = audit.run(_client(), "strategy", strategy_id="strat1")
    assert r["entries"][0]["tool"] == "strategy_create_custom_strategy"


def test_failures_only():
    r = audit.run(_client(), "failures")
    assert all(e["success"] is False for e in r["entries"]) and len(r["entries"]) == 1


def test_fails_open_on_empty():
    r = audit.run(audit._FixtureClient({}), "default")
    assert r["entries"] == [] and r["meta"].get("degraded")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
