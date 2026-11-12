"""The engine here must stay byte-identical to signals-hunter's (strategies/signals-hunter/main/scanners/).

The detector library IS this strategy. A vendored copy that is allowed to drift stops being the
thing whose signals the user was reading — quietly, and in the direction of whatever was convenient
at the time. signals-hunter holds the reference copy since senpi-signals 3.0 moved to the backend and no longer
carries the scripts.

If a detector is fixed in signals-hunter, re-vendor: copy the file across and bump this package. If the
strategy needs behaviour the feed does not have, it belongs in scan.py, never in these three files.

Run: python3 -m pytest strategies/puffin/tests -q
"""
import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))
VENDORED = os.path.join(HERE, "..", "main", "scanners")
SOURCE = os.path.join(HERE, "..", "..", "signals-hunter", "main", "scanners")
ENGINE = ("sweep.py", "score.py", "smartmoney.py")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_every_engine_file_is_byte_identical_to_signals_hunters():
    drift = []
    for name in ENGINE:
        a, b = os.path.join(SOURCE, name), os.path.join(VENDORED, name)
        assert os.path.isfile(a), f"{name} is gone from signals-hunter — re-point this test"
        assert os.path.isfile(b), f"{name} is missing from the package"
        if _sha(a) != _sha(b):
            drift.append(name)
    assert not drift, (
        f"vendored engine drifted from signals-hunter: {drift}. Re-vendor (copy across and "
        f"bump this package) — do not edit the copy. Strategy-only behaviour belongs in scan.py.")


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
