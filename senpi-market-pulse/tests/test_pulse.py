#!/usr/bin/env python3
"""Offline engine test — runs pulse.run() against a recorded MCP fixture (no network).

    python3 -m pytest senpi-market-pulse/tests/        # or: python3 tests/test_pulse.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

import pulse  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "pulse_fixture.json")


def _client():
    with open(FIXTURE) as f:
        return pulse._FixtureClient(json.load(f))


def _result():
    return pulse.run(_client(), want_smart=True)


def _state_path():
    """A fresh state-file path in a throwaway temp dir (the file itself does not exist yet)."""
    return os.path.join(tempfile.mkdtemp(prefix="pulse-test-"), "state.json")


def test_all_classes_present():
    """Every asset class is always returned — never crypto-only."""
    g = _result()["groups"]
    for required in ("crypto", "semis_memory", "software_megacap", "indices", "commodities", "macro_fx"):
        assert required in g, f"missing group {required}"
    assert g["crypto"]["avg_change_pct"] is not None
    assert g["crypto"]["avg_change_pct"] < 0  # the fixture is a down day for crypto


def test_context_nested_quotes_extracted():
    """Regression: live market_list_instruments nests markPx/prevDayPx under `context`.
    The fixture uses that real shape; prices must still come through (not all-null)."""
    g = _result()["groups"]
    btc = next(r for r in g["crypto"]["rows"] if r["asset"] == "BTC")
    assert btc["price"] == 62515 and btc["change_pct"] is not None
    # xyz rows arrive `xyz:`-prefixed — the symbol must be normalized and matched
    sp500 = next(r for r in g["indices"]["rows"] if r["asset"] == "SP500")
    assert sp500["price"] == 7396


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


# ──────────────────────────────────────────────────────────── streaming steps (pulse · smart · all)
def test_step_pulse_emits_only_its_slice():
    """`pulse` = the FAST core market read: groups/signals/day_classification, NO smart_money key."""
    p = pulse.step_pulse(_client(), want_smart=True, state_path=_state_path())
    assert set(p.keys()) == {"as_of", "day_classification", "signals", "groups", "meta"}
    assert "smart_money" not in p                       # the overlay is the separate `smart` step
    assert p["day_classification"]["label"] == "risk_off"
    assert p["groups"]["crypto"]["avg_change_pct"] is not None
    assert p["signals"]["funding_regime"] == "neutral"  # movers were deep-pulled (funding regime folded in)


def test_step_smart_emits_only_its_slice():
    """`smart` = the heavier overlay: prints ONLY {smart_money, meta} (with smart_money_available)."""
    s = pulse.step_smart(_client(), want_smart=True, state_path=_state_path())
    assert set(s.keys()) == {"smart_money", "meta"}
    assert s["meta"]["smart_money_available"] is True
    assert s["smart_money"]["concentration"]["concentration"][0]["asset"] == "HYPE"


def test_pulse_then_smart_reproduces_all():
    """pulse → smart over the SHARED state file reproduces the composed `all` (core + overlay)."""
    sp = _state_path()
    p = pulse.step_pulse(_client(), want_smart=True, state_path=sp)
    s = pulse.step_smart(_client(), want_smart=True, state_path=sp)   # reads the state pulse wrote
    composed = {
        "as_of": p["as_of"],
        "day_classification": p["day_classification"],
        "signals": p["signals"],
        "groups": p["groups"],
        "smart_money": s["smart_money"],
    }
    ref = pulse.run(_client(), want_smart=True)
    assert composed == {k: ref[k] for k in composed}


def test_all_is_byte_identical_to_run():
    """`all` (via _all_and_persist) prints byte-for-byte what the untouched run() produced."""
    ref = json.dumps(pulse.run(_client(), want_smart=True), ensure_ascii=False)
    got = json.dumps(pulse._all_and_persist(_client(), True, _state_path()), ensure_ascii=False)
    assert got == ref


def test_smart_self_heals_on_absent_state():
    """`smart` standalone (no prior `pulse`, empty state dir) self-heals the core read, still overlays."""
    sp = _state_path()                                  # dir exists, state file does NOT
    s = pulse.step_smart(_client(), want_smart=True, state_path=sp)
    assert s["smart_money"] is not None                 # overlay still landed
    st = json.load(open(sp))                            # and the recomputed core was persisted
    assert "crypto" in (st.get("groups") or {})


def test_step_fails_open_on_corrupt_state():
    """A corrupt/unreadable state file → recompute (never crash); the slice is still valid."""
    sp = _state_path()
    with open(sp, "w") as fh:
        fh.write("{ this is not valid json ]]]")
    p = pulse.step_pulse(_client(), want_smart=True, state_path=sp)   # pulse overwrites the corrupt file
    assert p["groups"]["crypto"]["avg_change_pct"] is not None
    s = pulse.step_smart(_client(), want_smart=True, state_path=sp)
    assert s["smart_money"] is not None


def test_step_no_smart_yields_null_overlay():
    """`smart --no-smart` returns a clean null overlay (available False), no exception."""
    s = pulse.step_smart(_client(), want_smart=False, state_path=_state_path())
    assert s["smart_money"] is None and s["meta"]["smart_money_available"] is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} passed")
