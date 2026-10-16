"""Bison held multi-day conviction trades behind a stop 0.8-1.1% from entry.

Leverage was clamped to 7-10x and the phase-1 cap is 8% of margin, which at 10x is a 0.8% price move
on BTC/ETH/SOL. Live: 77 of 132 closes were stops at -6.1% ROE, and after a stop 64% of them traded
back through entry within 4 hours and 40% reached the first profit lock within 24. The thesis needs
room to breathe, so the clamp is now 4-5x: the same 8% margin cap sits 1.6-2.0% away.

Sizing (25/31/37% margin) and the ratchet ladder are unchanged.
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
CATALOG = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))["catalog"]


def test_the_stop_has_room_for_a_multi_day_hold():
    assert (scan._MIN_LEVERAGE, scan._MAX_LEVERAGE) == (4, 5)
    stop_pct_of_margin = RUNTIME["exit"]["dsl_preset"]["phase1"]["max_loss_pct"]
    assert stop_pct_of_margin / scan._MAX_LEVERAGE >= 1.5      # at least 1.5% of price at the top clamp


def test_the_configured_leverage_matches_the_clamp():
    signals = next(s for s in RUNTIME["scanners"] if s["name"].endswith("_signals"))
    assert signals["inputs"]["leverageDefault"] == 5
    assert RUNTIME["strategy"]["default_leverage"] == 5
    assert CATALOG["leverage_max"] == 5


def test_sizing_and_the_ladder_are_unchanged():
    tiers = [t["trigger_pct"] for t in RUNTIME["exit"]["dsl_preset"]["phase2"]["tiers"]]
    assert tiers == [20, 30, 50, 75, 100]
