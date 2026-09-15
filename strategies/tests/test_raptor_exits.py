"""Raptor 1.1.1: the DSL is the exit — no clock closes a follow.

Raptor rides one proven trader's live conviction bet, and a whale's position runs for hours to
days. The 6h hard_timeout (it counts from entry and fires in either phase) closed follows that
had not yet reached the first tier and tier-armed winners alike; the 60m weak_peak_cut / 40m
dead_weight_cut closed follows that had merely not moved yet.
A follow now exits on the hard stop or the profit-lock ladder only. This pins that decision.

Run:
  python3 -m pytest strategies/tests/test_raptor_exits.py -q
"""
import pathlib

import yaml

_RUNTIME = pathlib.Path(__file__).resolve().parents[1] / "raptor" / "main" / "runtime.yaml"
_TIME_CUTS = ("hard_timeout", "weak_peak_cut", "dead_weight_cut")


def _preset():
    return yaml.safe_load(_RUNTIME.read_text(encoding="utf-8"))["exit"]["dsl_preset"]


def test_no_time_cut_is_enabled():
    live = [c for c in _TIME_CUTS if (_preset().get(c) or {}).get("enabled")]
    assert live == [], f"raptor re-grew a time cut: {live}"


def test_the_stop_and_the_ladder_are_the_exit():
    p = _preset()
    assert p["phase1"]["max_loss_pct"] > 0, "no hard stop — a follow with no clock needs one"
    triggers = [t["trigger_pct"] for t in p["phase2"]["tiers"]]
    assert p["phase2"]["enabled"] and triggers == sorted(triggers) and triggers, "no ratchet ladder"
