"""Whalehunter's engine IS Phalanx's. Both sleeves' scanners/scoring.py are byte-identical to
strategies/phalanx/main/scanners/scoring.py — the cohort one-sidedness, the net-headcount delta and
the hit-rate ledger are one implementation, not three. A fix that lands in phalanx/ must land here
too — this test is what says so (same pattern as athena/tests/test_legs_verbatim.py). The two sleeves
share scan.py verbatim as well (only the `direction` input differs); that is pinned too."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STRATEGIES = os.path.join(HERE, "..", "..")
SRC = os.path.join(STRATEGIES, "phalanx", "main", "scanners", "scoring.py")
SLEEVES = ("long", "short")


def _bytes(*parts):
    with open(os.path.join(STRATEGIES, "whalehunter", *parts), "rb") as f:
        return f.read()


def test_scoring_is_byte_identical_to_phalanx_in_both_sleeves():
    with open(SRC, "rb") as f:
        src = f.read()
    for sleeve in SLEEVES:
        assert _bytes(sleeve, "scanners", "scoring.py") == src, \
            f"whalehunter/{sleeve}/scanners/scoring.py drifted from phalanx"


def test_the_sleeves_share_scan_py_verbatim():
    assert _bytes("long", "scanners", "scan.py") == _bytes("short", "scanners", "scan.py"), \
        "whalehunter/long and /short scan.py drifted apart — only the `direction` input may differ"
