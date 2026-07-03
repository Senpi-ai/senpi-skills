#!/usr/bin/env python3
"""Offline engine test — runs smartmoney.run() + the step subcommands against a recorded MCP fixture
(no network).

    python3 -m pytest senpi-smart-money/tests/   # or: python3 tests/test_smartmoney.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import smartmoney  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "smartmoney_fixture.json")


def _client():
    with open(FIXTURE) as f:
        return smartmoney._FixtureClient(json.load(f))


def _result():
    return smartmoney.run(_client(), want_near=True)


def _tmp_state():
    return os.path.join(tempfile.mkdtemp(), "state.json")


def _canon(obj):
    return json.dumps(obj, sort_keys=True)


def test_cohorts_built():
    c = _result()["cohorts"]
    assert c["smart"]["members_sampled"] >= 2   # 0xsmart1/2 land in the >=$1M band
    assert c["crowd"]["members_sampled"] >= 2   # 0xcrowd1/2 land in the $10-100k band


def test_smart_leaning_headline():
    """The proven cohort: short HYPE, long BTC — both above the lean threshold with enough members."""
    leaning = {x["asset"]: x for x in _result()["smart_leaning"]}
    assert leaning["HYPE"]["direction"] == "short" and leaning["HYPE"]["members"] >= 5
    assert leaning["BTC"]["direction"] == "long" and leaning["BTC"]["members"] >= 5


def test_divergence_opposite_sides():
    """The core signal: smart short HYPE while the crowd is long it — flagged opposite_sides."""
    div = {x["asset"]: x for x in _result()["divergences"]}
    assert "HYPE" in div, "HYPE divergence not detected"
    h = div["HYPE"]
    assert h["opposite_sides"] is True
    assert h["smart_direction"] == "short" and h["crowd_direction"] == "long"
    assert h["smart_members"] >= 5 and h["crowd_members"] >= 5


def test_near_term_present_when_healthy():
    res = _result()
    assert res["meta"]["near_term_available"] is True
    assert res["near_term"]["concentration"]["concentration"][0]["asset"] == "HYPE"


def test_cohorts_unavailable_flag_on_empty():
    """No discovery data (e.g. app-scoped token) → flagged honestly, no exception."""
    res = smartmoney.run(smartmoney._FixtureClient({}), want_near=True)
    assert res["meta"].get("cohorts_unavailable")
    assert res["smart_leaning"] == [] and res["divergences"] == []


# ──────────────────────────────────────────────────────────── step subcommands (fast, resumable)
def test_step_cohorts_emits_its_slice():
    """`cohorts` step emits ONLY the cohort slice (headline + divergence table), no near_term, offline."""
    res = smartmoney.step_cohorts(_client(), want_near=True, state_path=_tmp_state())
    assert set(res.keys()) == {"as_of", "cohorts", "smart_leaning", "divergences", "meta"}
    assert "near_term" not in res
    div = {x["asset"]: x for x in res["divergences"]}
    assert div["HYPE"]["opposite_sides"] is True   # the core signal survives the step boundary


def test_step_near_term_emits_its_slice():
    """`near_term` step layers the 4h flow onto the (self-healed) cohort headline, offline."""
    res = smartmoney.step_near_term(_client(), want_near=True, state_path=_tmp_state())
    assert "near_term" in res and res["meta"]["near_term_available"] is True
    assert res["near_term"]["concentration"]["concentration"][0]["asset"] == "HYPE"


def test_cohorts_then_near_term_reproduces_all():
    """cohorts → near_term over a SHARED state file reproduces the composed `all` output, field by field."""
    all_res = _result()
    statep = _tmp_state()
    c_res = smartmoney.step_cohorts(_client(), want_near=True, state_path=statep)
    n_res = smartmoney.step_near_term(_client(), want_near=True, state_path=statep)
    composed = {"as_of": n_res["as_of"], "cohorts": c_res["cohorts"],
                "smart_leaning": c_res["smart_leaning"], "divergences": c_res["divergences"],
                "near_term": n_res["near_term"]}
    for k in ("as_of", "cohorts", "smart_leaning", "divergences", "near_term"):
        assert _canon(all_res[k]) == _canon(composed[k]), f"{k} diverged from all"


def test_near_term_self_heals_on_absent_state():
    """`near_term` with NO prior state re-runs the cohort fetch itself → still produces the full read."""
    all_res = _result()
    res = smartmoney.step_near_term(_client(), want_near=True, state_path=_tmp_state())
    assert _canon(res["divergences"]) == _canon(all_res["divergences"])
    assert res["meta"]["near_term_available"] is True


def test_near_term_fails_open_on_corrupt_state():
    """A corrupt state file → fail-open recompute (self-heal), never an exception."""
    all_res = _result()
    statep = _tmp_state()
    os.makedirs(os.path.dirname(statep), exist_ok=True)
    with open(statep, "w") as fh:
        fh.write("{ not valid json ]]")
    res = smartmoney.step_near_term(_client(), want_near=True, state_path=statep)
    assert _canon(res["divergences"]) == _canon(all_res["divergences"])


def test_step_cohorts_unavailable_flag_offline():
    """Empty discovery → `cohorts` step flags cohorts_unavailable and near_term reads it from state."""
    statep = _tmp_state()
    ec = smartmoney.step_cohorts(smartmoney._FixtureClient({}), want_near=True, state_path=statep)
    assert ec["meta"].get("cohorts_unavailable")
    assert ec["smart_leaning"] == [] and ec["divergences"] == []
    en = smartmoney.step_near_term(smartmoney._FixtureClient({}), want_near=True, state_path=statep)
    assert en["meta"].get("cohorts_unavailable")   # propagated through the shared state


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
