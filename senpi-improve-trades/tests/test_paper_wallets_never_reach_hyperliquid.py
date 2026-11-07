#!/usr/bin/env python3
"""A simulation's wallet is never sent to Hyperliquid.

`review.py` recovers closed trades from Hyperliquid's public `/info` by wallet address. A simulation's
wallet lives on the simulated venue, so asking Hyperliquid about it is wrong. `_hl_info` refuses a
simulation address before any request leaves, and the live `strategy_list` read drops a row marked
`paper: true`.

    python3 -m pytest senpi-improve-trades/tests/test_paper_wallets_never_reach_hyperliquid.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import review  # noqa: E402

PAPER_WALLET = "0x0000000000000000" + "1762500000000" + "0a1b2c3d4e5"
LIVE_WALLET = "0x" + "ab" * 20


def test_a_paper_address_is_refused_before_any_request(monkeypatch):
    sent = []
    monkeypatch.setattr(review.subprocess, "run", lambda argv, **kw: sent.append(argv))
    meta = {}
    for user in (PAPER_WALLET, "  " + PAPER_WALLET.upper().replace("0X", "0x")):
        assert review._hl_info({"type": "userFills", "user": user}, meta) is None
    assert sent == []
    assert any("simulation wallet" in w for w in meta["warnings"])


def test_strict_closed_trades_for_a_paper_wallet_read_unknown_not_empty(monkeypatch):
    sent = []
    monkeypatch.setattr(review.subprocess, "run", lambda argv, **kw: sent.append(argv))

    class _Empty:
        def mcp_call(self, tool, timeout=12, **kw):
            return {"success": True, "data": {"positions": []}}
    meta = {}
    got = review.fetch_closed_trades(_Empty(), PAPER_WALLET, 0, 2 ** 62, 50, meta, strict=True)
    assert got is None
    assert PAPER_WALLET in meta.get("closed_trades_unknown", [])
    assert sent == []


def test_the_live_strategy_list_drops_paper_rows(monkeypatch):
    monkeypatch.setattr(review, "load_runtime_registry", lambda meta: ({}, {}, None))

    class _List:
        def mcp_call(self, tool, timeout=12, **kw):
            assert tool == "strategy_list" and "paper" not in kw
            return {"success": True, "data": {"strategies": [
                {"id": "live-1", "strategyWalletAddress": LIVE_WALLET, "status": "ACTIVE"},
                {"id": "paper-1", "strategyWalletAddress": PAPER_WALLET, "status": "ACTIVE", "paper": True}]}}
    rows = review.fetch_strategies(_List(), {})
    assert [r["wallet"] for r in rows] == [LIVE_WALLET]
