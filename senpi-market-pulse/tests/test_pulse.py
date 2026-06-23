#!/usr/bin/env python3
"""Offline engine test — runs pulse.run() against a recorded MCP fixture (no network).

    python3 -m pytest senpi-market-pulse/tests/        # or: python3 tests/test_pulse.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

import pulse  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "pulse_fixture.json")


def _result():
    with open(FIXTURE) as f:
        client = pulse._FixtureClient(json.load(f))
    return pulse.run(client, want_smart=True)


def test_all_classes_present():
    """Every asset class is always returned — never crypto-only."""
    g = _result()["groups"]
    for required in ("crypto", "semis_memory", "software_megacap", "indices", "commodities", "macro_fx"):
        assert required in g, f"missing group {required}"
    assert g["crypto"]["avg_change_pct"] is not None
    assert g["crypto"]["avg_change_pct"] < 0  # the fixture is a down day for crypto


def test_day_classified_risk_off():
    sig = _result()["signals"]
    assert sig["day_classification"]["label"] == "risk_off"
    assert sig["day_classification"]["groups_down"] >= 3


def test_dispersion_read():
    """SP500 calm while memory names break → the engine should flag dispersion, not capitulation."""
    sig = _result()["signals"]
    disp = sig["dispersion"]
    assert disp["worst_group"] in ("semis_memory", "semis_equipment")
    assert "dispersion" in (disp["read"] or "")


def test_confirmation_checklist():
    sig = _result()["signals"]
    assert "haven bid intact" in (sig["gold"]["read"] or "")     # gold only -1.2%
    assert "no funding stress" in (sig["dxy"]["read"] or "")     # DXY flat
    assert "contained" in (sig["vix"]["read"] or "")             # VIX 20 < 22


def test_movers_get_volume():
    """The biggest movers should be deep-pulled and carry volume conviction."""
    groups = _result()["groups"]
    rows = [r for g in groups.values() for r in g["rows"]]
    assert any(r.get("volume_usd") for r in rows), "no mover got a volume read"


def test_smart_money_present_when_healthy():
    res = _result()
    assert res["meta"]["smart_money_available"] is True
    assert res["smart_money"]["concentration"]["concentration"][0]["asset"] == "HYPE"


def test_fails_open_on_empty():
    """No data anywhere → still valid structure, flagged degraded, no exception."""
    res = pulse.run(pulse._FixtureClient({}), want_smart=True)
    assert "groups" in res and "meta" in res
    assert res["meta"].get("degraded")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} passed")
