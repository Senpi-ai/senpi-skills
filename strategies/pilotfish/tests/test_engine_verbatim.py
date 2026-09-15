"""Pilotfish's engine IS Phalanx's. scanners/scoring.py is byte-identical to
strategies/phalanx/main/scanners/scoring.py — the cohort one-sidedness, the net-headcount delta
and the hit-rate ledger are one implementation, not two. A fix that lands in phalanx/ must land
here too — this test is what says so (same pattern as athena/tests/test_legs_verbatim.py)."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STRATEGIES = os.path.join(HERE, "..", "..")
SRC = os.path.join(STRATEGIES, "phalanx", "main", "scanners", "scoring.py")
DST = os.path.join(STRATEGIES, "pilotfish", "main", "scanners", "scoring.py")


def test_scoring_is_byte_identical_to_phalanx():
    with open(SRC, "rb") as a, open(DST, "rb") as b:
        assert a.read() == b.read(), "pilotfish/main/scanners/scoring.py drifted from phalanx"
