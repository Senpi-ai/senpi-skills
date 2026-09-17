"""Athena's hedge sleeve churned, and its copy claimed things the code does not do.

Aegis makes 77% of Athena's fills and its fees exceeded its gross profit over the 30-day window. The
8h weak-peak cut closed 61 positions, 16 of which were re-entered on the same asset within 30 minutes
despite the 2h per-asset cooldown: a hedge that is still the right hedge should not be retired by a
clock. Phalanx dropped its time cuts for the same reason (#609).

The copy also said the strategy "goes to cash" in a neutral regime. A neutral regime only stops NEW
entries (aegis scan.py) — nothing closes — so the card promised a flat book that never happens.
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
STRATEGIES = os.path.join(HERE, "..", "..")
TIME_CUTS = ("hard_timeout", "weak_peak_cut", "dead_weight_cut")


def _preset(*parts):
    rt = yaml.safe_load(open(os.path.join(STRATEGIES, *parts), encoding="utf-8"))
    return rt["exit"]["dsl_preset"]


def _catalog_text(sid):
    cat = yaml.safe_load(open(os.path.join(STRATEGIES, sid, "strategy.yaml"), encoding="utf-8"))["catalog"]
    return " ".join(str(cat.get(k, "")) for k in ("tagline", "belief_plain", "thesis")).lower()


def test_no_clock_retires_the_hedge():
    for preset in (_preset("aegis", "main", "runtime.yaml"), _preset("athena", "aegis", "runtime.yaml")):
        live = [c for c in TIME_CUTS if (preset.get(c) or {}).get("enabled")]
        assert live == [], f"a time cut is back on the hedge: {live}"


def test_the_stop_and_the_ladder_are_still_the_exit():
    for preset in (_preset("aegis", "main", "runtime.yaml"), _preset("athena", "aegis", "runtime.yaml")):
        assert preset["phase1"]["max_loss_pct"] > 0
        assert preset["phase2"]["enabled"] and preset["phase2"]["tiers"]


def test_the_cards_do_not_promise_what_the_code_does_not_do():
    for sid in ("athena", "aegis"):
        text = _catalog_text(sid)
        for claim in ("cash in neutral", "sits in cash", "sit in cash", "goes to cash",
                      "bank gains", "doesn't bleed", "pay back losers", "guaranteed"):
            assert claim not in text, f"{sid} card claims '{claim}'"
