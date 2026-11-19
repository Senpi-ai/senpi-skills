"""Chipmunk runs squirrel's dip-ladder engine, byte for byte.

Each rung's `scanners/scan.py` and `scanners/scoring.py` are copies of strategies/squirrel/rung1's (the
canonical copy; squirrel's own test_engine_parity.py pins its rung2 and rung3). Everything that makes
chipmunk chipmunk — the 40-week trend gate on its buys — is an INPUT (`regimeWeeks: 40`) in each rung's
runtime.yaml, so a fix to the engine lands in one place and is re-vendored deliberately; a drifted copy
cannot quietly make the conservative ladder trade differently from the one it is described against.

Run: python3 -B -m pytest strategies/chipmunk/tests -q
"""
import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STRATEGIES = os.path.abspath(os.path.join(HERE, "..", ".."))
FILES = ("scan.py", "scoring.py")


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_the_engine_copies_do_not_drift():
    for rung in ("rung1", "rung2", "rung3"):
        for name in FILES:
            ours = os.path.join(STRATEGIES, "chipmunk", rung, "scanners", name)
            theirs = os.path.join(STRATEGIES, "squirrel", "rung1", "scanners", name)
            assert os.path.exists(theirs), f"squirrel's {name} is missing — chipmunk vendors it"
            assert _sha(ours) == _sha(theirs), (
                f"{name} DRIFTED between strategies/squirrel/rung1 and strategies/chipmunk/{rung} — re-vendor it "
                f"byte-identically (cp strategies/squirrel/rung1/scanners/{name} strategies/chipmunk/{rung}/scanners/). "
                f"Chipmunk differs from squirrel only through runtime.yaml inputs, never through the engine.")


if __name__ == "__main__":
    test_the_engine_copies_do_not_drift()
    print("ENGINE PARITY OK")
