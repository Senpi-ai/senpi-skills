"""mustang-sp500 runs the Mustang engine, byte for byte.

`scanners/scan.py` and `scanners/scoring.py` are copies of strategies/mustang's. Everything that makes
mustang-sp500 differ from Mustang is an INPUT in main/runtime.yaml (and its exit ladder), so a fix to the
engine lands in one place and is re-vendored here deliberately; a drifted copy cannot quietly change
how this variant trades. The copies are pinned byte-for-byte, the way ibex pins moose's.

Run: python3 -B -m pytest strategies/mustang-sp500/tests -q
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
    for name in FILES:
        ours = os.path.join(STRATEGIES, "mustang-sp500", "main", "scanners", name)
        theirs = os.path.join(STRATEGIES, "mustang", "main", "scanners", name)
        assert os.path.exists(theirs), f"mustang's {name} is missing — mustang-sp500 vendors it"
        assert _sha(ours) == _sha(theirs), (
            f"{name} DRIFTED between strategies/mustang and strategies/mustang-sp500 — re-vendor it byte-identically "
            f"(cp strategies/mustang/main/scanners/{name} strategies/mustang-sp500/main/scanners/).")


if __name__ == "__main__":
    test_the_engine_copies_do_not_drift()
    print("ENGINE PARITY OK")
