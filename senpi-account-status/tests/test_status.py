#!/usr/bin/env python3
"""Offline engine test — runs status.run() against a recorded MCP fixture (no network)."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import status  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "status_fixture.json")


def _result():
    with open(FIXTURE) as f:
        return status.run(status._FixtureClient(json.load(f)))


def test_identity_and_points():
    r = _result()
    assert r["identity"]["senpi_user_id"] == "u123"
    assert r["identity"]["wallet"] == "0xme"          # embedded, not external
    assert r["points"]["total"] == 15000 and r["points"]["rank"] == 42.0


def test_loyalty_milestone():
    loy = _result()["loyalty"]
    assert loy["tier"] == "Silver" and loy["fee_bps"] == 4.5
    assert loy["next_tier"] == "Gold" and loy["points_to_next"] == 5000.0


def test_arena_standing():
    a = _result()["arena"]
    assert a["enrolled"] is True and a["rank"] == 5.0
    assert a["roe_pct"] == 18.5 and a["qualified"] is True
    assert a["week_pool_usd"] == 5000 and a["prize_estimate_usd"] == 400.0   # rank-5 prize


def test_referral_and_wins():
    r = _result()
    assert r["referral"]["balance_usdc"] == 12.5
    assert len(r["wins"]) == 2 and r["wins"][0]["asset"] == "ETH"


def test_fails_open_on_empty():
    r = status.run(status._FixtureClient({}))
    assert "points" in r and r["meta"].get("degraded")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
