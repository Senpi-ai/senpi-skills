#!/usr/bin/env python3
"""The newest close was dated but never aged, so "is this still trading?" was a subtraction done by
eye — and a strategy holding a book it opened days ago read as actively trading.

    python3 -m pytest senpi-portfolio/tests/test_close_recency.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import portfolio  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="senpi-recency-tests-")
WALLET = "0xLIVE0000000000000000000000000000000live"


def _runtimes():
    fd, path = tempfile.mkstemp(suffix=".json", dir=_TMP)
    with os.fdopen(fd, "w") as fh:
        json.dump({"ok": True, "runtimes": [
            {"id": "live-main", "wallet": WALLET, "status": "running",
             "descriptor": {"name": "live-main", "group": "live", "description": "deployed"}}]}, fh)
    return path


def _mcp(close_age_hours=1, positions=(), with_closed=True):
    """One ACTIVE strategy. `close_age_hours` dates its newest close; `with_closed=False` gives it no
    closed record at all — the fresh-deploy case."""
    closes = [{"coin": "ETH", "type": "Close Long", "szi": "1", "realizedPnl": "12.5",
               "closeTime": int(time.time() - close_age_hours * 3600)}] if with_closed else []
    return {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 500, "total_withdrawable": 500,
                                  "total_usdc_in_hyperliquid": 0, "token_balances": []},
        "strategy_list": {"strategies": [
            {"strategyName": "live", "tradingStrategyName": "live",
             "strategyMetadata": {"skillName": "live", "skillVersion": "1.0.0"},
             "strategyWalletAddress": WALLET, "status": "ACTIVE", "totalFunded": 500}]},
        f"strategy_get_clearinghouse_state::{WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "500"}, "withdrawable": "500",
                     "assetPositions": list(positions)},
            "xyz": {"marginSummary": {"accountValue": "500"}, "withdrawable": "500",
                    "assetPositions": []}},
        f"discovery_get_trader_history::{WALLET.lower()}": {"closedPositions": closes},
    }


def _run(mcp_fixture=None):
    saved = os.environ.get("SENPI_RUNTIMES_FIXTURE")
    os.environ["SENPI_RUNTIMES_FIXTURE"] = _runtimes()
    try:
        return portfolio.run(portfolio._FixtureClient(mcp_fixture or _mcp()))
    finally:
        if saved is None:
            os.environ.pop("SENPI_RUNTIMES_FIXTURE", None)
        else:
            os.environ["SENPI_RUNTIMES_FIXTURE"] = saved


def _row(out):
    return next(s for s in out["strategies"] if str(s["wallet"]).lower() == WALLET.lower())


def test_last_close_carries_its_age():
    row = _row(_run(_mcp(close_age_hours=5)))
    assert row["last_close_utc"] is not None
    assert row["hours_since_last_close"] == 5


def test_a_strategy_holding_a_stale_book_is_reported_quiet():
    """The case this exists for: open positions, newest close 60h old, reads as actively trading."""
    pos = [{"position": {"coin": "GOLD", "szi": "2", "positionValue": "120", "marginUsed": "40",
                         "entryPx": "1", "unrealizedPnl": "-1", "returnOnEquity": "-0.02"}}]
    meta = _run(_mcp(close_age_hours=60, positions=pos))["meta"]
    assert meta["quiet_strategies"][0]["hours"] == 60
    assert meta["quiet_strategies"][0]["holding"] == 1
    # reported as DATA, never as a fault — `warnings` is for degraded / not running / failed reads,
    # and a slow-clock sleeve holding by design must not train anyone to skip that channel
    assert not any("CLOSED a trade" in w for w in meta["warnings"])


def test_a_recent_close_is_not_quiet():
    assert "quiet_strategies" not in _run(_mcp(close_age_hours=2))["meta"]


def test_the_threshold_fires_at_the_hour_the_constant_names():
    """`// 3600` floors the reported AGE, which does not move the boundary: floor(e/3600) >= 48 is
    e >= 48h exactly. 47h59m is out, 48h00m is in."""
    assert "quiet_strategies" not in _run(_mcp(close_age_hours=portfolio.QUIET_AFTER_HOURS - 1/60.))["meta"]
    assert _run(_mcp(close_age_hours=portfolio.QUIET_AFTER_HOURS))["meta"]["quiet_strategies"]


def test_a_strategy_with_no_closed_record_is_never_called_quiet():
    """`strategy_list` carries no creation date, so flagging "never closed" fires on every fresh deploy."""
    out = _run(_mcp(with_closed=False))
    assert _row(out)["hours_since_last_close"] is None
    assert "quiet_strategies" not in out["meta"]


def test_age_hours_reads_utc_without_a_local_timezone_shift():
    """`timegm`, not `mktime`. The TZ has to be PINNED: on a UTC host both agree and the test cannot
    tell them apart — measured mktime=3 under UTC, 0 under Chicago, 12 under Tokyo. Both signs of
    offset, so an implementation wrong in only one direction still fails."""
    saved = os.environ.get("TZ")
    try:
        for tz in ("America/Chicago", "Asia/Tokyo"):
            os.environ["TZ"] = tz
            time.tzset()
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 3600))
            assert portfolio._age_hours(stamp) == 3, tz
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


def test_age_hours_never_invents_an_age():
    for bad in (None, "", "not-a-date", 12345, "2026-13-45T99:99:99Z"):
        assert portfolio._age_hours(bad) is None


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
