#!/usr/bin/env python3
"""The Senpi main wallet is a row of the book (R1 dev E2E round 2, A3).

Every wallet first-class: the trade review's `book.rows` lists the Senpi main wallet beside the
strategies and the wallets the user added, valued by its own idle cash — read the way senpi-portfolio
reads it (ONE `account_get_portfolio`, forceFetch, the vendored main-wallet reader). Without it the
managed subtotal missed the main wallet: live, portfolio said "managed by Senpi $471" and this review
"$310.36" for the same account in the same minute. The reconciliation test below holds the two skills'
managed subtotals equal on one fixture.

    python3 -m pytest senpi-improve-trades/tests/test_main_wallet_row.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import importlib.util
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import test_review_external_wallets as T  # noqa: E402
import review  # noqa: E402

PORTFOLIO_PY = os.path.join(HERE, "..", "..", "senpi-portfolio", "scripts", "portfolio.py")
SKILL = os.path.join(HERE, "..", "SKILL.md")
MAIN = "0x" + "e1" * 20
PORTFOLIO = {"portfolio": {"total_in_hyperliquid": "120.50", "total_spot_usd_in_hyperliquid": "39.90",
                           "token_balances": [{"symbol": "USDC", "balanceInUSD": "0.05", "chain": "base"},
                                              {"symbol": "HYPE", "balanceInUSD": "999.00"}],
                           "total_balance_usd": "1660.45"}}


def _fx(portfolio=PORTFOLIO, embedded=True, me=True):
    fx = T._two_saved(T._valued_base(), T._valued_state("900.00"), T._valued_state("5000.00", "FULL"))
    if embedded:
        fx["user_get_me"]["user"]["wallets"] = [{"walletType": "embedded", "walletAddress": MAIN}]
    if not me:
        fx["user_get_me"] = {"success": False, "error": "UNAVAILABLE"}
    if portfolio is not None:
        fx["account_get_portfolio"] = portfolio
    return fx


def _main(rows):
    found = [r for r in rows if r.get("origin") == "main_wallet"]
    assert len(found) <= 1
    return found[0] if found else None


def test_the_main_wallet_is_a_managed_row_valued_by_its_own_idle_cash_and_sorted_by_value():
    res, _ = T._run(_fx())
    rows = res["book"]["rows"]
    assert [(r["label"], r["kind"], r["value_usd"]) for r in rows] == [
        ("Ledger", "read_only", 5000.0), ("kodiak", "managed", 1500.0), ("MetaMask", "read_only", 900.0),
        ("Senpi main wallet", "managed", 160.45)]
    m = _main(rows)
    # perps 120.50 + spot 39.90 + EVM USDC 0.05 — a non-stable token is never idle cash
    assert m["wallet"] == MAIN and m["value_read"] == "ok" and m["holds"] == "cash"
    assert m["closed_trade_count"] is None and m["protection"] is None and m["trades_unknown"] is False
    man = res["book"]["total"]["managed"]
    assert man["value_usd"] == 1660.45 and man["wallet_count"] == 2
    assert "managed by Senpi $1,660.45 (2 wallets)" in res["book"]["total"]["line"]


def test_the_main_wallet_read_is_the_one_portfolio_makes():
    _res, client = T._run(_fx(), T.Recording)
    calls = [kw for t, kw in client.calls if t == "account_get_portfolio"]
    assert calls and all(kw.get("forceFetch") is True and kw.get("strategyStatus") == "ALL" for kw in calls)


def test_a_failed_main_wallet_read_is_couldnt_load_never_zero_and_named():
    for bad in ({"success": False, "error": "UNAVAILABLE"}, None):
        res, _ = T._run(_fx(portfolio=bad))
        rows = res["book"]["rows"]
        m = _main(rows)
        assert m is not None and m["value_usd"] is None and m["value_read"] == "unavailable", bad
        assert rows[-1] is m, "couldn't-load sorts last"
        total = res["book"]["total"]
        assert "Senpi main wallet" in total["excludes"]["couldnt_load"]
        assert total["managed"]["value_usd"] == 1500.0 and total["managed"]["wallets_loaded"] == 1
        assert "managed by Senpi $1,500.00 (1 of 2 wallets)" in total["line"]
        assert "excludes 1 wallet that couldn't load (Senpi main wallet)" in total["line"]


def test_an_unread_user_get_me_still_lists_the_main_wallet():
    res, _ = T._run(_fx(embedded=False, me=False))
    m = _main(res["book"]["rows"])
    assert m is not None and m["wallet"] is None and m["value_usd"] == 160.45
    res, _ = T._run(_fx(portfolio=None, embedded=False, me=False))
    m = _main(res["book"]["rows"])
    assert m is not None and m["value_usd"] is None, "unknown, never absent"


def test_no_main_wallet_named_and_none_read_adds_no_row():
    res, _ = T._run(_fx(portfolio=None, embedded=False))
    assert _main(res["book"]["rows"]) is None


def test_the_main_wallet_is_never_compared_or_offered_for_a_trade_deep_dive():
    res, _ = T._run(_fx())
    book = res["book"]
    assert all(w["label"] != "Senpi main wallet" for w in book["comparison"]["wallets"])
    assert all(n["label"] != "Senpi main wallet" for n in book["comparison"]["not_compared"])
    assert "Senpi main wallet" not in (book["deep_dive"] or {}).get("order", [])


def test_the_strategies_step_carries_the_main_wallet_too():
    saved = T._env()
    try:
        path = tempfile.mktemp(suffix=".json")
        client = review._FixtureClient(_fx())
        review.step_timing(client, window_days=T.WINDOW_DAYS, want_market=False, state_path=path, now_ms=T.NOW_MS)
        out = review.step_strategies(client, window_days=T.WINDOW_DAYS, want_market=False, state_path=path,
                                     now_ms=T.NOW_MS)
    finally:
        T._restore(saved)
    m = _main(out["book"]["rows"])
    assert m is not None and m["wallet"] == MAIN and m["value_usd"] == 160.45


def _portfolio_module():
    spec = importlib.util.spec_from_file_location("senpi_portfolio_reconcile", PORTFOLIO_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_managed_subtotal_reconciles_with_senpi_portfolio_on_the_same_reads():
    """THE A3 guard: one fixture, both skills — improve-trades' managed subtotal == portfolio's
    `managed_usd`, and the main wallet row is the same number as portfolio's main wallet row."""
    pf = _portfolio_module()
    fx = _fx()
    saved = T._env()
    try:
        res, _ = T._run(fx)
        money = pf.step_money(pf._FixtureClient(fx), state_path=tempfile.mktemp(suffix=".json"))
    finally:
        T._restore(saved)
    totals = money["book"]["totals"]
    assert totals["managed_usd"] == res["book"]["total"]["managed"]["value_usd"] == 1660.45
    assert totals["read_only_usd"] == res["book"]["total"]["read_only"]["value_usd"]
    pf_main = [w for w in money["book"]["wallets"] if w["origin"] == "embedded"][0]
    assert pf_main["value_usd"] == _main(res["book"]["rows"])["value_usd"]
    assert pf_main["label"] == _main(res["book"]["rows"])["label"]


def test_skill_names_the_main_wallet_row():
    with open(SKILL, encoding="utf-8") as f:
        text = " ".join(f.read().split())
    assert "Senpi main wallet" in text
    assert re.search(r"managed subtotal[^.]*main wallet", text), "SKILL.md says the managed subtotal includes it"
