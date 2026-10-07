#!/usr/bin/env python3
"""Saved wallets in discover's holdings context (External Wallets R1, amendment A1).

A saved wallet is one the user added in Your wallets by pasting its address — their claim, not proof of
control — and trades by hand. Senpi can read it and cannot place, change or cancel orders on it.

discover reads it for ONE reason: a template must not double an exposure the user already holds. So
`--context-only` carries the saved wallets' positions next to the Senpi holdings, each row naming its
wallet and origin, and nothing else changes: the budget stays Senpi money only (a saved wallet can't be
deployed), and every pick is still a Senpi strategy deployed with Senpi money.

Unknown is never empty: a failed read is `saved_wallets_status: "unavailable"` with holdings None (never
[]), a wallet whose state couldn't load is named in `saved_wallets_unread` (never flat), and no failure
breaks the context — the Senpi budget and holdings still come back.

    python3 -m pytest senpi-strategy-discover/tests/test_discover_saved_wallets.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "senpi-strategy-discover", "scripts"))

import discover  # noqa: E402

SKILL = os.path.join(ROOT, "senpi-strategy-discover", "SKILL.md")
ACCESS = "Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it."
CW_A = "0x" + "a1" * 20
CW_B = "0x" + "b2" * 20
PORTFOLIO = {"total_in_hyperliquid": 40.0, "total_spot_usd_in_hyperliquid": 0, "token_balances": [],
             "total_withdrawable": 900.0, "positions": [{"coin": "ETH"}]}


def _pos(coin, side="LONG", value="3200.00", protection="FULL"):
    return {"coin": coin, "dex": "", "side": side, "size": "0.05", "positionValueUsd": value,
            "unrealizedPnlUsd": "100.00", "leverage": 5, "stopOrders": [], "coveredSize": "0.05",
            "protection": protection}


def _state(positions):
    return {"readAt": "2026-10-03T12:00:00Z", "readError": None, "role": "USER", "totalValueUsd": "5234.10",
            "unpricedCoins": [], "positions": positions, "openOrders": []}


def _wallet(addr, label, state):
    return {"address": addr, "label": label, "added_at": "2026-10-01T15:31:02.000Z", "access": ACCESS,
            "state": state}


class _Client:
    """Records every call; `external` is the account_get_external_wallets reply, or an Exception to raise."""
    def __init__(self, external=None):
        self.external, self.calls = external, []

    def mcp_call(self, name, **kw):
        self.calls.append(name)
        if name == "account_get_portfolio":
            return {"success": True, "data": {"portfolio": dict(PORTFOLIO)}}
        if name == "account_get_external_wallets":
            if isinstance(self.external, Exception):
                raise self.external
            return self.external
        raise AssertionError(f"unexpected tool {name}")


def test_saved_wallet_positions_join_the_holdings_labelled_by_wallet_and_origin():
    c = _Client({"success": True, "data": {"external_wallets": [
        _wallet(CW_A, "MetaMask", _state([_pos("BTC"), _pos("xyz:NVDA", side="SHORT", value="800.00")])),
        _wallet(CW_B, None, _state([_pos("HYPE")]))]}})
    ctx = discover.fetch_user_context(c, saved_wallets=True)
    assert ctx["holdings"] == ["ETH"], "the Senpi holdings are unchanged"
    assert ctx["saved_wallets_status"] == "ok"
    assert ctx["saved_wallets_unread"] == []
    rows = ctx["saved_wallet_holdings"]
    assert [(r["coin"], r["side"], r["wallet"]) for r in rows] == [
        ("BTC", "LONG", "MetaMask"), ("xyz:NVDA", "SHORT", "MetaMask"), ("HYPE", "LONG", "0xb2b2…b2b2")]
    for r in rows:
        assert r["origin"] == "saved_wallet" and r["access"] == "read-only", r
    # quoted verbatim, never recomputed
    assert rows[1]["position_value_usd"] == "800.00"


def test_the_budget_stays_senpi_money_only():
    """A saved wallet can't be deployed: its value is never a budget, whatever it holds."""
    c = _Client({"success": True, "data": {"external_wallets": [_wallet(CW_A, "Main", _state([_pos("BTC")]))]}})
    assert discover.fetch_user_context(c, saved_wallets=True)["budget"] == 40.0


