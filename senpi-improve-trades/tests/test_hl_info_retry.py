#!/usr/bin/env python3
"""Hyperliquid rate limiting is retried, never read as "couldn't read" on the first 429 (R1 dev E2E round 2, N5).

HL answers a rate-limited read with HTTP 429 and a `null` body. With ~90 `userFills` calls in ~9 s on a big
book, the saved wallets — read last — came back "trades couldn't be read". `_hl_info` now reads the HTTP
status, retries 429 / 5xx (3 tries, bounded by a per-review sleep budget), still fails open → None, and the
fan-out reads saved wallets first. The `_FixtureClient` path is untouched.

    python3 -m pytest senpi-improve-trades/tests/test_hl_info_retry.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import review  # noqa: E402

PAYLOAD = {"type": "userFills", "user": "0x" + "c3" * 20}


class _Curl:
    """Stands in for subprocess.run: each call pops the next (stdout, returncode)."""
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out, rc = self.answers.pop(0)
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")


@pytest.fixture
def curl(monkeypatch):
    sleeps = []
    monkeypatch.setattr(review.time, "sleep", lambda s: sleeps.append(s))
    review._reset_hl_retry_budget()

    def install(answers):
        c = _Curl(answers)
        monkeypatch.setattr(review.subprocess, "run", c)
        c.sleeps = sleeps
        return c
    return install


def test_a_200_is_parsed_from_the_body_and_the_status_is_asked_for(curl):
    c = curl([('[{"coin": "BTC"}]\n200', 0)])
    assert review._hl_info(PAYLOAD, {}) == [{"coin": "BTC"}]
    assert "-w" in c.calls[0] and c.calls[0][c.calls[0].index("-w") + 1] == "\n%{http_code}"
    assert c.sleeps == []


def test_a_429_is_retried_then_answered(curl):
    c = curl([("null\n429", 0), ("null\n429", 0), ("[]\n200", 0)])
    meta = {}
    assert review._hl_info(PAYLOAD, meta) == [], "a recovered read is an answer"
    assert len(c.calls) == 3 and c.sleeps == [1.0, 2.0]
    assert not meta.get("warnings")


def test_a_5xx_is_retried_too(curl):
    c = curl([("<html>bad gateway</html>\n502", 0), ('[{"coin": "ETH"}]\n200', 0)])
    assert review._hl_info(PAYLOAD, {}) == [{"coin": "ETH"}]
    assert len(c.calls) == 2


def test_three_429s_fail_open_to_none_with_the_status_named(curl):
    c = curl([("null\n429", 0)] * 3)
    meta = {}
    assert review._hl_info(PAYLOAD, meta) is None, "unknown — never [] (no trades)"
    assert len(c.calls) == 3
    assert any("HTTP 429" in w for w in meta["warnings"])


def test_a_4xx_other_than_429_is_not_retried(curl):
    c = curl([("bad request\n422", 0)])
    assert review._hl_info(PAYLOAD, {}) is None
    assert len(c.calls) == 1 and c.sleeps == []


def test_a_transport_failure_is_not_retried_and_fails_open(curl):
    c = curl([("\n000", 28)])
    assert review._hl_info(PAYLOAD, {}) is None
    assert len(c.calls) == 1


def test_the_retry_sleep_is_bounded_per_review(curl):
    c = curl([("null\n429", 0)] * 30)
    for _ in range(10):
        review._hl_info(PAYLOAD, {})
    assert sum(c.sleeps) <= review.HL_RETRY_BUDGET_S
    assert len(c.calls) < 30, "once the budget is spent a 429 fails open at once"


def test_a_saved_wallets_fills_still_read_as_unknown_after_retries(curl):
    curl([("null\n429", 0)] * 3)
    meta = {}

    class _Client:   # no `_r`: the live transport path
        def mcp_call(self, tool, timeout=12, **kw):
            return {"closedPositions": []}
    assert review.fetch_closed_trades(_Client(), PAYLOAD["user"], 0, None, None, meta, strict=True) is None
    assert meta["closed_trades_unknown"] == [PAYLOAD["user"]]


def test_the_fixture_client_path_never_shells_out(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fixture path must not shell out")
    monkeypatch.setattr(review.subprocess, "run", boom)
    client = review._FixtureClient({f"hl::userFills::{PAYLOAD['user']}": []})
    assert review._hl_info(PAYLOAD, {}, client) == []


def test_saved_wallets_are_read_first_and_merged_in_the_original_order(monkeypatch):
    seen = []

    def one(client, strat, priv, *a, **k):
        seen.append(strat["label"])
        return {"trades": [{"label": strat["label"], "close_time": 0}], "missed_signals": [],
                "leaks": {c: {"count": 0, "samples": []} for c in ("order_failed", "protection_gaps", "risk_halts")},
                "fills": {"maker": 0, "taker": 0, "unknown": 0}, "meta": priv}
    monkeypatch.setattr(review, "_collect_one_strategy", one)
    strategies = ([{"label": f"s{i}", "kind": None} for i in range(10)]
                  + [{"label": "saved", "kind": review.EXTERNAL}])
    trades, *_ = review._collect_trades(None, strategies, {}, 0, None, None, want_market=False)
    assert seen.index("saved") < 8, "a saved wallet is submitted ahead of the strategy wallets"
    assert {t["label"] for t in trades} == {s["label"] for s in strategies}
