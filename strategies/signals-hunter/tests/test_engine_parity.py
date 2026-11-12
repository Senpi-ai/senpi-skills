"""This package holds the reference copy of the strategy-side detector engine.

senpi-signals 3.0 reads Senpi Signals from the backend (signals-service, agent-bridge, a Go port of
these same files) and no longer carries the scripts. signals-hunter's copy is now the one the other
strategies that trade this engine (puffin, puffin-duo) are held byte-identical to.

If a detector is fixed, fix it here, copy it to puffin and puffin-duo, and bump all three. If the
strategy needs behaviour the engine does not have, it belongs in scan.py, never in these three files.

Run: python3 -m pytest strategies/signals-hunter/tests -q
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
VENDORED = os.path.join(HERE, "..", "main", "scanners")
ENGINE = ("sweep.py", "score.py", "smartmoney.py")


def test_the_reference_engine_is_here():
    for name in ENGINE:
        assert os.path.isfile(os.path.join(VENDORED, name)), f"{name} is missing from the package"


def test_the_scanner_owns_no_copy_of_the_detectors():
    """scan.py must CALL the engine, not reimplement it — the failure mode that made the earlier
    hand-written version diverge from the feed it claimed to trade."""
    with open(os.path.join(VENDORED, "scan.py"), encoding="utf-8") as f:
        body = f.read()
    assert "import score" in body and "import sweep" in body
    for owned_by_the_engine in ("def detect_from_metrics", "def trade_score", "def credibility",
                                "def coverage", "def rank("):
        assert owned_by_the_engine not in body, (
            f"scan.py redefines {owned_by_the_engine!r} — that lives in the engine")