def test_a_failed_saved_wallets_read_is_unavailable_never_none_and_never_breaks_the_context():
    for external in (RuntimeError("tool failed: UNAVAILABLE: moxie timeout"),
                     {"success": False, "error": {"code": "UNAVAILABLE"}},
                     {"success": True, "data": {}},
                     None):
        ctx = discover.fetch_user_context(_Client(external), saved_wallets=True)
        assert ctx["saved_wallets_status"] == "unavailable", external
        assert ctx["saved_wallet_holdings"] is None, f"{external}: unknown must never read as []"
        assert ctx["budget"] == 40.0 and ctx["holdings"] == ["ETH"], "the Senpi context survives"
    assert "moxie timeout" in discover.fetch_user_context(
        _Client(RuntimeError("tool failed: UNAVAILABLE: moxie timeout")), saved_wallets=True)["_saved_wallets_error"]


def test_none_saved_is_ok_and_empty():
    ctx = discover.fetch_user_context(_Client({"success": True, "data": {"external_wallets": []}}),
                                      saved_wallets=True)
    assert ctx["saved_wallets_status"] == "ok" and ctx["saved_wallet_holdings"] == []


def test_a_wallet_whose_state_could_not_load_is_named_never_flat():
    c = _Client({"success": True, "data": {"external_wallets": [
        _wallet(CW_A, "Main", None),
        _wallet(CW_B, "Cold", {"readAt": "2026-10-03T12:00:00Z", "readError": "PERPS_UNAVAILABLE",
                               "positions": None}),
        _wallet("0x" + "c3" * 20, "Live", _state([_pos("SOL")]))]}})
    ctx = discover.fetch_user_context(c, saved_wallets=True)
    assert ctx["saved_wallets_unread"] == ["Main", "Cold"]
    assert [r["wallet"] for r in ctx["saved_wallet_holdings"]] == ["Live"]


def test_the_match_run_does_not_read_saved_wallets():
    """Only `--context-only` reads them: the match run attaches the budget, and a saved wallet is never one."""
    c = _Client(RuntimeError("must not be called"))
    ctx = discover.fetch_user_context(c)
    assert c.calls == ["account_get_portfolio"]
    assert "saved_wallets_status" not in ctx


def test_context_only_carries_the_saved_wallets(monkeypatch):
    c = _Client({"success": True, "data": {"external_wallets": [_wallet(CW_A, "Main", _state([_pos("BTC")]))]}})
    monkeypatch.setattr(discover, "_get_client", lambda: c)
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert discover.main(["--context-only"]) == 0
    uc = json.loads(buf.getvalue())["user_context"]
    assert uc["saved_wallets_status"] == "ok" and uc["saved_wallet_holdings"][0]["coin"] == "BTC"


# ── the SKILL.md surface ────────────────────────────────────────────────────────────────────────
def _skill():
    with open(SKILL, encoding="utf-8") as f:
        return " ".join(f.read().split())


def _section():
    text = _skill()
    start = "Optionally `discover.py --context-only` to reference holdings"
    assert start in text
    return text.split(start, 1)[1].split("Then run the engine with the FULL concrete flag set", 1)[0]


def test_skill_reads_saved_wallets_as_context_never_as_a_deploy_target():
    sec = _section()
    for needle in ("(confirm first; never silently infer)",
                   "`saved_wallet_holdings`",
                   "a pick must not double an exposure they already hold",
                   "The budget stays Senpi money only",
                   "never a deploy target",
                   "every pick is a Senpi strategy deployed with Senpi money",
                   '`saved_wallets_status: "unavailable"`',
                   "I couldn't load your saved wallets",
                   "`saved_wallets_unread`",
                   'never "flat"',
                   '"your wallets"'):
        assert needle in sec, needle


def test_skill_section_never_calls_a_saved_wallet_connected_verified_or_owned():
    sec = _section()
    bad = re.findall(r"(?i)connect(?!ion)|\bverified\b|\bproo?f\b|\bprov(e|ed|en)\b|\b(you|they) own\b|"
                     r"\bowned by\b|\bowning\b", sec)
    assert not bad, bad
    for m in re.finditer(r"on senpi\.ai \(web\)", sec):
        assert sec[:m.end()].endswith("add it in Your wallets on senpi.ai (web)"), sec[max(0, m.start() - 60):m.end()]
