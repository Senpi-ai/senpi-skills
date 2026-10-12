"""Ibex runs the Moose engine, byte for byte.

`scanners/scan.py` and `scanners/scoring.py` are copies of strategies/moose's. Everything that
makes Ibex Ibex — the market, the breakout trigger as the default, the channel length, one slot,
the sizing — is an INPUT in main/runtime.yaml, which is the point: a fix to the engine lands in one
place and is re-vendored here deliberately, and a drifted copy cannot quietly change how gold is
traded. The copies are pinned byte-for-byte, the way squid pins its blacklist client.

Run: python3 -B -m pytest strategies/ibex/tests -q
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
        ours = os.path.join(STRATEGIES, "ibex", "main", "scanners", name)
        theirs = os.path.join(STRATEGIES, "moose", "main", "scanners", name)
        assert os.path.exists(theirs), f"moose's {name} is missing — ibex vendors it"
        assert _sha(ours) == _sha(theirs), (
            f"{name} DRIFTED between strategies/moose and strategies/ibex — re-vendor it byte-identically "
            f"(cp strategies/moose/main/scanners/{name} strategies/ibex/main/scanners/). Ibex differs from "
            f"moose only through runtime.yaml inputs, never through the engine.")


if __name__ == "__main__":
    test_the_engine_copies_do_not_drift()
    print("ENGINE PARITY OK")
