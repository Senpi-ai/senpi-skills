"""gannet-duo runs the Gannet engine, byte for byte.

`scanners/scan.py` and `scanners/scoring.py` are copies of strategies/gannet's. Everything that makes
gannet-duo differ from Gannet is an INPUT in main/runtime.yaml (and its exit ladder), so a fix to the
engine lands in one place and is re-vendored here deliberately; a drifted copy cannot quietly change
how this variant trades. The copies are pinned byte-for-byte, the way ibex pins moose's.

Run: python3 -B -m pytest strategies/gannet-duo/tests -q
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
        ours = os.path.join(STRATEGIES, "gannet-duo", "main", "scanners", name)
        theirs = os.path.join(STRATEGIES, "gannet", "main", "scanners", name)
        assert os.path.exists(theirs), f"gannet's {name} is missing — gannet-duo vendors it"
        assert _sha(ours) == _sha(theirs), (
            f"{name} DRIFTED between strategies/gannet and strategies/gannet-duo — re-vendor it byte-identically "
            f"(cp strategies/gannet/main/scanners/{name} strategies/gannet-duo/main/scanners/).")


if __name__ == "__main__":
    test_the_engine_copies_do_not_drift()
    print("ENGINE PARITY OK")
