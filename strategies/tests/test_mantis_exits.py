"""Mantis 1.1.1: a laggard gets its whole lag window — the 20min weak-peak cut is gone, the
240min lag-window ceiling stays.

The edge is a statistical lag: the alt catches up to BTC within its typical lag window (the
model's own timeout is avg_lag x 1.5, clamped to [30, 240] minutes). A 20min weak_peak_cut fired
before that window could open. The hard_timeout is kept on purpose: it is the ceiling of the
model's own window (it counts from entry and fires in either phase), and past it the catchup
either never materialised or has already played out — holding longer is the directional bet the
thesis disclaims. This pins both.

Run:
  python3 -m pytest strategies/tests/test_mantis_exits.py -q
"""
import pathlib

import yaml

_RUNTIME = pathlib.Path(__file__).resolve().parents[1] / "mantis" / "main" / "runtime.yaml"


def _runtime():
    return yaml.safe_load(_RUNTIME.read_text(encoding="utf-8"))


def test_the_only_clock_is_the_lag_window():
    rt = _runtime()
    p = rt["exit"]["dsl_preset"]
    assert not (p.get("weak_peak_cut") or {}).get("enabled"), "the 20min weak-peak cut is back"
    assert not (p.get("dead_weight_cut") or {}).get("enabled"), "a dead-weight cut appeared"
    model_ceiling = next(s for s in rt["scanners"] if s["name"] == "mantis_main_signals")["inputs"][
        "hardTimeoutCeilingMinutes"]
    assert p["hard_timeout"]["enabled"] and p["hard_timeout"]["interval_in_minutes"] >= model_ceiling, (
        "the DSL ceiling must not fire inside the model's own lag window")


def test_the_stop_and_the_ladder_are_the_exit():
    p = _runtime()["exit"]["dsl_preset"]
    assert p["phase1"]["max_loss_pct"] > 0, "no hard stop"
    triggers = [t["trigger_pct"] for t in p["phase2"]["tiers"]]
    assert p["phase2"]["enabled"] and triggers == sorted(triggers) and triggers, "no ratchet ladder"
