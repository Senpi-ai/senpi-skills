"""Chameleon entered before the ratio turned, and re-fired the same signal every scan.

Its own card says it waits for the ratio to start reverting, but "turning" was only worth +1 of the
score, so a still-stretching pair cleared the floor of 4 on extremity alone: live log
"EMIT ETH SHORT z=2.14 score=4" while z kept rising to 2.37. Turning is now the gate, not a point.

recentSignalTtlSeconds was 240s against a 300s scan, so the same pair re-signalled on every tick. It
is now 6h — one signal per pair per reversion, not per scan.
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scoring  # noqa: E402

PAIR = {"numerator": "A", "denominator": "B", "leg": "A"}
CFG = {"lookbackBars": 20, "zEntryMin": 2.0, "zStrong": 3.0, "smTiltMinPct": 100}
CLOSES = {"A": [100.0] * 30, "B": [100.0] * 30}


def _thesis(z_now, z_prev, monkeypatch):
    """build_pair_thesis reads the ratio z twice: this bar, then the bar before."""
    monkeypatch.setattr(scoring, "ratio_zscore",
                        lambda ca, cb, lookback: ((z_now if len(ca) == 30 else z_prev), 1.0, 1.0, 0.01))
    return scoring.build_pair_thesis(PAIR, CLOSES, {"A": [], "B": []}, None, CFG)


def test_a_ratio_still_stretching_is_not_a_reversion(monkeypatch):
    assert _thesis(3.0, 2.5, monkeypatch) is None            # |z| grew since the last bar
    assert _thesis(-3.0, -2.5, monkeypatch) is None          # same on the other side


def test_a_ratio_that_has_started_to_turn_is(monkeypatch):
    th = _thesis(3.5, 4.0, monkeypatch)                      # past zStrong, and coming back
    assert th and th["direction"] in ("LONG", "SHORT")
    assert "ratio_turning" not in " ".join(th["reasons"])     # the gate is not also a point
    assert th["score"] == 4                                  # 2 base + 2 z_extreme, no turning bonus


def test_one_signal_per_reversion_not_per_scan():
    rt = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
    scanner = next(s for s in rt["scanners"] if s["name"].endswith("_signals"))
    assert scanner["inputs"]["recentSignalTtlSeconds"] == 21600
    assert scanner["inputs"]["recentSignalTtlSeconds"] > scanner["interval_seconds"]
