"""Cheetah 1.1.1: the ratchet, not a 12h clock, decides when a winner is done.

Cheetah waits weeks for one confluence and then lets a ratcheting trailing stop — not a fixed
target — decide the exit. The 12h hard_timeout (it counts from entry and fires in either phase)
closed positions that had not reached the first tier — including ones sitting in profit below
it — and tier-armed winners alike. A position now exits on the hard stop or the profit-lock
ladder only. This pins that decision.

Run:
  python3 -m pytest strategies/tests/test_cheetah_exits.py -q
"""
import pathlib

import yaml

_RUNTIME = pathlib.Path(__file__).resolve().parents[1] / "cheetah" / "main" / "runtime.yaml"
_TIME_CUTS = ("hard_timeout", "weak_peak_cut", "dead_weight_cut")


def _preset():
    return yaml.safe_load(_RUNTIME.read_text(encoding="utf-8"))["exit"]["dsl_preset"]


def test_no_time_cut_is_enabled():
    live = [c for c in _TIME_CUTS if (_preset().get(c) or {}).get("enabled")]
    assert live == [], f"cheetah re-grew a time cut: {live}"


def test_the_stop_and_the_ladder_are_the_exit():
    p = _preset()
    assert p["phase1"]["max_loss_pct"] > 0, "no hard stop — a sniper with no clock needs one"
    triggers = [t["trigger_pct"] for t in p["phase2"]["tiers"]]
    assert p["phase2"]["enabled"] and triggers == sorted(triggers) and triggers, "no ratchet ladder"
