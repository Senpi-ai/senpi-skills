"""Gannet Duo: Gannet's dip cascade across two slots at half the size, pinned.

Everything but the slot count and the per-slot margin must equal Gannet's — the markets and their
order, the RSI line, the leverage per market, the stop and the ladder — so the two differ only in
how many washed-out markets they hold at once. The DSL decides every exit.

Run: python3 -B -m pytest strategies/gannet-duo/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))
import scan  # noqa: E402,F401  (imports cleanly from this package's own copy)

STRATS = os.path.join(HERE, "..", "..")


def _load(pkg):
    rt = yaml.safe_load(open(os.path.join(STRATS, pkg, "main", "runtime.yaml"), encoding="utf-8"))
    man = yaml.safe_load(open(os.path.join(STRATS, pkg, "strategy.yaml"), encoding="utf-8"))
    sig = next(s for s in rt["scanners"] if s["type"] == "external_scanner")
    return rt, man, dict(sig["inputs"])


RT, MAN, INPUTS = _load("gannet-duo")
PRT, PMAN, PINPUTS = _load("gannet")


def test_two_slots_at_half_the_parents_margin():
    assert INPUTS["maxSlots"] == RT["strategy"]["slots"] == MAN["catalog"]["max_slots"] == 2
    assert INPUTS["marginPct"] == RT["strategy"]["margin_pct"] == PINPUTS["marginPct"] / 2 == 15
    assert MAN["catalog"]["varies"] == "gannet"


def test_everything_else_is_gannets():
    skip = {"marginPct", "maxSlots"}
    assert {k: v for k, v in INPUTS.items() if k not in skip} == {k: v for k, v in PINPUTS.items() if k not in skip}
    assert RT["exit"] == PRT["exit"]
    assert MAN["catalog"]["assets"] == PMAN["catalog"]["assets"] == INPUTS["allowedAssets"]
    assert MAN["catalog"]["leverage_max"] == PMAN["catalog"]["leverage_max"]


def test_no_clock_closes_anything_and_the_stop_agrees():
    dsl = RT["exit"]["dsl_preset"]
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert dsl[cut]["enabled"] is False
    assert dsl["phase1"]["enabled"] is False and dsl["phase1"]["max_loss_pct"] == INPUTS["stopRoePct"]


def test_the_rails_fit_two_slots():
    rails = RT["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RT["strategy"]["slots"]
    two_stops = 2 * INPUTS["stopRoePct"] * INPUTS["marginPct"] / 100
    assert rails["daily_loss_limit_pct"] > two_stops
    assert rails["drawdown_reset_on_day_rollover"] is False


def test_the_measured_parameters_are_the_shipped_ones():
    """Replay (12 Nov 2026) on US daily bars Mar 2010 - Nov 2026 (SOXL; QQQ for XYZ100; SPY for
    SP500), Gannet's rule and exits with two slots, 0.18% fees per round trip: 146 trades, profit
    factor 2.45, 2010-14 2.60, 2014-18 2.42, 2018-22 1.80, 2022-26 2.94, worst drawdown 13.0% at 15%
    a slot. All 162 neighbouring cells (RSI 8/10/12 x line 28/30/32 x stop 15/21/30 x three ladders x
    both arming bounds) above 1.0, the worst 1.89."""
    assert (INPUTS["rsiPeriod"], INPUTS["oversoldRsi"], INPUTS["stopRoePct"], INPUTS["maxSlots"]) == (10, 30, 21, 2)
